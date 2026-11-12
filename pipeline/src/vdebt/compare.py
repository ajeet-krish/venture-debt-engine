"""Structure comparison table on identical borrower inputs (architecture P3.5).

Spec section 10.6 pins exactly three rows: bridge, io_then_straight_line,
and rbf_capped. The drawn bullet structure is not a comparison row, so the
consistency check in the tests applies only when the drawn structure is
io_then_straight_line (a bullet borrower's drawn schedule has no matching
comparison row).

Plain dataclasses only, no envelopes: ComparisonRow and ComparisonTable
carry raw values and status strings. build.py assembles the comparison.json
envelopes later, keeping one envelope owner (architecture P3.5).

Every row shares one frozen SharedInputs block: close, base rate with its
rate source and source timestamp, all-in rate, the canonical sized facility,
the closing fee, and warrant coverage. The facility is never re-sized per
structure: sizing is canonical (one capacity per borrower, computed on the
io-then-straight-line serviceability schedule), so the three structures
differ in schedule and returns at that one capacity, the disclosed
serviceability proxy. binding_constraint repeats the sizing answer on all
three rows because capacity is a property of the borrower, not the
structure.

Per-structure cell semantics (architecture P3.5):
- bridge: term is the pinned tenor (3 months in scenario.toml), IO months
  not_applicable with the pinned reason, total interest from the bridge
  schedule, min DSCR = CFADS at the maturity period divided by the total
  repayment (the only period with debt service; a path that lacks that
  period, an all-zero CFADS path, or a zero repayment gives
  not_applicable with a reason). A pre-revenue borrower's all-zero path
  therefore shows not_applicable on the bridge row exactly like the io
  and RBF rows, never a fabricated 0.0000.
- io_then_straight_line: term = io + the pinned amortizing periods, IO
  months = borrower io_months, total interest from the canonical schedule,
  min DSCR equals sizing_result.min_dscr on the same schedule basis so the
  row ties back to the sizing answer.
- rbf_capped: term is the pinned horizon (24 months), IO months
  not_applicable with the pinned reason, total interest is the contractual
  zero 0 (the provenance string for that zero is build.py's envelope
  duty), total receipts = final cumulative remittances, min DSCR = CFADS /
  remittance over periods with positive remittance and not_applicable with
  a reason when no period remits (pre-revenue rows).

Returns per row: warrant = warrant_value_cents at cfg.warrant_valuation_
factor, warrant_date = the final payment date of that row's schedule, flows
from lender_cash_flows (closing fee at close, warrant at the final payment
date), IRR from solve_irr at cfg.solver, and MOIC from moic over receipts
(interest plus principal for the amortizing and bridge schedules, final
cumulative remittances for RBF).

Paths adapt to each row's horizon: a bridge-drawn borrower's path spans
only the pinned tenor, so the RBF row extends the tail with the generator's
own growth formula (the same extension build.py applies to the sizing CFADS
path; build.py's helper is private and will import this module, so the
three-line formula is restated here to avoid an import cycle), and an
amortizing-drawn path is sliced to the pinned RBF horizon.

Infeasible sizing still yields three rows of the same shape: facility,
closing fee, IRR, MOIC, min DSCR, total interest, and total receipts are
None with not_applicable statuses and the pinned reason (never fabricated
zeros), and the shared block still carries the shared inputs with a None
facility.

Depends on the standard library plus vdebt.config, vdebt.generator,
vdebt.money, vdebt.pricing, vdebt.rates, vdebt.returns, vdebt.schedule, and
vdebt.sizing. No network access anywhere in this module.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from vdebt.config import ScenarioConfig
from vdebt.generator import MONTHS_PER_YEAR, Borrower
from vdebt.money import MONEY_CONTEXT, quantize
from vdebt.pricing import Pricing
from vdebt.rates import RateSnapshot
from vdebt.returns import (
    lender_cash_flows,
    moic,
    solve_irr,
    warrant_value_cents,
)
from vdebt.schedule import (
    RbfSchedule,
    Schedule,
    build_bridge_schedule,
    build_rbf_schedule,
    build_schedule,
    closing_fee_cents,
)
from vdebt.sizing import SizingResult

# Published status vocabulary (snapshot.STATUS_VALUES carries all three).
STATUS_FEASIBLE: str = "feasible"
STATUS_NO_SOLUTION: str = "no_solution"
STATUS_NOT_APPLICABLE: str = "not_applicable"

# The three pinned comparison rows, in spec 10.6 order (architecture P3.5).
ROW_STRUCTURES: tuple[str, ...] = (
    "bridge",
    "io_then_straight_line",
    "rbf_capped",
)
STRUCTURE_BRIDGE: str = "bridge"
STRUCTURE_IO: str = "io_then_straight_line"
STRUCTURE_RBF: str = "rbf_capped"

# first_breach placeholder on every row: the covenant engine lands in
# phase 4, which replaces the leaf in place so the column shape never moves.
FIRST_BREACH_STATUS: str = "not_applicable"
FIRST_BREACH_REASON: str = "covenant engine lands in phase 4"

# io_then_straight_line is also the sizing structure (sizing._SIZING_STRUCTURE).
SIZING_STRUCTURE: str = "io_then_straight_line"

# Published DSCR scale: the sizing min_dscr is scale 4, so the bridge and
# RBF period ratios quantize once to the same scale at their metric boundary.
DSCR_SCALE: int = 4

# Cell-level not_applicable reasons (section 6.4 companions).
IO_BRIDGE_REASON: str = "bridge carries no interest-only period, single repayment at maturity"
IO_RBF_REASON: str = "RBF remittances begin in period 1, no interest-only period"
INFEASIBLE_REASON: str = "sizing infeasible so no facility exists to fund"
BRIDGE_SHORT_PATH_REASON: str = (
    "CFADS path ends before the bridge maturity period, so min DSCR is undefined"
)
BRIDGE_ZERO_SERVICE_REASON: str = (
    "bridge total repayment at maturity is zero, so min DSCR is undefined"
)
BRIDGE_PRE_REVENUE_REASON: str = (
    "pre-revenue borrower, no cash flow available for debt service"
)
RBF_NO_REMITTANCE_REASON: str = (
    "RBF schedule carries no period with a positive remittance, so min DSCR is undefined"
)
NO_SIZING_DSCR_REASON: str = (
    "sizing carries no min DSCR: pre-revenue borrower or no period with debt service"
)

# Bands every row's cells derive from, then the structure-specific extras.
_BASE_BANDS: tuple[str, ...] = (
    "facility_sizing",
    "closing_fee",
    "warrant_coverage",
)
IO_EXTRA_BAND: str = "interest_only"
RBF_EXTRA_BAND: str = "rbf_cap"

# Lender type to pricing band name; any other lender_type raises ValueError.
_PRICING_BANDS: dict[str, str] = {
    "bank": "pricing_bank",
    "non_bank": "pricing_nonbank",
}


@dataclass(frozen=True)
class SharedInputs:
    """Identical borrower inputs shared by the three comparison rows.

    close is the rate snapshot observation date, base_rate and all_in_rate
    are percent_annual scale 4, rate_source and rate_source_timestamp come
    from the committed rate snapshot (spec 10.6 acceptance: byte-identical
    across rows), facility_cents is the canonical sized facility and None
    when sizing is infeasible, closing_fee_cents is derived from that
    facility (None alongside it), and warrant_coverage is the borrower's
    drawn percent.
    """

    close: date
    base_rate: Decimal
    rate_source: str
    rate_source_timestamp: str
    all_in_rate: Decimal
    facility_cents: int | None
    closing_fee_cents: int | None
    warrant_coverage: Decimal


@dataclass(frozen=True)
class ComparisonRow:
    """One structure's comparison cells (spec 10.6 columns, raw values).

    irr_status is feasible, no_solution, or not_applicable; min_dscr_status
    is feasible or not_applicable; first_breach_status is always the
    not_applicable placeholder until phase 4. A status other than feasible
    carries its reason companion, and None numeric cells are never
    fabricated zeros (infeasible sizing publishes None with the pinned
    reason).
    """

    structure: str
    facility_cents: int | None
    all_in_rate: Decimal
    term_months: int
    io_months: int | None
    io_months_reason: str | None
    total_interest_cents: int | None
    closing_fee_cents: int | None
    irr_percent: Decimal | None
    irr_status: str
    irr_reason: str | None
    moic: Decimal | None
    min_dscr: Decimal | None
    min_dscr_status: str
    min_dscr_reason: str | None
    binding_constraint: str | None
    first_breach_status: str
    first_breach_reason: str
    warrant_coverage: Decimal
    total_receipts_cents: int | None
    band_names: tuple[str, ...]


@dataclass(frozen=True)
class ComparisonTable:
    """One borrower's comparison: shared inputs plus exactly three rows.

    rows holds bridge, io_then_straight_line, rbf_capped in that pinned
    order (ROW_STRUCTURES).
    """

    borrower_id: str
    shared: SharedInputs
    rows: tuple[ComparisonRow, ...]


def _pricing_band_name(borrower: Borrower) -> str:
    """Return the pricing band table name for the borrower's lender type."""
    band_name = _PRICING_BANDS.get(borrower.lender_type)
    if band_name is None:
        allowed = ", ".join(sorted(_PRICING_BANDS))
        raise ValueError(
            f"unknown lender_type {borrower.lender_type!r}; expected one of: {allowed}"
        )
    return band_name


def _row_band_names(borrower: Borrower, structure: str) -> tuple[str, ...]:
    """Return the bands one row's cells derive from (architecture P3.5).

    Every row carries facility sizing, its pricing band, closing fee, and
    warrant coverage; the IO row adds interest_only (the IO months cell)
    and the RBF row adds rbf_cap (the cap drives remittances and receipts).
    """
    names = (_pricing_band_name(borrower), *_BASE_BANDS)
    if structure == STRUCTURE_IO:
        return (*names, IO_EXTRA_BAND)
    if structure == STRUCTURE_RBF:
        return (*names, RBF_EXTRA_BAND)
    return names


def _shared_inputs(
    borrower: Borrower,
    pricing: Pricing,
    sizing_result: SizingResult,
    snapshot: RateSnapshot,
) -> SharedInputs:
    """Build the shared block once so all three rows carry identical values."""
    facility = sizing_result.facility_cents
    fee = (
        None
        if facility is None
        else closing_fee_cents(facility, borrower.closing_fee)
    )
    return SharedInputs(
        close=snapshot.observation_date,
        base_rate=pricing.base_rate,
        rate_source=snapshot.source,
        rate_source_timestamp=snapshot.source_timestamp,
        all_in_rate=pricing.all_in_rate,
        facility_cents=facility,
        closing_fee_cents=fee,
        warrant_coverage=borrower.warrant_coverage,
    )


def _grown_revenue_cents(borrower: Borrower, period: int) -> int:
    """Return the generator revenue formula at period, quantized once.

    ARR / 12 grown by the monthly growth rate per period (generator._paths
    and build.py's sizing path extension share this formula; it is restated
    here because build.py's helper is private and build.py imports this
    module). Pre-revenue borrowers have ARR 0, so the growth yields 0 cents.
    """
    base = MONEY_CONTEXT.divide(Decimal(borrower.arr_cents), Decimal(MONTHS_PER_YEAR))
    growth_fraction = MONEY_CONTEXT.divide(borrower.monthly_growth, Decimal(100))
    factor = MONEY_CONTEXT.add(Decimal(1), growth_fraction)
    grown = MONEY_CONTEXT.power(factor, Decimal(period - 1))
    return int(quantize(MONEY_CONTEXT.multiply(base, grown), 0))


def _horizon_revenue_path(borrower: Borrower, horizon: int) -> tuple[int, ...]:
    """Return the revenue path over exactly `horizon` periods.

    Slices the generator path when it is longer (amortizing borrowers draw
    io plus the amortizing periods and the RBF row runs the pinned horizon)
    and extends the tail with the generator growth formula when it is
    shorter (bridge borrowers draw only the pinned tenor).
    """
    path = borrower.revenue_path
    if len(path) == horizon:
        return path
    if len(path) > horizon:
        return path[:horizon]
    extended = list(path)
    for period in range(len(path) + 1, horizon + 1):
        extended.append(_grown_revenue_cents(borrower, period))
    return tuple(extended)


def _horizon_cfads_path(borrower: Borrower, horizon: int) -> tuple[int, ...]:
    """Return the CFADS path over exactly `horizon` periods.

    Slicing is exact because the generator derives CFADS from revenue. The
    extension applies the gross margin to the grown revenue with one
    quantize per period, the same rule generator._paths uses.
    """
    path = borrower.cfads_path
    if len(path) == horizon:
        return path
    if len(path) > horizon:
        return path[:horizon]
    extended = list(path)
    for period in range(len(path) + 1, horizon + 1):
        revenue = _grown_revenue_cents(borrower, period)
        cfads = int(
            quantize(
                MONEY_CONTEXT.divide(
                    MONEY_CONTEXT.multiply(Decimal(revenue), borrower.gross_margin),
                    Decimal(100),
                ),
                0,
            )
        )
        extended.append(cfads)
    return tuple(extended)


def _final_payment_date(schedule: Schedule | RbfSchedule) -> date:
    """Return the final row's payment date (the warrant dating anchor)."""
    return schedule.rows[-1].payment_date


def _schedule_receipts_cents(schedule: Schedule | RbfSchedule) -> int:
    """Return total lender receipts: interest + principal, or remittances.

    An RbfSchedule carries no interest or principal split, so its receipts
    are the final cumulative remittances; a Schedule's receipts are its
    interest plus principal totals (the P3.3 receipts basis).
    """
    final_cumulative = getattr(schedule, "final_cumulative_cents", None)
    if final_cumulative is not None:
        return final_cumulative
    return schedule.total_interest_cents + schedule.total_principal_cents


def _returns_cells(
    schedule: Schedule | RbfSchedule,
    receipts_cents: int,
    fee_cents: int,
    warrant_cents: int,
    close: date,
    cfg: ScenarioConfig,
) -> tuple[Decimal | None, str, str | None, Decimal]:
    """Return (irr_percent, irr_status, irr_reason, moic) for one structure.

    lender_cash_flows dates the close flow (fee minus facility) at close and
    the warrant at the schedule's final payment date; solve_irr runs the
    section 13 bisection at cfg.solver (feasible publishes the percent,
    no_solution publishes the reason); moic divides receipts plus warrant by
    the gross facility once at scale 2.
    """
    warrant_date = _final_payment_date(schedule)
    flows = lender_cash_flows(schedule, fee_cents, warrant_cents, warrant_date)
    irr = solve_irr(flows, close, cfg.solver)
    funded = getattr(schedule, "facility_cents", None)
    if funded is None:
        funded = schedule.advance_cents
    row_moic = moic(receipts_cents, warrant_cents, funded)
    return irr.irr_percent, irr.status, irr.reason, row_moic


def _row_term_months(structure: str, borrower: Borrower, cfg: ScenarioConfig) -> int:
    """Return one row's term in months.

    bridge uses the pinned tenor, rbf_capped the pinned horizon, and the IO
    row io_months plus the pinned amortizing periods (the canonical sizing
    term), independent of the borrower's drawn structure.
    """
    if structure == STRUCTURE_BRIDGE:
        return cfg.generator.bridge_tenor_months
    if structure == STRUCTURE_RBF:
        return cfg.generator.rbf_horizon_periods
    return borrower.io_months + cfg.generator.term_amortizing_periods


def _infeasible_row(
    structure: str,
    shared: SharedInputs,
    binding_constraint: str | None,
    borrower: Borrower,
    cfg: ScenarioConfig,
) -> ComparisonRow:
    """Build one row's shape when sizing is infeasible (P3.5).

    Facility, closing fee, IRR, MOIC, min DSCR, total interest, and total
    receipts are all None with not_applicable statuses and the pinned
    reason: no fabricated zeros when no facility exists to fund. Term and
    IO months stay structural (they are pinned or borrower inputs, not
    facility-dependent), and binding_constraint repeats the sizing answer.
    """
    io_months: int | None = None
    io_reason: str | None = None
    if structure == STRUCTURE_IO:
        io_months = borrower.io_months
    elif structure == STRUCTURE_BRIDGE:
        io_reason = IO_BRIDGE_REASON
    else:
        io_reason = IO_RBF_REASON
    return ComparisonRow(
        structure=structure,
        facility_cents=None,
        all_in_rate=shared.all_in_rate,
        term_months=_row_term_months(structure, borrower, cfg),
        io_months=io_months,
        io_months_reason=io_reason,
        total_interest_cents=None,
        closing_fee_cents=None,
        irr_percent=None,
        irr_status=STATUS_NOT_APPLICABLE,
        irr_reason=INFEASIBLE_REASON,
        moic=None,
        min_dscr=None,
        min_dscr_status=STATUS_NOT_APPLICABLE,
        min_dscr_reason=INFEASIBLE_REASON,
        binding_constraint=binding_constraint,
        first_breach_status=FIRST_BREACH_STATUS,
        first_breach_reason=FIRST_BREACH_REASON,
        warrant_coverage=shared.warrant_coverage,
        total_receipts_cents=None,
        band_names=_row_band_names(borrower, structure),
    )


def _bridge_row(
    borrower: Borrower,
    pricing: Pricing,
    sizing_result: SizingResult,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
    shared: SharedInputs,
) -> ComparisonRow:
    """Build the bridge row: single repayment at the pinned tenor.

    min DSCR = CFADS at the maturity period divided by the total repayment
    (the only period with debt service; zero-service periods are excluded
    from the minimum). An all-zero CFADS path (pre-revenue), a path that
    lacks the maturity period, or a zero repayment each give None with
    not_applicable and a reason, matching the io row's treatment of a
    borrower whose sizing skipped DSCR. The ratio quantizes once to scale
    4, the sizing min_dscr basis.
    """
    facility = sizing_result.facility_cents
    fee = shared.closing_fee_cents
    if facility is None or fee is None:
        raise ValueError(
            f"{borrower.borrower_id}: bridge row requires a feasible facility"
        )
    close = snapshot.observation_date
    tenor = cfg.generator.bridge_tenor_months
    schedule = build_bridge_schedule(facility, close, tenor, pricing.all_in_rate)
    index = tenor - 1
    if all(cents == 0 for cents in borrower.cfads_path):
        # Same condition the io row uses: an all-zero CFADS path means the
        # sizing predicate skipped DSCR, so the ratio is undefined and the
        # cell publishes not_applicable with a reason, never a 0.0000.
        min_dscr: Decimal | None = None
        min_status = STATUS_NOT_APPLICABLE
        min_reason: str | None = BRIDGE_PRE_REVENUE_REASON
    elif index >= len(borrower.cfads_path):
        min_dscr = None
        min_status = STATUS_NOT_APPLICABLE
        min_reason = BRIDGE_SHORT_PATH_REASON
    else:
        repayment_cents = (
            schedule.rows[0].interest_cents + schedule.rows[0].principal_cents
        )
        if repayment_cents == 0:
            min_dscr = None
            min_status = STATUS_NOT_APPLICABLE
            min_reason = BRIDGE_ZERO_SERVICE_REASON
        else:
            min_dscr = quantize(
                MONEY_CONTEXT.divide(
                    Decimal(borrower.cfads_path[index]), Decimal(repayment_cents)
                ),
                DSCR_SCALE,
            )
            min_status = STATUS_FEASIBLE
            min_reason = None
    warrant = warrant_value_cents(
        borrower.warrant_coverage, facility, cfg.warrant_valuation_factor
    )
    receipts = _schedule_receipts_cents(schedule)
    irr_percent, irr_status, irr_reason, row_moic = _returns_cells(
        schedule, receipts, fee, warrant, close, cfg
    )
    return ComparisonRow(
        structure=STRUCTURE_BRIDGE,
        facility_cents=facility,
        all_in_rate=pricing.all_in_rate,
        term_months=tenor,
        io_months=None,
        io_months_reason=IO_BRIDGE_REASON,
        total_interest_cents=schedule.total_interest_cents,
        closing_fee_cents=fee,
        irr_percent=irr_percent,
        irr_status=irr_status,
        irr_reason=irr_reason,
        moic=row_moic,
        min_dscr=min_dscr,
        min_dscr_status=min_status,
        min_dscr_reason=min_reason,
        binding_constraint=sizing_result.binding_constraint,
        first_breach_status=FIRST_BREACH_STATUS,
        first_breach_reason=FIRST_BREACH_REASON,
        warrant_coverage=borrower.warrant_coverage,
        total_receipts_cents=receipts,
        band_names=_row_band_names(borrower, STRUCTURE_BRIDGE),
    )


def _io_row(
    borrower: Borrower,
    pricing: Pricing,
    sizing_result: SizingResult,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
    shared: SharedInputs,
) -> ComparisonRow:
    """Build the io-then-straight-line row on the canonical schedule.

    The schedule rebuilds from scratch at the canonical facility with term
    io plus the pinned amortizing periods (the consistency test's row), and
    min_dscr repeats sizing_result.min_dscr on that same schedule basis so
    the row ties back to the sizing answer.
    """
    facility = sizing_result.facility_cents
    fee = shared.closing_fee_cents
    if facility is None or fee is None:
        raise ValueError(
            f"{borrower.borrower_id}: io row requires a feasible facility"
        )
    close = snapshot.observation_date
    term = borrower.io_months + cfg.generator.term_amortizing_periods
    schedule = build_schedule(
        SIZING_STRUCTURE, facility, close, term, borrower.io_months, pricing.all_in_rate
    )
    if sizing_result.min_dscr is None:
        min_status = STATUS_NOT_APPLICABLE
        min_reason: str | None = NO_SIZING_DSCR_REASON
    else:
        min_status = STATUS_FEASIBLE
        min_reason = None
    warrant = warrant_value_cents(
        borrower.warrant_coverage, facility, cfg.warrant_valuation_factor
    )
    receipts = _schedule_receipts_cents(schedule)
    irr_percent, irr_status, irr_reason, row_moic = _returns_cells(
        schedule, receipts, fee, warrant, close, cfg
    )
    return ComparisonRow(
        structure=STRUCTURE_IO,
        facility_cents=facility,
        all_in_rate=pricing.all_in_rate,
        term_months=term,
        io_months=borrower.io_months,
        io_months_reason=None,
        total_interest_cents=schedule.total_interest_cents,
        closing_fee_cents=fee,
        irr_percent=irr_percent,
        irr_status=irr_status,
        irr_reason=irr_reason,
        moic=row_moic,
        min_dscr=sizing_result.min_dscr,
        min_dscr_status=min_status,
        min_dscr_reason=min_reason,
        binding_constraint=sizing_result.binding_constraint,
        first_breach_status=FIRST_BREACH_STATUS,
        first_breach_reason=FIRST_BREACH_REASON,
        warrant_coverage=borrower.warrant_coverage,
        total_receipts_cents=receipts,
        band_names=_row_band_names(borrower, STRUCTURE_IO),
    )


def _rbf_min_dscr(
    borrower: Borrower, schedule: RbfSchedule, horizon: int
) -> tuple[Decimal | None, str, str | None]:
    """Return (min_dscr, status, reason) for the RBF row.

    The minimum runs over periods with a positive remittance of CFADS_t /
    remittance_t, taken unquantized and quantized once to scale 4 at the
    metric boundary (the sizing basis). No positive remittance (a
    pre-revenue path) gives None with not_applicable and a reason.
    """
    cfads = _horizon_cfads_path(borrower, horizon)
    minimum: Decimal | None = None
    for row in schedule.rows:
        if row.remittance_cents <= 0:
            continue
        ratio = MONEY_CONTEXT.divide(
            Decimal(cfads[row.period - 1]), Decimal(row.remittance_cents)
        )
        if minimum is None or ratio < minimum:
            minimum = ratio
    if minimum is None:
        return None, STATUS_NOT_APPLICABLE, RBF_NO_REMITTANCE_REASON
    return quantize(minimum, DSCR_SCALE), STATUS_FEASIBLE, None


def _rbf_row(
    borrower: Borrower,
    pricing: Pricing,
    sizing_result: SizingResult,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
    shared: SharedInputs,
) -> ComparisonRow:
    """Build the rbf_capped row on the pinned horizon.

    Remittances run at the pinned cfg.generator rate on the borrower's
    revenue path (adapted to the pinned horizon), capped at the borrower's
    rbf_cap multiple. Total interest is the contractual 0 with no reason
    companion (the provenance string for that zero is build.py's envelope
    duty), and total receipts are the final cumulative remittances. The
    warrant lands at the final remittance date, which is the cap-hit date
    when the cap binds inside the horizon.
    """
    facility = sizing_result.facility_cents
    fee = shared.closing_fee_cents
    if facility is None or fee is None:
        raise ValueError(
            f"{borrower.borrower_id}: rbf row requires a feasible facility"
        )
    close = snapshot.observation_date
    horizon = cfg.generator.rbf_horizon_periods
    schedule = build_rbf_schedule(
        facility,
        close,
        horizon,
        cfg.generator.rbf_remittance_rate_pct,
        borrower.rbf_cap,
        _horizon_revenue_path(borrower, horizon),
    )
    min_dscr, min_status, min_reason = _rbf_min_dscr(borrower, schedule, horizon)
    warrant = warrant_value_cents(
        borrower.warrant_coverage, facility, cfg.warrant_valuation_factor
    )
    receipts = _schedule_receipts_cents(schedule)
    irr_percent, irr_status, irr_reason, row_moic = _returns_cells(
        schedule, receipts, fee, warrant, close, cfg
    )
    return ComparisonRow(
        structure=STRUCTURE_RBF,
        facility_cents=facility,
        all_in_rate=pricing.all_in_rate,
        term_months=horizon,
        io_months=None,
        io_months_reason=IO_RBF_REASON,
        total_interest_cents=0,
        closing_fee_cents=fee,
        irr_percent=irr_percent,
        irr_status=irr_status,
        irr_reason=irr_reason,
        moic=row_moic,
        min_dscr=min_dscr,
        min_dscr_status=min_status,
        min_dscr_reason=min_reason,
        binding_constraint=sizing_result.binding_constraint,
        first_breach_status=FIRST_BREACH_STATUS,
        first_breach_reason=FIRST_BREACH_REASON,
        warrant_coverage=borrower.warrant_coverage,
        total_receipts_cents=receipts,
        band_names=_row_band_names(borrower, STRUCTURE_RBF),
    )


def build_comparison(
    borrower: Borrower,
    pricing: Pricing,
    sizing_result: SizingResult,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
) -> ComparisonTable:
    """Build one borrower's three-row comparison table (architecture P3.5).

    The shared block is built once and every row receives identical shared
    values plus the canonical sized facility: the facility is never re-sized
    per structure (the disclosed serviceability proxy), and
    binding_constraint repeats the sizing answer on all three rows.

    Infeasible sizing (facility None) still yields all three rows with the
    uniform shape: facility, closing fee, IRR, MOIC, min DSCR, total
    interest, and total receipts are None with not_applicable statuses and
    the pinned reason, never fabricated zeros; the shared block still
    carries the shared inputs with a None facility. The drawn bullet
    structure is not a comparison row (spec 10.6 pins three rows), so the
    consistency check in tests applies only when the drawn structure is
    io_then_straight_line.
    """
    if not isinstance(borrower, Borrower):
        raise TypeError(
            f"borrower must be a Borrower, got {type(borrower).__name__}"
        )
    if not isinstance(pricing, Pricing):
        raise TypeError(f"pricing must be a Pricing, got {type(pricing).__name__}")
    if not isinstance(sizing_result, SizingResult):
        raise TypeError(
            f"sizing_result must be a SizingResult, got {type(sizing_result).__name__}"
        )
    if not isinstance(snapshot, RateSnapshot):
        raise TypeError(
            f"snapshot must be a RateSnapshot, got {type(snapshot).__name__}"
        )
    if not isinstance(cfg, ScenarioConfig):
        raise TypeError(f"cfg must be a ScenarioConfig, got {type(cfg).__name__}")

    shared = _shared_inputs(borrower, pricing, sizing_result, snapshot)
    binding = sizing_result.binding_constraint
    if sizing_result.facility_cents is None:
        rows = tuple(
            _infeasible_row(structure, shared, binding, borrower, cfg)
            for structure in ROW_STRUCTURES
        )
        return ComparisonTable(
            borrower_id=borrower.borrower_id, shared=shared, rows=rows
        )
    rows = (
        _bridge_row(borrower, pricing, sizing_result, snapshot, cfg, shared),
        _io_row(borrower, pricing, sizing_result, snapshot, cfg, shared),
        _rbf_row(borrower, pricing, sizing_result, snapshot, cfg, shared),
    )
    if tuple(row.structure for row in rows) != ROW_STRUCTURES:
        raise ValueError(
            f"{borrower.borrower_id}: comparison rows must follow "
            f"{ROW_STRUCTURES}"
        )
    return ComparisonTable(
        borrower_id=borrower.borrower_id, shared=shared, rows=rows
    )
