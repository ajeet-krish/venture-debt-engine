"""Amortization schedules for the venture debt engine (phase 2).

Phase 2 implements exactly two structures: bullet and io_then_straight_line
(architecture section P2.4, spec section 10.3). Any other structure raises
NotImplementedError; RBF and bridge land in phase 3.

Interest accrues on the opening balance of each period and is quantized once
to cents with ROUND_HALF_UP in MONEY_CONTEXT, the F1 step 10 basis. Principal
settles at the payment date: bullet repays the full facility in the final
period, IO-then-straight-line pays interest only for the first io_months
periods then allocate_cents over the remaining periods in order (early
periods receive the extra cents). Every build asserts that principal sums to
the facility, the final closing balance is 0, closing balances never go
negative, and payment dates are strictly increasing.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from vdebt.dates import payment_dates, year_fraction_act365
from vdebt.money import MONEY_CONTEXT, allocate_cents, quantize

# Phase 2 structures; RBF and bridge are phase 3 (architecture section P2.4).
_SUPPORTED_STRUCTURES: frozenset[str] = frozenset(
    {"bullet", "io_then_straight_line"}
)


@dataclass(frozen=True)
class ScheduleRow:
    """One payment period, all money in integer cents (architecture P2.4)."""

    period: int
    payment_date: date
    opening_cents: int
    interest_cents: int
    principal_cents: int
    closing_cents: int


@dataclass(frozen=True)
class Schedule:
    """A full amortization schedule: rows plus exact integer totals."""

    structure: str
    facility_cents: int
    close: date
    all_in_rate: Decimal
    rows: tuple[ScheduleRow, ...]
    total_interest_cents: int
    total_principal_cents: int


def build_schedule(
    structure: str,
    facility_cents: int,
    close: date,
    term_months: int,
    io_months: int,
    all_in_rate: Decimal,
) -> Schedule:
    """Build the canonical schedule for a supported structure.

    Interest per period is quantized once (ROUND_HALF_UP, MONEY_CONTEXT) from
    all_in_rate / 100 * opening * ACT/365 year fraction of the actual span,
    where the span runs from the previous payment date (close for period 1)
    to this payment date, and the product is taken on the opening balance
    before principal settles. Payment dates come from dates.payment_dates.
    Raises NotImplementedError for structures outside phase 2 and ValueError
    when a build-time assertion fails.
    """
    if structure not in _SUPPORTED_STRUCTURES:
        raise NotImplementedError(
            f"structure {structure!r} is not implemented in phase 2; "
            "supported structures: bullet, io_then_straight_line"
        )
    dates = payment_dates(close, term_months)
    if not dates:
        raise ValueError(f"term_months must be >= 1, got {term_months}")
    if structure == "bullet":
        principal_plan = [0] * term_months
        principal_plan[term_months - 1] = facility_cents
    else:
        if isinstance(io_months, float) or not isinstance(io_months, int):
            raise TypeError(
                f"io_months must be an int, got {type(io_months).__name__}"
            )
        if io_months < 0:
            raise ValueError(f"io_months must be >= 0, got {io_months}")
        amortizing_periods = term_months - io_months
        if amortizing_periods < 1:
            raise ValueError(
                f"io_months must be < term_months ({term_months}), got {io_months}"
            )
        principal_plan = [0] * io_months + allocate_cents(
            facility_cents, amortizing_periods
        )
    rate_fraction = MONEY_CONTEXT.divide(all_in_rate, Decimal(100))
    rows: list[ScheduleRow] = []
    opening_cents = facility_cents
    previous_date = close
    total_interest_cents = 0
    total_principal_cents = 0
    for period, payment_date in enumerate(dates, start=1):
        if payment_date <= previous_date:
            raise ValueError(
                f"payment dates must be strictly increasing: period {period} "
                f"payment {payment_date.isoformat()} does not follow "
                f"{previous_date.isoformat()}"
            )
        year_fraction = year_fraction_act365(previous_date, payment_date)
        raw_interest = MONEY_CONTEXT.multiply(
            MONEY_CONTEXT.multiply(rate_fraction, Decimal(opening_cents)),
            year_fraction,
        )
        interest_cents = int(quantize(raw_interest, 0))
        principal_cents = principal_plan[period - 1]
        closing_cents = opening_cents - principal_cents
        if closing_cents < 0:
            raise ValueError(
                f"closing balance went negative at period {period}: opening "
                f"{opening_cents} minus principal {principal_cents} is "
                f"{closing_cents}"
            )
        rows.append(
            ScheduleRow(
                period=period,
                payment_date=payment_date,
                opening_cents=opening_cents,
                interest_cents=interest_cents,
                principal_cents=principal_cents,
                closing_cents=closing_cents,
            )
        )
        total_interest_cents += interest_cents
        total_principal_cents += principal_cents
        opening_cents = closing_cents
        previous_date = payment_date
    if total_principal_cents != facility_cents:
        raise ValueError(
            f"sum of principal {total_principal_cents} does not equal "
            f"facility {facility_cents}"
        )
    if rows[-1].closing_cents != 0:
        raise ValueError(
            f"final closing balance must be 0, got {rows[-1].closing_cents}"
        )
    return Schedule(
        structure=structure,
        facility_cents=facility_cents,
        close=close,
        all_in_rate=all_in_rate,
        rows=tuple(rows),
        total_interest_cents=total_interest_cents,
        total_principal_cents=total_principal_cents,
    )


def closing_fee_cents(facility_cents: int, closing_fee_pct: Decimal) -> int:
    """Return the closing fee in integer cents: facility * pct / 100.

    The product is quantized once to cents with ROUND_HALF_UP in MONEY_CONTEXT
    (F2 expects 1,000,000 cents at 1.00 percent of 100,000,000).
    """
    raw_fee = MONEY_CONTEXT.divide(
        MONEY_CONTEXT.multiply(Decimal(facility_cents), closing_fee_pct),
        Decimal(100),
    )
    return int(quantize(raw_fee, 0))


def moic(
    total_interest_cents: int,
    total_principal_cents: int,
    warrant_value_cents: int,
    facility_cents: int,
) -> Decimal:
    """Return MOIC per spec section 5, quantized once to scale 2.

    Numerator is interest plus principal plus warrant value; the closing fee
    is excluded (the recorded phase 1 decision). Warrant value is 0 in phase
    2; the warrant model is phase 3. The ratio is divided in MONEY_CONTEXT
    and quantized once with ROUND_HALF_UP to multiple scale 2.
    """
    numerator = Decimal(
        total_interest_cents + total_principal_cents + warrant_value_cents
    )
    ratio = MONEY_CONTEXT.divide(numerator, Decimal(facility_cents))
    return quantize(ratio, 2)
