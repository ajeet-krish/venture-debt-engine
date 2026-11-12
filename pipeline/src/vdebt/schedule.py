"""Amortization schedules for the venture debt engine.

build_schedule implements exactly two amortizing structures: bullet and
io_then_straight_line (architecture section P2.4, spec section 10.3). Any
other structure raises NotImplementedError pointing at the dedicated
builders: build_rbf_schedule for the revenue based financing cap schedule
(architecture P3.1) and build_bridge_schedule for the bridge (P3.2), which
returns the same amortizing Schedule with a single repayment row at
maturity. RBF rows carry revenue, remittance, and cumulative cents only:
fixture F4 has no interest term, so fabricated zero interest would violate
the not_applicable principle.

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

from vdebt.dates import add_months, payment_dates, roll_forward, year_fraction_act365
from vdebt.money import MONEY_CONTEXT, allocate_cents, quantize

# Structures build_schedule owns (architecture section P2.4); rbf_capped and
# bridge come from build_rbf_schedule and build_bridge_schedule (P3.1, P3.2).
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


@dataclass(frozen=True)
class RbfRow:
    """One RBF remittance period (architecture P3.1).

    Carries revenue, remittance, and cumulative cents only: F4 has no
    interest term, so no interest, principal, or balance keys appear.
    """

    period: int
    payment_date: date
    revenue_cents: int
    remittance_cents: int
    cumulative_cents: int


@dataclass(frozen=True)
class RbfSchedule:
    """RBF schedule with cap mechanics and a status envelope (P3.1).

    close is the facility close date, the date the lender funds the advance
    (vdebt.returns.lender_cash_flows dates the close flow there, mirroring
    Schedule.close).
    """

    close: date
    advance_cents: int
    cap_cents: int
    remittance_rate_pct: Decimal
    cap_multiple: Decimal
    horizon: int
    rows: tuple[RbfRow, ...]
    final_cumulative_cents: int
    residual_cents: int
    status: str


def _accrued_interest_cents(
    rate_fraction: Decimal,
    opening_cents: int,
    start: date,
    end: date,
) -> int:
    """Quantize simple interest once: rate * opening * ACT/365 span.

    The single ROUND_HALF_UP quantization at the cents boundary in
    MONEY_CONTEXT (the F1 step 10 basis), shared unchanged by build_schedule
    and build_bridge_schedule: one period of accrual on the opening balance
    is the same algebra in both.
    """
    raw_interest = MONEY_CONTEXT.multiply(
        MONEY_CONTEXT.multiply(rate_fraction, Decimal(opening_cents)),
        year_fraction_act365(start, end),
    )
    return int(quantize(raw_interest, 0))


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
    Raises NotImplementedError for structures build_schedule does not own,
    with the message pointing at build_rbf_schedule and
    build_bridge_schedule, and ValueError when a build-time assertion fails.
    """
    if structure not in _SUPPORTED_STRUCTURES:
        raise NotImplementedError(
            f"structure {structure!r} is not implemented in build_schedule; "
            "RBF schedules come from build_rbf_schedule and bridge "
            "schedules come from build_bridge_schedule; supported "
            "amortizing structures: bullet, io_then_straight_line"
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
        interest_cents = _accrued_interest_cents(
            rate_fraction, opening_cents, previous_date, payment_date
        )
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


def build_rbf_schedule(
    advance_cents: int,
    close: date,
    horizon: int,
    remittance_rate_pct: Decimal,
    cap_multiple: Decimal,
    revenue_cents: tuple[int, ...],
) -> RbfSchedule:
    """Build the RBF remittance schedule with a repayment cap (P3.1, F4).

    The cap is quantized once (scale 0, ROUND_HALF_UP, MONEY_CONTEXT) from
    cap_multiple times advance_cents (F4 step 2: 1.50 x 60,000,000 =
    90,000,000). Payment dates come from dates.payment_dates(close, horizon).
    Each period computes nominal = quantize(remittance_rate_pct / 100 times
    revenue_t, 0), remits min(nominal, the residual still owed to the cap),
    and accumulates it. The row list stops at the first period whose
    cumulative reaches the cap, that row included, because remittances stop
    at the cap. Status is "feasible" when the cap is reached inside the
    horizon (residual 0) and "shortfall" when the horizon ends first, with
    residual_cents equal to the remaining gap. Raises ValueError when a
    build-time assertion fails, including a revenue tuple whose length
    differs from horizon.
    """
    if len(revenue_cents) != horizon:
        raise ValueError(
            f"revenue_cents length {len(revenue_cents)} does not equal "
            f"horizon {horizon}"
        )
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    cap_cents = int(
        quantize(MONEY_CONTEXT.multiply(cap_multiple, Decimal(advance_cents)), 0)
    )
    dates = payment_dates(close, horizon)
    rate_fraction = MONEY_CONTEXT.divide(remittance_rate_pct, Decimal(100))
    rows: list[RbfRow] = []
    cumulative_cents = 0
    previous_date = close
    for period, payment_date in enumerate(dates, start=1):
        if payment_date <= previous_date:
            raise ValueError(
                f"payment dates must be strictly increasing: period {period} "
                f"payment {payment_date.isoformat()} does not follow "
                f"{previous_date.isoformat()}"
            )
        revenue = revenue_cents[period - 1]
        nominal_cents = int(
            quantize(MONEY_CONTEXT.multiply(rate_fraction, Decimal(revenue)), 0)
        )
        residual_before_cents = cap_cents - cumulative_cents
        remittance_cents = min(nominal_cents, residual_before_cents)
        if remittance_cents < 0 or remittance_cents > residual_before_cents:
            raise ValueError(
                f"remittance {remittance_cents} at period {period} is "
                f"outside [0, {residual_before_cents}]"
            )
        cumulative_cents += remittance_cents
        if cumulative_cents > cap_cents:
            raise ValueError(
                f"cumulative remittance {cumulative_cents} at period "
                f"{period} exceeds cap {cap_cents}"
            )
        rows.append(
            RbfRow(
                period=period,
                payment_date=payment_date,
                revenue_cents=revenue,
                remittance_cents=remittance_cents,
                cumulative_cents=cumulative_cents,
            )
        )
        previous_date = payment_date
        if cumulative_cents == cap_cents:
            break
    final_cumulative_cents = sum(row.remittance_cents for row in rows)
    if final_cumulative_cents != cumulative_cents:
        raise ValueError(
            f"sum of remittances {final_cumulative_cents} does not equal "
            f"running cumulative {cumulative_cents}"
        )
    for index, row in enumerate(rows):
        if row.cumulative_cents == cap_cents and index != len(rows) - 1:
            raise ValueError(
                f"cap reached at period {row.period} but later rows follow; "
                "remittances must stop at the cap"
            )
    residual_cents = cap_cents - final_cumulative_cents
    status = "feasible" if final_cumulative_cents == cap_cents else "shortfall"
    return RbfSchedule(
        close=close,
        advance_cents=advance_cents,
        cap_cents=cap_cents,
        remittance_rate_pct=remittance_rate_pct,
        cap_multiple=cap_multiple,
        horizon=horizon,
        rows=tuple(rows),
        final_cumulative_cents=final_cumulative_cents,
        residual_cents=residual_cents,
        status=status,
    )


def build_bridge_schedule(
    facility_cents: int,
    close: date,
    tenor_months: int,
    all_in_rate: Decimal,
) -> Schedule:
    """Build the bridge schedule: simple interest, one payment at maturity.

    Maturity is roll_forward(add_months(close, tenor_months)) (F5 step 1:
    2026-02-27 plus 3 months is Wednesday 2026-05-27, unchanged). Simple
    interest accrues ACT/365 on the whole span and is quantized once
    (ROUND_HALF_UP, MONEY_CONTEXT) as all_in_rate / 100 times facility times
    the year fraction (F5 step 3: 12.0000 percent over 89 days on a
    50,000,000 cent facility gives 1,463,014 cents). Principal repays with
    the accrued interest in a single period 1 row with closing balance 0
    (F5 step 4: total repayment 51,463,014 cents). Raises ValueError when
    tenor_months is below 1.
    """
    maturity = roll_forward(add_months(close, tenor_months))
    if tenor_months < 1:
        raise ValueError(f"tenor_months must be >= 1, got {tenor_months}")
    rate_fraction = MONEY_CONTEXT.divide(all_in_rate, Decimal(100))
    interest_cents = _accrued_interest_cents(
        rate_fraction, facility_cents, close, maturity
    )
    row = ScheduleRow(
        period=1,
        payment_date=maturity,
        opening_cents=facility_cents,
        interest_cents=interest_cents,
        principal_cents=facility_cents,
        closing_cents=0,
    )
    return Schedule(
        structure="bridge",
        facility_cents=facility_cents,
        close=close,
        all_in_rate=all_in_rate,
        rows=(row,),
        total_interest_cents=interest_cents,
        total_principal_cents=facility_cents,
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
