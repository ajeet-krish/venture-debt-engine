"""Downside scenario runner, published deltas, rate shock, covenant inputs.

Architecture P4.2 (downside transmission) and P4.3 (rate shock). Pure
computation like returns.py and covenants.py: dataclasses in, dataclasses
out. Envelope assembly stays in build.py, the single envelope owner. This
module reads no files, no wall clock, and no network.

Path transforms (P4.2), one ROUND_HALF_UP quantize per period in
MONEY_CONTEXT: downside revenue is revenue x (1 minus downlift / 100);
downside CFADS is recomputed from the downside revenue x gross margin / 100;
downside burn is net burn x (1 plus uplift / 100) quantized once; the
downside warrant is base warrant x warrant_factor quantized once, the
standalone revalued figure (equivalently quantize(coverage / 100 x facility
x valuation_factor x warrant_factor, 0) once, because the base warrant is
already integer cents).

Scenario flow assembly follows the pinned Package A transmission rule
(owner confirmed 2026-11-12): term structures (bullet,
io_then_straight_line, bridge) publish contractual scenario cash flows that
are identical to base, and BOTH scenarios compute lender IRR and MOIC with
the BASE warrant valuation, so those outputs are byte identical across
scenarios and the revalued warrant never enters a flow set. The revalued
warrant publishes only as the downside block's standalone warrant figure.
rbf_capped is the one structure whose flows move: the downside rebuilds the
remittance schedule through schedule.build_rbf_schedule on the downside
revenue path at the same advance, rate, and cap, so receipts fall with
revenue and a base cap-hit borrower can degrade to shortfall with its
residual companion.

Deltas are downside minus base at each published scale (P4.2): lender IRR
as bp scale 1 through returns.irr_delta_bp, MOIC as multiple scale 2, and
interest, receipts, and warrant as integer cents. Every leaf carries a
label from delta_label: "invariant by construction" when the numeric delta
is exactly zero, else "downside minus base", so build.py publishes a status
label on the delta envelope and the browser never subtracts (P4.5).

Rate shock (P4.3): the schedule rebuilds at the shocked all-in rate, the
shocked IRR solves the shocked flows with the BASE warrant (the shock is a
rate scenario only), and irr_delta_bp plus sensitivity_per_100bp reuse the
phase 3 returns helpers at bp scale 1. RBF publishes shocked_total_interest
as the contractual zero with its provenance string and the shocked IRR
fields as not_applicable with the pinned reason, never a fabricated 0 bp.

Covenant scenario inputs: build_scenario_covenant_inputs derives the
ebitda, net_debt, cash, cash_interest, debt_service, and dates tuples plus
the scenario burn floor from a structure schedule and the scenario burn and
CFADS paths. It is configuration free: the caller passes every limit, and
build.py feeds the tuples to covenants.evaluate_package for base and
downside (the downside uses downside CFADS and downside burn).

Depends on the standard library plus vdebt.covenants, vdebt.config,
vdebt.generator, vdebt.money, vdebt.pricing, vdebt.returns, vdebt.schedule,
and vdebt.sizing. No network access anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from vdebt.config import ScenarioConfig
from vdebt.covenants import burn_floor_cents, cash_path
from vdebt.generator import Borrower
from vdebt.money import MONEY_CONTEXT, quantize
from vdebt.pricing import Pricing
from vdebt.returns import (
    DatedCashFlow,
    IRRResult,
    irr_delta_bp,
    lender_cash_flows,
    sensitivity_per_100bp,
    solve_irr,
    warrant_value_cents,
)
from vdebt.returns import (
    moic as receipts_moic,
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

# Published scenario names (P4.2 payload shape).
SCENARIO_BASE: str = "base"
SCENARIO_DOWNSIDE: str = "downside"
SCENARIOS: tuple[str, ...] = (SCENARIO_BASE, SCENARIO_DOWNSIDE)

# Published status vocabulary (snapshot.STATUS_VALUES carries all three).
STATUS_FEASIBLE: str = "feasible"
STATUS_NO_SOLUTION: str = "no_solution"
STATUS_NOT_APPLICABLE: str = "not_applicable"

# Structure names this module dispatches on (P4.2 term versus RBF split).
STRUCTURE_RBF: str = "rbf_capped"
STRUCTURE_BRIDGE: str = "bridge"
_KNOWN_STRUCTURES: frozenset[str] = frozenset(
    {"bullet", "io_then_straight_line", STRUCTURE_BRIDGE, STRUCTURE_RBF}
)

# Published delta status labels (P4.5: a zero delta renders from a status
# label on the envelope, never from a browser threshold comparison).
DELTA_LABEL_INVARIANT: str = "invariant by construction"
DELTA_LABEL_DOWNSIDE_MINUS_BASE: str = "downside minus base"

# Pinned not_applicable and reason strings (P4.3).
RBF_TOTAL_INTEREST_REASON: str = "derived: RBF carries no interest term"
RBF_RATE_SHOCK_IRR_REASON: str = (
    "RBF carries no interest term so a rate shock does not reprice its receipts"
)
IRR_DELTA_NO_SOLUTION_REASON: str = (
    "lender IRR delta needs a feasible IRR in both scenarios"
)

# Publish scales not owned elsewhere: bp and multiple (returns.py uses the
# same private values; they are restated here to keep this module
# self-describing at its publication boundary).
_BP_SCALE: int = 1
_MULTIPLE_SCALE: int = 2
_ZERO_SCALE: int = 0

# Exact operands for the fixed multipliers, divisors, and factors.
_ONE: Decimal = Decimal(1)
_ONE_HUNDRED: Decimal = Decimal(100)


@dataclass(frozen=True)
class ScenarioBlock:
    """One scenario's flow set and published return metrics (P4.2).

    scenario is SCENARIO_BASE or SCENARIO_DOWNSIDE and structure the drawn
    borrower structure. lender_flows are the dated lender cash flows that
    the scenario's IRR solved over, computed with the BASE warrant in both
    scenarios (Package A). irr is the returns.IRRResult over those flows,
    moic the receipts-based multiple at scale 2, and the three money totals
    integer cents. warrant_cents is the scenario-published figure: the base
    warrant for base and the revalued standalone figure for downside, which
    never enters lender_flows. rbf_status and rbf_residual_cents are set
    only on the downside block of an rbf_capped borrower; every other block
    leaves them None.
    """

    scenario: str
    structure: str
    schedule: Schedule | RbfSchedule
    lender_flows: tuple[DatedCashFlow, ...]
    irr: IRRResult
    moic: Decimal
    total_interest_cents: int
    total_receipts_cents: int
    closing_fee_cents: int
    warrant_cents: int
    rbf_status: str | None = None
    rbf_residual_cents: int | None = None


@dataclass(frozen=True)
class ScenarioDeltas:
    """Every published headline delta, downside minus base (P4.2).

    lender_irr_delta_bp is (downside IRR percent minus base IRR percent)
    x 100 quantized once to bp scale 1; it is None with
    lender_irr_delta_reason when either IRR is not feasible, never a
    fabricated 0 bp. Each leaf carries its own label from delta_label so
    build.py publishes the invariant status beside a zero delta (P4.5).
    """

    lender_irr_delta_bp: Decimal | None
    lender_irr_delta_label: str | None
    lender_irr_delta_reason: str | None
    moic_delta: Decimal
    moic_delta_label: str
    total_interest_delta_cents: int
    total_interest_delta_label: str
    total_receipts_delta_cents: int
    total_receipts_delta_label: str
    warrant_delta_cents: int
    warrant_delta_label: str


@dataclass(frozen=True)
class ScenarioPair:
    """Base block, downside block, and their published deltas for one borrower."""

    base: ScenarioBlock
    downside: ScenarioBlock
    deltas: ScenarioDeltas


@dataclass(frozen=True)
class RateShockBlock:
    """The P4.3 rate-shock block: shocked interest, IRR, delta, sensitivity.

    shocked_all_in_rate echoes pricing.shocked_all_in_rate.
    shocked_total_interest_cents is the total interest of the schedule
    rebuilt at the shocked all-in rate, or the contractual 0 for RBF with
    total_interest_reason carrying the P4.3 provenance string. shocked_irr
    is the returns.IRRResult over the shocked flows with the BASE warrant,
    or None for RBF; irr_status and irr_reason are the flat status and
    companion reason build.py publishes (not_applicable with the pinned RBF
    reason, no_solution with the solver reason). irr_delta_bp and
    sensitivity_per_100bp are bp scale 1, or None with delta_reason when
    the delta is undefined: never a fabricated 0 bp.
    """

    shocked_all_in_rate: Decimal
    shocked_total_interest_cents: int
    total_interest_reason: str | None
    shocked_irr: IRRResult | None
    irr_status: str
    irr_reason: str | None
    irr_delta_bp: Decimal | None
    sensitivity_per_100bp: Decimal | None
    delta_reason: str | None


@dataclass(frozen=True)
class ScenarioCovenantInputs:
    """Per-period covenant paths for one scenario plus the burn-floor limit.

    Field names match the covenants.evaluate_package keyword parameters so
    build.py forwards them directly; burn_floor_cents is the scenario
    min_cash limit (P4.1: the floor scales with the scenario's burn) for
    the caller's min_cash CovenantSpec. ebitda is the scenario CFADS proxy
    per period, net_debt the schedule closing balance minus the derived
    cash path, cash the cash_path recurrence under the scenario burn, and
    cash_interest None for RBF (no interest term).
    """

    ebitda: tuple[int, ...]
    net_debt: tuple[int, ...]
    cash: tuple[int, ...]
    cash_interest: tuple[int, ...] | None
    debt_service: tuple[int, ...]
    dates: tuple[date, ...]
    burn_floor_cents: int


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


def _require_non_negative(value: Decimal, name: str) -> Decimal:
    """Return a Decimal that must be greater than or equal to zero."""
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return value


def _require_path(values: object, name: str) -> tuple[int, ...]:
    """Return a tuple of integer cents, rejecting lists and non-int elements."""
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be a tuple, got {type(values).__name__}")
    return tuple(
        _require_cents(element, f"{name}[{index}]") for index, element in enumerate(values)
    )


def downside_revenue(revenue_path: tuple[int, ...], downlift_pct: Decimal) -> tuple[int, ...]:
    """Return the downlifted revenue path, one quantize per period (P4.2).

    Each period is quantize(revenue_t x (1 minus downlift_pct / 100), 0)
    with ROUND_HALF_UP in MONEY_CONTEXT, exactly one quantize per period.
    The factor is built once in MONEY_CONTEXT and shared across periods;
    the rounding still happens per period, never once over the whole path.
    """
    path = _require_path(revenue_path, "revenue_path")
    downlift = _require_non_negative(
        _require_decimal(downlift_pct, "downlift_pct"), "downlift_pct"
    )
    factor = MONEY_CONTEXT.subtract(_ONE, MONEY_CONTEXT.divide(downlift, _ONE_HUNDRED))
    return tuple(
        int(quantize(MONEY_CONTEXT.multiply(Decimal(amount), factor), _ZERO_SCALE))
        for amount in path
    )


def downside_cfads(downside_revenue: tuple[int, ...], gross_margin: Decimal) -> tuple[int, ...]:
    """Return CFADS recomputed from downside revenue, one quantize per period.

    Each period is quantize(revenue_t x gross_margin / 100, 0) with
    ROUND_HALF_UP in MONEY_CONTEXT, the generator's own CFADS rule applied
    to the downside revenue path (P4.2: cfads_t^D from revenue_t^D).
    """
    path = _require_path(downside_revenue, "downside_revenue")
    margin = _require_non_negative(_require_decimal(gross_margin, "gross_margin"), "gross_margin")
    return tuple(
        int(
            quantize(
                MONEY_CONTEXT.divide(
                    MONEY_CONTEXT.multiply(Decimal(amount), margin), _ONE_HUNDRED
                ),
                _ZERO_SCALE,
            )
        )
        for amount in path
    )


def downside_burn(net_burn: int, uplift_pct: Decimal) -> int:
    """Return the downside net burn, quantized exactly once (P4.2).

    quantize(net_burn x (1 plus uplift_pct / 100), 0) with ROUND_HALF_UP in
    MONEY_CONTEXT: one product, one quantize, no intermediate rounding.
    """
    burn = _require_non_negative(Decimal(_require_cents(net_burn, "net_burn")), "net_burn")
    uplift = _require_non_negative(_require_decimal(uplift_pct, "uplift_pct"), "uplift_pct")
    factor = MONEY_CONTEXT.add(_ONE, MONEY_CONTEXT.divide(uplift, _ONE_HUNDRED))
    return int(quantize(MONEY_CONTEXT.multiply(burn, factor), _ZERO_SCALE))


def downside_warrant(base_warrant_cents: int, warrant_factor: Decimal) -> int:
    """Return the standalone revalued warrant figure, quantized once (P4.2).

    quantize(base_warrant x warrant_factor, 0) with ROUND_HALF_UP in
    MONEY_CONTEXT. This equals quantize(coverage / 100 x facility x
    valuation_factor x warrant_factor, 0) taken as one product chain,
    because the base warrant is already integer cents: one quantize at this
    boundary either way. warrant_factor must lie in (0, 1], the config.py
    pin, so the revaluation can never zero out or amplify the figure. The
    returned value is the downside block's published warrant only; it never
    enters a scenario flow set (Package A).
    """
    base = _require_cents(base_warrant_cents, "base_warrant_cents")
    if base < 0:
        raise ValueError(f"base_warrant_cents must be >= 0, got {base}")
    factor = _require_decimal(warrant_factor, "warrant_factor")
    # The config pin is (0, 1]: a factor at or below 0 would zero or invert
    # the figure, and a factor above 1 would amplify it.
    if factor <= 0:
        raise ValueError(f"warrant_factor must be > 0, got {factor}")
    if factor > _ONE:
        raise ValueError(f"warrant_factor must be <= 1, got {factor}")
    return int(quantize(MONEY_CONTEXT.multiply(Decimal(base), factor), _ZERO_SCALE))


def delta_label(delta: Decimal | int) -> str:
    """Return the published status label for one delta leaf (P4.5).

    "invariant by construction" when the delta is exactly zero (the
    Package A term-loan rows), else "downside minus base". A Decimal zero
    at any scale counts as zero, so 0.00 and 0.0000 both label invariant.
    """
    if isinstance(delta, bool) or not isinstance(delta, (Decimal, int)):
        raise TypeError(f"delta must be a Decimal or int, got {type(delta).__name__}")
    if delta == 0:
        return DELTA_LABEL_INVARIANT
    return DELTA_LABEL_DOWNSIDE_MINUS_BASE


def _receipts_cents(schedule: Schedule | RbfSchedule) -> int:
    """Return total lender receipts: interest plus principal, or remittances.

    An RbfSchedule carries no interest or principal split, so its receipts
    are the final cumulative remittances; a Schedule's receipts are its
    interest plus principal totals (the P3.3 receipts basis).
    """
    final_cumulative = getattr(schedule, "final_cumulative_cents", None)
    if final_cumulative is not None:
        return final_cumulative
    return schedule.total_interest_cents + schedule.total_principal_cents


def _total_interest_cents(schedule: Schedule | RbfSchedule) -> int:
    """Return the published total interest: schedule interest, or RBF's zero.

    RBF carries no interest term, so its scenario total interest is the
    contractual zero in every scenario (the provenance string for that
    zero is build.py's envelope duty, re-exported as
    RBF_TOTAL_INTEREST_REASON for the rate-shock block).
    """
    if isinstance(schedule, RbfSchedule):
        return 0
    return schedule.total_interest_cents


def _final_payment_date(schedule: Schedule | RbfSchedule) -> date:
    """Return the final row's payment date (the warrant dating anchor)."""
    return schedule.rows[-1].payment_date


def _validate_assembly_inputs(
    borrower: Borrower,
    pricing: Pricing,
    sizing_result: SizingResult,
    base_schedule: Schedule | RbfSchedule,
    cfg: ScenarioConfig,
) -> int:
    """Type-check the shared assembly inputs and return the sized facility.

    Fails loudly when the drawn structure and the base schedule kind
    disagree, when an amortizing or bridge schedule was not built at the
    priced all-in rate (a shocked or stale schedule must never pass as
    base), or when sizing is infeasible: there is no facility to fund, and
    build.py publishes the not_applicable scenario shape instead of calling
    into this module.
    """
    if not isinstance(borrower, Borrower):
        raise TypeError(f"borrower must be a Borrower, got {type(borrower).__name__}")
    if not isinstance(pricing, Pricing):
        raise TypeError(f"pricing must be a Pricing, got {type(pricing).__name__}")
    if not isinstance(sizing_result, SizingResult):
        raise TypeError(
            f"sizing_result must be a SizingResult, got {type(sizing_result).__name__}"
        )
    if not isinstance(cfg, ScenarioConfig):
        raise TypeError(f"cfg must be a ScenarioConfig, got {type(cfg).__name__}")
    if not isinstance(base_schedule, (Schedule, RbfSchedule)):
        raise TypeError(
            "base_schedule must be a Schedule or RbfSchedule, got "
            f"{type(base_schedule).__name__}"
        )
    if borrower.structure not in _KNOWN_STRUCTURES:
        allowed = ", ".join(sorted(_KNOWN_STRUCTURES))
        raise ValueError(
            f"{borrower.borrower_id}: unknown structure {borrower.structure!r}; "
            f"expected one of: {allowed}"
        )
    if borrower.structure == STRUCTURE_RBF:
        if not isinstance(base_schedule, RbfSchedule):
            raise ValueError(
                f"{borrower.borrower_id}: rbf_capped needs an RbfSchedule base "
                f"schedule, got {type(base_schedule).__name__}"
            )
    else:
        if not isinstance(base_schedule, Schedule):
            raise ValueError(
                f"{borrower.borrower_id}: structure {borrower.structure!r} needs a "
                f"Schedule base schedule, got {type(base_schedule).__name__}"
            )
        if base_schedule.all_in_rate != pricing.all_in_rate:
            raise ValueError(
                f"{borrower.borrower_id}: base schedule all-in rate "
                f"{base_schedule.all_in_rate} does not match the priced all-in "
                f"rate {pricing.all_in_rate}"
            )
    facility = sizing_result.facility_cents
    if facility is None:
        raise ValueError(
            f"{borrower.borrower_id}: sizing is infeasible so no facility exists "
            "to fund; build.py publishes the not_applicable scenario shape "
            "instead of assembling flows"
        )
    return facility


def _downside_rbf_schedule(
    borrower: Borrower, base_schedule: RbfSchedule, cfg: ScenarioConfig
) -> RbfSchedule:
    """Rebuild the RBF remittance schedule on the downside revenue path (P4.2).

    Same advance, remittance rate, cap multiple, horizon, and close as the
    base schedule: only the revenue path changes, downlifted once per
    period. Remittances fall, rows stop at the cap-hit period or run the
    horizon, and a base cap-hit borrower can degrade to shortfall, which
    the caller reads off the returned schedule's status and residual.
    Raises ValueError when the borrower's revenue path does not span the
    base schedule horizon (the generator pins them equal for rbf_capped).
    """
    horizon = base_schedule.horizon
    path = borrower.revenue_path
    if len(path) != horizon:
        raise ValueError(
            f"{borrower.borrower_id}: RBF revenue path length {len(path)} does "
            f"not equal the base schedule horizon {horizon}"
        )
    lowered = downside_revenue(path, cfg.downside.revenue_downlift_pct)
    return build_rbf_schedule(
        base_schedule.advance_cents,
        base_schedule.close,
        horizon,
        base_schedule.remittance_rate_pct,
        base_schedule.cap_multiple,
        lowered,
    )


def assemble_scenario(
    borrower: Borrower,
    pricing: Pricing,
    sizing_result: SizingResult,
    base_schedule: Schedule | RbfSchedule,
    cfg: ScenarioConfig,
    scenario: str,
) -> ScenarioBlock:
    """Assemble one scenario's flow set and return its published metrics.

    Package A (owner confirmed 2026-11-12): the flow set is built with the
    BASE warrant valuation in both scenarios, so term structures
    (bullet, io_then_straight_line, bridge) produce byte-identical flows,
    IRR, MOIC, total interest, and total receipts across scenarios while
    the downside block still publishes the revalued standalone warrant as
    its warrant_cents. rbf_capped is the exception: the downside rebuilds
    the remittance schedule through _downside_rbf_schedule and recomputes
    receipts, IRR, and MOIC from the downside remittances with the base
    warrant, carrying the rebuilt status and residual on the block.

    scenario must be SCENARIO_BASE or SCENARIO_DOWNSIDE. Raises TypeError
    or ValueError on malformed inputs (see _validate_assembly_inputs).
    """
    if scenario not in SCENARIOS:
        allowed = ", ".join(SCENARIOS)
        raise ValueError(f"scenario must be one of: {allowed}, got {scenario!r}")
    facility = _validate_assembly_inputs(borrower, pricing, sizing_result, base_schedule, cfg)
    fee = closing_fee_cents(facility, borrower.closing_fee)
    base_warrant = warrant_value_cents(
        borrower.warrant_coverage, facility, cfg.warrant_valuation_factor
    )
    rbf_status: str | None = None
    rbf_residual_cents: int | None = None
    if scenario == SCENARIO_BASE or borrower.structure != STRUCTURE_RBF:
        schedule: Schedule | RbfSchedule = base_schedule
    else:
        schedule = _downside_rbf_schedule(borrower, base_schedule, cfg)
        rbf_status = schedule.status
        rbf_residual_cents = schedule.residual_cents
    # Package A: the BASE warrant enters the flow set in both scenarios;
    # the revalued figure publishes only as the block's warrant_cents.
    flows = lender_cash_flows(schedule, fee, base_warrant, _final_payment_date(schedule))
    irr = solve_irr(flows, schedule.close, cfg.solver)
    receipts = _receipts_cents(schedule)
    published_warrant = (
        base_warrant
        if scenario == SCENARIO_BASE
        else downside_warrant(base_warrant, cfg.downside.warrant_factor)
    )
    return ScenarioBlock(
        scenario=scenario,
        structure=borrower.structure,
        schedule=schedule,
        lender_flows=flows,
        irr=irr,
        moic=receipts_moic(receipts, base_warrant, facility),
        total_interest_cents=_total_interest_cents(schedule),
        total_receipts_cents=receipts,
        closing_fee_cents=fee,
        warrant_cents=published_warrant,
        rbf_status=rbf_status,
        rbf_residual_cents=rbf_residual_cents,
    )


def scenario_deltas(base: ScenarioBlock, downside: ScenarioBlock) -> ScenarioDeltas:
    """Return every published headline delta, downside minus base (P4.2).

    lender_irr_delta_bp is (downside IRR percent minus base IRR percent)
    x 100 quantized once to bp scale 1 through returns.irr_delta_bp; when
    either IRR is not feasible the delta publishes None with
    lender_irr_delta_reason instead of a fabricated 0 bp. moic_delta is the
    scale 2 difference quantized once, the money deltas are exact integer
    cent differences, and each leaf's label comes from delta_label.
    Raises ValueError when the blocks are not a matching base and downside
    pair for the same structure.
    """
    if not isinstance(base, ScenarioBlock):
        raise TypeError(f"base must be a ScenarioBlock, got {type(base).__name__}")
    if not isinstance(downside, ScenarioBlock):
        raise TypeError(
            f"downside must be a ScenarioBlock, got {type(downside).__name__}"
        )
    if base.scenario != SCENARIO_BASE or downside.scenario != SCENARIO_DOWNSIDE:
        raise ValueError(
            "scenario_deltas expects the base block first and the downside "
            f"block second, got {base.scenario!r} and {downside.scenario!r}"
        )
    if base.structure != downside.structure:
        raise ValueError(
            f"scenario structures must match, got {base.structure!r} and "
            f"{downside.structure!r}"
        )
    if (
        base.irr.status == STATUS_FEASIBLE
        and downside.irr.status == STATUS_FEASIBLE
        and base.irr.irr_percent is not None
        and downside.irr.irr_percent is not None
    ):
        irr_delta = irr_delta_bp(base.irr.irr_percent, downside.irr.irr_percent)
        irr_label: str | None = delta_label(irr_delta)
        irr_reason: str | None = None
    else:
        irr_delta = None
        irr_label = None
        irr_reason = IRR_DELTA_NO_SOLUTION_REASON
    moic_delta = quantize(
        MONEY_CONTEXT.subtract(downside.moic, base.moic), _MULTIPLE_SCALE
    )
    interest_delta = downside.total_interest_cents - base.total_interest_cents
    receipts_delta = downside.total_receipts_cents - base.total_receipts_cents
    warrant_delta = downside.warrant_cents - base.warrant_cents
    return ScenarioDeltas(
        lender_irr_delta_bp=irr_delta,
        lender_irr_delta_label=irr_label,
        lender_irr_delta_reason=irr_reason,
        moic_delta=moic_delta,
        moic_delta_label=delta_label(moic_delta),
        total_interest_delta_cents=interest_delta,
        total_interest_delta_label=delta_label(interest_delta),
        total_receipts_delta_cents=receipts_delta,
        total_receipts_delta_label=delta_label(receipts_delta),
        warrant_delta_cents=warrant_delta,
        warrant_delta_label=delta_label(warrant_delta),
    )


def assemble_scenarios(
    borrower: Borrower,
    pricing: Pricing,
    sizing_result: SizingResult,
    base_schedule: Schedule | RbfSchedule,
    cfg: ScenarioConfig,
) -> ScenarioPair:
    """Assemble both scenarios and their deltas for one borrower.

    Runs assemble_scenario for base and downside on the same inputs and
    derives the delta set once, so the pair can never disagree about which
    two blocks were subtracted.
    """
    base = assemble_scenario(borrower, pricing, sizing_result, base_schedule, cfg, SCENARIO_BASE)
    downside = assemble_scenario(
        borrower, pricing, sizing_result, base_schedule, cfg, SCENARIO_DOWNSIDE
    )
    return ScenarioPair(
        base=base, downside=downside, deltas=scenario_deltas(base, downside)
    )


def _shocked_schedule(
    borrower: Borrower,
    pricing: Pricing,
    facility: int,
    base_schedule: Schedule,
    cfg: ScenarioConfig,
) -> Schedule:
    """Rebuild the term schedule at the shocked all-in rate (P4.3).

    bridge goes through build_bridge_schedule at the pinned tenor and the
    amortizing structures through build_schedule on the borrower's own
    structure, term, and IO months, both at pricing.shocked_all_in_rate:
    only interest reprices, so balances, principal, dates, fee, and warrant
    stay at their base values.
    """
    close = base_schedule.close
    if borrower.structure == STRUCTURE_BRIDGE:
        return build_bridge_schedule(
            facility, close, cfg.generator.bridge_tenor_months, pricing.shocked_all_in_rate
        )
    return build_schedule(
        borrower.structure,
        facility,
        close,
        borrower.term_months,
        borrower.io_months,
        pricing.shocked_all_in_rate,
    )


def build_rate_shock(
    borrower: Borrower,
    pricing: Pricing,
    sizing_result: SizingResult,
    base_schedule: Schedule | RbfSchedule,
    cfg: ScenarioConfig,
) -> RateShockBlock:
    """Build the P4.3 rate-shock block for one borrower.

    Term structures rebuild the schedule at pricing.shocked_all_in_rate,
    solve the shocked flows with the BASE warrant (a rate scenario only:
    no balance, principal, fee, or warrant changes), and derive
    irr_delta_bp and sensitivity_per_100bp through the phase 3 returns
    helpers at bp scale 1. RBF publishes shocked total interest as the
    contractual zero with RBF_TOTAL_INTEREST_REASON and the shocked IRR
    fields as not_applicable with RBF_RATE_SHOCK_IRR_REASON: a rate shock
    does not reprice royalty receipts, so the delta and sensitivity fields
    stay None rather than publishing a fabricated 0 bp.
    """
    facility = _validate_assembly_inputs(borrower, pricing, sizing_result, base_schedule, cfg)
    if borrower.structure == STRUCTURE_RBF:
        return RateShockBlock(
            shocked_all_in_rate=pricing.shocked_all_in_rate,
            shocked_total_interest_cents=0,
            total_interest_reason=RBF_TOTAL_INTEREST_REASON,
            shocked_irr=None,
            irr_status=STATUS_NOT_APPLICABLE,
            irr_reason=RBF_RATE_SHOCK_IRR_REASON,
            irr_delta_bp=None,
            sensitivity_per_100bp=None,
            delta_reason=RBF_RATE_SHOCK_IRR_REASON,
        )
    if not isinstance(base_schedule, Schedule):
        raise TypeError(
            f"{borrower.borrower_id}: term structures need a Schedule base "
            f"schedule, got {type(base_schedule).__name__}"
        )
    fee = closing_fee_cents(facility, borrower.closing_fee)
    base_warrant = warrant_value_cents(
        borrower.warrant_coverage, facility, cfg.warrant_valuation_factor
    )
    shocked_schedule = _shocked_schedule(borrower, pricing, facility, base_schedule, cfg)
    shocked_flows = lender_cash_flows(
        shocked_schedule, fee, base_warrant, _final_payment_date(shocked_schedule)
    )
    shocked_irr = solve_irr(shocked_flows, shocked_schedule.close, cfg.solver)
    base_flows = lender_cash_flows(
        base_schedule, fee, base_warrant, _final_payment_date(base_schedule)
    )
    base_irr = solve_irr(base_flows, base_schedule.close, cfg.solver)
    if (
        shocked_irr.status == STATUS_FEASIBLE
        and base_irr.status == STATUS_FEASIBLE
        and shocked_irr.irr_percent is not None
        and base_irr.irr_percent is not None
    ):
        delta = irr_delta_bp(base_irr.irr_percent, shocked_irr.irr_percent)
        sensitivity = sensitivity_per_100bp(delta, cfg.rate_shock_bp)
        delta_reason: str | None = None
    else:
        delta = None
        sensitivity = None
        delta_reason = IRR_DELTA_NO_SOLUTION_REASON
    return RateShockBlock(
        shocked_all_in_rate=pricing.shocked_all_in_rate,
        shocked_total_interest_cents=shocked_schedule.total_interest_cents,
        total_interest_reason=None,
        shocked_irr=shocked_irr,
        irr_status=shocked_irr.status,
        irr_reason=shocked_irr.reason,
        irr_delta_bp=delta,
        sensitivity_per_100bp=sensitivity,
        delta_reason=delta_reason,
    )


def build_scenario_covenant_inputs(
    borrower: Borrower,
    schedule: Schedule | RbfSchedule,
    scenario_net_burn: int,
    scenario_cfads: tuple[int, ...],
    floor_months: Decimal,
) -> ScenarioCovenantInputs:
    """Build the covenant inputs for one scenario, configuration free (P4.1).

    The structure schedule supplies the payment dates, per-period debt
    service (interest plus principal for amortizing and bridge rows,
    remittance for RBF), and closing balances (row closing balance, or the
    RBF advance carried constant because no principal amortizes). The
    scenario burn drives the cash path recurrence and the burn floor, and
    the scenario CFADS is the EBITDA proxy. cash_interest is None for RBF,
    which takes its pinned not_applicable path in evaluate_package.

    Every limit travels in: floor_months is the min_cash floor in months
    for the caller's CovenantSpec, and the net_leverage, interest_cover,
    and DSCR limits stay in cfg at the publication layer. Raises
    TypeError or ValueError when a path does not span the schedule rows.
    """
    if not isinstance(borrower, Borrower):
        raise TypeError(f"borrower must be a Borrower, got {type(borrower).__name__}")
    if not isinstance(schedule, (Schedule, RbfSchedule)):
        raise TypeError(
            f"schedule must be a Schedule or RbfSchedule, got {type(schedule).__name__}"
        )
    cfads = _require_path(scenario_cfads, "scenario_cfads")
    burn = _require_cents(scenario_net_burn, "scenario_net_burn")
    dates = tuple(row.payment_date for row in schedule.rows)
    period_count = len(dates)
    if period_count == 0:
        raise ValueError("schedule carries no rows so covenant periods are undefined")
    if len(cfads) != period_count:
        raise ValueError(
            f"scenario_cfads length {len(cfads)} must equal the schedule rows "
            f"{period_count}"
        )
    debt_service: list[int] = []
    closing_balances: list[int] = []
    interest_rows: list[int] = []
    if isinstance(schedule, RbfSchedule):
        cash_interest: tuple[int, ...] | None = None
        advance = schedule.advance_cents
        for row in schedule.rows:
            debt_service.append(row.remittance_cents)
            closing_balances.append(advance)
    else:
        for row in schedule.rows:
            debt_service.append(row.interest_cents + row.principal_cents)
            closing_balances.append(row.closing_cents)
            interest_rows.append(row.interest_cents)
        cash_interest = tuple(interest_rows)
    service = tuple(debt_service)
    cash = cash_path(borrower.cash_cents, burn, service)
    net_debt = tuple(
        closing_balances[index] - cash[index] for index in range(period_count)
    )
    return ScenarioCovenantInputs(
        ebitda=cfads,
        net_debt=net_debt,
        cash=cash,
        cash_interest=cash_interest,
        debt_service=service,
        dates=dates,
        burn_floor_cents=burn_floor_cents(burn, _require_decimal(floor_months, "floor_months")),
    )
