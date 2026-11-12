"""Lender returns analytics: warrant value, dated cash flows, IRR, and MOIC.

Architecture P3.3 and P3.4 with the section 13 IRR solver pins. Pure
computation: dataclasses in, dataclasses out. Envelope assembly belongs to
build.py, the single envelope owner. Every Decimal operation passes
MONEY_CONTEXT explicitly, money stays in integer cents, and each published
metric quantizes exactly once at its boundary with ROUND_HALF_UP.

Warrant (P3.4, a stated assumption never a market quote):
warrant_value_cents = coverage_pct / 100 x facility_cents x
valuation_factor, taken as one MONEY_CONTEXT product chain and quantized
exactly once at the integer cent boundary (F6 step 24: 0.02 x 100,000,000 x
0.15 = 300,000.0000 cents becomes 300,000 cents). The valuation factor is an
explicit parameter so phase 4 downside passes a downscaled factor without
reshaping this interface. Zero coverage returns 0, which the caller
publishes with the "not_granted" companion (section 6.4).

Dated lender cash flows (P3.3): the close flow is closing fee minus the
funded amount for all four structures (F6 step 23: 1,000,000 minus
100,000,000 = -99,000,000 dated at close); each schedule row adds its
receipt on its payment date, interest plus principal for amortizing and
bridge rows (a bridge is a single-row Schedule) and the whole remittance for
RBF rows (a royalty structure carries no interest split, so the remittance
is the receipt); the warrant lands on warrant_date, the final payment date
under cfg.warrant.placement, so the final payment date carries interest plus
principal plus warrant as one flow (F6 step 25). Flows sharing a date
aggregate into one signed flow, dates whose aggregate is exactly 0 move no
money and are omitted, and the tuple is ascending by date. The schedule is
read structurally (close, funded amount, rows), so Schedule and RbfSchedule
both fit without an import cycle.

IRR (section 13): solve sum_i CF_i x (1 + r) raised to power (-yf_i) = 0
where yf_i is the ACT/365 year fraction from close to flow date i. Decimal
bisection on r over [bracket_low, bracket_high] from SolverParams (-0.99 to
10.00 in scenario.toml [solver.irr]) with midpoint (lo + hi) / 2 in
MONEY_CONTEXT and sign-only NPV decisions, terminating when hi minus lo
falls below tolerance_decimal ("0.000001", i.e. 0.0001 percent). Exceeding
max_iterations (200) raises vdebt.sizing.SolverError printing the bracket: a
hard failure, never a silent fallback. When NPV at the two bracket ends
shares a sign there is no root in the bracket (an all-positive flow set, or
a lone close outflow from an RBF shortfall borrower with no warrant), and the
result publishes status no_solution with a reason and no irr_percent; a
degenerate all-zero flow set gets its own reason. The published irr_percent
is r x 100 quantized exactly once to percent_annual scale 4 (F6: base
11.7898, shocked 12.9080).

MOIC: (receipts_cents + warrant_value_cents) / funded_cents, divided in
MONEY_CONTEXT and quantized once to multiple scale 2 (F6 step 33:
103,609,589 / 100,000,000 = 1.03609589 becomes 1.04). receipts are interest
plus principal for amortizing and bridge schedules and total remittances for
RBF; the closing fee never enters the numerator (decisions entry "MOIC
numerator excludes closing fee"). schedule.moic stays the F2 to F5 fixture
surface and returns.moic is the general receipts-based form; a test asserts
both agree on identical inputs.

Rate-shock helpers: irr_delta_bp is shocked minus base IRR, scaled by 100
and quantized once to bp scale 1 (F6 step 31: 111.8), and
sensitivity_per_100bp divides that delta by shock_bp / 100 and quantizes once
to bp scale 1 (F6 step 32: 55.9 at a 200 bp shock).

Depends on the standard library plus vdebt.config, vdebt.dates, vdebt.money,
vdebt.rates, and vdebt.sizing. No network access anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from vdebt.config import SolverParams
from vdebt.dates import year_fraction_act365
from vdebt.money import MONEY_CONTEXT, quantize
from vdebt.rates import PERCENT_ANNUAL_SCALE
from vdebt.sizing import SolverError

if TYPE_CHECKING:
    # lender_cash_flows reads the schedule structurally (close, funded amount,
    # rows), so both schedule types are type-only imports and this module
    # never imports vdebt.schedule at runtime.
    from vdebt.schedule import RbfSchedule, Schedule

# Published IRR statuses (snapshot.STATUS_VALUES carries both).
STATUS_FEASIBLE: str = "feasible"
STATUS_NO_SOLUTION: str = "no_solution"

# Publish scales not owned by vdebt.rates: multiple and basis points.
_MULTIPLE_SCALE: int = 2
_BP_SCALE: int = 1

# Exact operands for the fixed multipliers and divisors.
_ONE_HUNDRED: Decimal = Decimal(100)
_TWO: Decimal = Decimal(2)

# no_solution reason for the degenerate flow set with no nonzero amount.
_REASON_ALL_ZERO: str = "all cash flows are zero, so no root exists"


@dataclass(frozen=True)
class DatedCashFlow:
    """One lender cash flow on one date, signed integer cents.

    Positive amounts are receipts to the lender; the close flow is negative
    because the lender funds the facility net of the fee received at funding.
    """

    flow_date: date
    amount_cents: int


@dataclass(frozen=True)
class IRRResult:
    """IRR outcome: feasible publishes irr_percent, no_solution publishes reason.

    status is STATUS_FEASIBLE or STATUS_NO_SOLUTION. irr_percent is
    percent_annual scale 4 when published and None otherwise. reason is
    non-None exactly when status is no_solution, the companion the payload
    matrix requires beside no_solution (section 6.4). iterations counts
    bisection steps and is 0 whenever the solve exits before the loop: an
    endpoint root, a bracket with no sign change, or an all-zero flow set.
    """

    status: str
    irr_percent: Decimal | None
    reason: str | None
    iterations: int


def _require_decimal(value: object, name: str) -> Decimal:
    """Validate a finite Decimal input, rejecting float loudly."""
    if isinstance(value, float):
        raise TypeError(f"{name} must not be a float; use Decimal")
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}")
    return value


def _require_cents(value: object, name: str) -> int:
    """Validate integer cents, rejecting bool and float loudly."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int of cents, got {type(value).__name__}")
    return value


def _sign(value: Decimal) -> int:
    """Return 1, 0, or -1 for the sign of value (sign-only decisions)."""
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _require_flows(cash_flows: tuple[DatedCashFlow, ...]) -> tuple[DatedCashFlow, ...]:
    """Validate that every element is a DatedCashFlow and return the sequence."""
    flows: list[DatedCashFlow] = []
    for flow in cash_flows:
        if not isinstance(flow, DatedCashFlow):
            raise TypeError(
                f"cash_flows elements must be DatedCashFlow, got {type(flow).__name__}"
            )
        flows.append(flow)
    return tuple(flows)


def _schedule_close(schedule: object) -> date:
    """Read the close date from a Schedule or RbfSchedule."""
    close = getattr(schedule, "close", None)
    if not isinstance(close, date):
        raise TypeError(
            f"schedule must expose a close date attribute, got "
            f"{type(schedule).__name__} without one"
        )
    return close


def _schedule_funded_cents(schedule: object) -> int:
    """Read the funded amount: facility_cents on Schedule, advance_cents on RbfSchedule."""
    for attribute in ("facility_cents", "advance_cents"):
        value = getattr(schedule, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    raise TypeError(
        "schedule must expose facility_cents (Schedule) or advance_cents "
        f"(RbfSchedule), got {type(schedule).__name__}"
    )


def _schedule_rows(schedule: object) -> tuple[object, ...]:
    """Read the payment rows from a Schedule or RbfSchedule."""
    rows = getattr(schedule, "rows", None)
    if not isinstance(rows, (tuple, list)):
        raise TypeError(f"schedule must expose rows, got {type(schedule).__name__}")
    return tuple(rows)


def _row_receipt(row: object) -> int:
    """Return one row's lender receipt in integer cents.

    An RbfRow carries remittance_cents and the whole remittance is the
    receipt (a royalty structure has no interest split); an amortizing or
    bridge ScheduleRow carries interest_cents plus principal_cents.
    """
    remittance = getattr(row, "remittance_cents", None)
    if remittance is not None:
        return _require_cents(remittance, "row.remittance_cents")
    interest = getattr(row, "interest_cents", None)
    principal = getattr(row, "principal_cents", None)
    return _require_cents(interest, "row.interest_cents") + _require_cents(
        principal, "row.principal_cents"
    )


def warrant_value_cents(
    coverage_pct: Decimal,
    facility_cents: int,
    valuation_factor: Decimal,
) -> int:
    """Return the warrant value in integer cents, quantized once (P3.4).

    coverage_pct / 100 x facility_cents x valuation_factor is taken as one
    MONEY_CONTEXT product chain and quantized exactly once at the integer
    cent boundary with ROUND_HALF_UP (F6 step 24: 0.02 x 100,000,000 x 0.15 =
    300,000.0000 cents becomes 300,000 cents). Zero coverage returns 0, which
    the caller publishes with the "not_granted" companion (section 6.4). The
    valuation factor is an explicit parameter so phase 4 downside passes a
    downscaled factor without reshaping this interface. The value is a stated
    assumption, never a market quote, on every surface that renders it.
    """
    coverage = _require_decimal(coverage_pct, "coverage_pct")
    factor = _require_decimal(valuation_factor, "valuation_factor")
    facility = _require_cents(facility_cents, "facility_cents")
    if coverage < 0:
        raise ValueError(f"coverage_pct must be >= 0, got {coverage}")
    if factor < 0:
        raise ValueError(f"valuation_factor must be >= 0, got {factor}")
    if facility < 0:
        raise ValueError(f"facility_cents must be >= 0, got {facility}")
    fraction = MONEY_CONTEXT.divide(coverage, _ONE_HUNDRED)
    raw = MONEY_CONTEXT.multiply(
        MONEY_CONTEXT.multiply(fraction, Decimal(facility)),
        factor,
    )
    return int(quantize(raw, 0))


def lender_cash_flows(
    schedule: Schedule | RbfSchedule,
    closing_fee_cents: int,
    warrant_cents: int,
    warrant_date: date,
) -> tuple[DatedCashFlow, ...]:
    """Build the dated lender flows for any schedule structure (P3.3).

    Close flow (all four structures): closing_fee_cents minus the funded
    amount (Schedule.facility_cents or RbfSchedule.advance_cents), dated at
    schedule.close (F6 step 23: 1,000,000 minus 100,000,000 = -99,000,000).
    Each row then adds one receipt on its payment date: interest plus
    principal for amortizing and bridge rows (a bridge is a single-row
    Schedule), remittance for RBF rows (no interest split, so the whole
    remittance is the receipt). warrant_cents lands on warrant_date, the
    final payment date under cfg.warrant.placement, so the final payment
    date carries interest plus principal plus warrant as one flow (F6 step
    25). Flows sharing a date aggregate into one signed flow, dates whose
    aggregate is exactly 0 are omitted because they move no money, and the
    tuple is ascending by date. The closing fee enters the close flow only:
    it never appears in a period receipt nor in the MOIC numerator.
    """
    fee = _require_cents(closing_fee_cents, "closing_fee_cents")
    warrant = _require_cents(warrant_cents, "warrant_cents")
    if not isinstance(warrant_date, date):
        raise TypeError(
            f"warrant_date must be a date, got {type(warrant_date).__name__}"
        )
    close = _schedule_close(schedule)
    funded = _schedule_funded_cents(schedule)
    totals: dict[date, int] = {close: fee - funded}
    for row in _schedule_rows(schedule):
        payment_date = getattr(row, "payment_date", None)
        if not isinstance(payment_date, date):
            raise TypeError(f"schedule rows must expose a payment_date, got {payment_date!r}")
        totals[payment_date] = totals.get(payment_date, 0) + _row_receipt(row)
    totals[warrant_date] = totals.get(warrant_date, 0) + warrant
    flows = [
        DatedCashFlow(flow_date=flow_date, amount_cents=amount)
        for flow_date, amount in sorted(totals.items())
        if amount != 0
    ]
    return tuple(flows)


def npv(
    cash_flows: tuple[DatedCashFlow, ...],
    close: date,
    rate: Decimal,
) -> Decimal:
    """Return the net present value of the flows at rate (section 13 form).

    Each term is amount x (1 + rate) raised to power (-yf), where yf is the
    ACT/365 year fraction from close to the flow date. The power and the
    accumulation run in MONEY_CONTEXT; the result is unquantized because it
    is an intermediate of the solver and of the publish bracket check. rate
    must be greater than -1 so the base stays positive; a smaller rate raises
    ValueError instead of a Decimal power failure.
    """
    flows = _require_flows(cash_flows)
    discount_rate = _require_decimal(rate, "rate")
    if not isinstance(close, date):
        raise TypeError(f"close must be a date, got {type(close).__name__}")
    if discount_rate <= -1:
        raise ValueError(f"rate must be greater than -1, got {discount_rate}")
    base = MONEY_CONTEXT.add(Decimal(1), discount_rate)
    total = Decimal(0)
    for flow in flows:
        year_fraction = year_fraction_act365(close, flow.flow_date)
        # Negation is exact in Decimal, so the exponent needs no context.
        discount = MONEY_CONTEXT.power(base, -year_fraction)
        total = MONEY_CONTEXT.add(
            total,
            MONEY_CONTEXT.multiply(Decimal(flow.amount_cents), discount),
        )
    return total


def _publish_irr(root: Decimal) -> Decimal:
    """Quantize root x 100 once to percent_annual scale 4 (section 13)."""
    percent = MONEY_CONTEXT.multiply(root, _ONE_HUNDRED)
    return quantize(percent, PERCENT_ANNUAL_SCALE)


def solve_irr(
    cash_flows: tuple[DatedCashFlow, ...],
    close: date,
    params: SolverParams,
) -> IRRResult:
    """Solve the dated lender IRR by Decimal bisection (section 13 pins).

    Bisect r on [params.bracket_low, params.bracket_high] (scenario.toml
    [solver.irr]: -0.99 to 10.00) with the midpoint (lo + hi) / 2 taken in
    MONEY_CONTEXT and sign-only NPV decisions: a midpoint whose NPV shares
    the sign of the low end raises the low end, otherwise it lowers the high
    end, so the bracket keeps a sign change at its ends. The loop terminates
    when hi minus lo falls below params.tolerance_decimal ("0.000001") and
    the answer is the final bracket center (lo + hi) / 2 (F6 step 27 basis:
    24 iterations to r = 0.117898202240467071533203125, published 11.7898).
    Exceeding params.max_iterations (200) raises vdebt.sizing.SolverError
    printing the bracket: a hard failure, never a silent fallback.

    no_solution: when NPV at the bracket ends shares a sign there is no root
    in the bracket (an all-positive flow set, or a lone close outflow), and
    an all-zero flow set gets its own reason. Both publish status
    STATUS_NO_SOLUTION with the reason companion and irr_percent None. An
    exact zero NPV at a bracket end publishes that end as the root.

    irr_percent is r x 100 quantized exactly once to percent_annual scale 4
    with ROUND_HALF_UP (F6 base 11.7898, shocked 12.9080).
    """
    flows = _require_flows(cash_flows)
    if not isinstance(params, SolverParams):
        raise TypeError(f"params must be a SolverParams, got {type(params).__name__}")
    if not isinstance(close, date):
        raise TypeError(f"close must be a date, got {type(close).__name__}")
    if params.bracket_low >= params.bracket_high:
        raise ValueError(
            f"bracket_low must be below bracket_high, got {params.bracket_low} "
            f"and {params.bracket_high}"
        )
    if all(flow.amount_cents == 0 for flow in flows):
        return IRRResult(
            status=STATUS_NO_SOLUTION,
            irr_percent=None,
            reason=_REASON_ALL_ZERO,
            iterations=0,
        )
    npv_low = npv(flows, close, params.bracket_low)
    npv_high = npv(flows, close, params.bracket_high)
    sign_low = _sign(npv_low)
    sign_high = _sign(npv_high)
    if sign_low == 0:
        return IRRResult(
            status=STATUS_FEASIBLE,
            irr_percent=_publish_irr(params.bracket_low),
            reason=None,
            iterations=0,
        )
    if sign_high == 0:
        return IRRResult(
            status=STATUS_FEASIBLE,
            irr_percent=_publish_irr(params.bracket_high),
            reason=None,
            iterations=0,
        )
    if sign_low == sign_high:
        return IRRResult(
            status=STATUS_NO_SOLUTION,
            irr_percent=None,
            reason=(
                "no sign change in NPV across bracket "
                f"[{params.bracket_low} {params.bracket_high}]"
            ),
            iterations=0,
        )
    low = params.bracket_low
    high = params.bracket_high
    iterations = 0
    while True:
        if MONEY_CONTEXT.subtract(high, low) < params.tolerance_decimal:
            break
        iterations += 1
        if iterations > params.max_iterations:
            raise SolverError(
                f"irr bisection exceeded {params.max_iterations} iterations; "
                f"bracket=[{low} {high}]"
            )
        mid = MONEY_CONTEXT.divide(MONEY_CONTEXT.add(low, high), _TWO)
        sign_mid = _sign(npv(flows, close, mid))
        if sign_mid == 0:
            low = mid
            high = mid
            break
        if sign_mid == sign_low:
            low = mid
        else:
            high = mid
    root = MONEY_CONTEXT.divide(MONEY_CONTEXT.add(low, high), _TWO)
    return IRRResult(
        status=STATUS_FEASIBLE,
        irr_percent=_publish_irr(root),
        reason=None,
        iterations=iterations,
    )


def moic(receipts_cents: int, warrant_value_cents: int, funded_cents: int) -> Decimal:
    """Return (receipts + warrant) / funded quantized once to multiple scale 2.

    receipts are interest plus principal for amortizing and bridge schedules
    and total remittances for RBF (the P3.3 receipts basis); the closing fee
    never enters the numerator (decisions entry "MOIC numerator excludes
    closing fee"). The division runs in MONEY_CONTEXT and the ratio
    quantizes exactly once with ROUND_HALF_UP to scale 2 (F6 step 33:
    103,609,589 / 100,000,000 = 1.03609589 becomes 1.04). funded_cents is
    the gross advance, never net of fee, and must be positive.
    schedule.moic stays the F2 to F5 fixture surface and a test asserts both
    forms agree on identical inputs.
    """
    receipts = _require_cents(receipts_cents, "receipts_cents")
    warrant = _require_cents(warrant_value_cents, "warrant_value_cents")
    funded = _require_cents(funded_cents, "funded_cents")
    if funded <= 0:
        raise ValueError(f"funded_cents must be > 0, got {funded}")
    ratio = MONEY_CONTEXT.divide(Decimal(receipts + warrant), Decimal(funded))
    return quantize(ratio, _MULTIPLE_SCALE)


def irr_delta_bp(base_percent: Decimal, shocked_percent: Decimal) -> Decimal:
    """Return shocked minus base IRR in bp, quantized once to scale 1.

    The difference is scaled by 100 in MONEY_CONTEXT and quantized exactly
    once to bp scale 1 (F6 step 31: 12.9080 minus 11.7898 = 1.1182 percent,
    x 100 = 111.82 bp, becomes 111.8 bp). Both inputs are published IRR
    values at percent_annual scale 4.
    """
    base = _require_decimal(base_percent, "base_percent")
    shocked = _require_decimal(shocked_percent, "shocked_percent")
    delta = MONEY_CONTEXT.subtract(shocked, base)
    scaled = MONEY_CONTEXT.multiply(delta, _ONE_HUNDRED)
    return quantize(scaled, _BP_SCALE)


def sensitivity_per_100bp(delta_bp: Decimal, shock_bp: Decimal) -> Decimal:
    """Return delta_bp / (shock_bp / 100) quantized once to scale 1.

    The divisor and division run in MONEY_CONTEXT and the ratio quantizes
    exactly once to bp scale 1 (F6 step 32: 111.8 / (200.0 / 100) = 55.9 at
    the 200 bp shock, so the per 100 bp sensitivity is delta / 2). shock_bp
    must not be 0: a zero shock has no defined sensitivity and raises
    ValueError instead of a Decimal division failure.
    """
    delta = _require_decimal(delta_bp, "delta_bp")
    shock = _require_decimal(shock_bp, "shock_bp")
    divisor = MONEY_CONTEXT.divide(shock, _ONE_HUNDRED)
    if divisor == 0:
        raise ValueError(f"shock_bp must not be 0, got {shock}")
    ratio = MONEY_CONTEXT.divide(delta, divisor)
    return quantize(ratio, _BP_SCALE)
