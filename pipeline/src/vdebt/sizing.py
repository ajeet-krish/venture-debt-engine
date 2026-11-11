"""Bisection sizing of the facility under the runway and DSCR floors.

Architecture P2.5, spec section 10.2, sizing pins in architecture section 14.
size_facility returns the largest facility (integer cents) whose post-draw
runway stays at or above cfg.runway_floor_months and whose minimum DSCR on the
canonical io-then-straight-line schedule stays at or above cfg.dscr_floor,
never exceeding the sizing band cap. Both constraints compare unquantized
against their floors, with equality feasible.

The search is an integer bisection: lo = 0 and hi = band_cap_cents are both
evaluated first, then the loop runs while hi minus lo is greater than 1 with
midpoint (lo + hi) // 2, a feasible midpoint raises lo and an infeasible one
lowers hi, and the answer is lo (the largest proven-feasible cent). Exceeding
cfg.solver.bisection_max_iterations (60 in scenario.toml) raises SolverError
printing the current bracket: a hard failure, never a silent fallback. The
integer grid halves 2e10 to 1 in about 35 steps, so 60 is a safe ceiling.

Every feasibility evaluation rebuilds the canonical schedule through
vdebt.schedule.build_schedule("io_then_straight_line", ...) fresh, with no
caching and no analytic shortcut: fixture F1 steps 21 to 23 prove the analytic
linear bound is 8 cents too high once row quantization binds, so the quantized
rows inside the loop are the binding basis. Per period,
DSCR_t = Decimal(cfads_t) / Decimal(interest_t + principal_t) uses the
quantized row cents but is compared unquantized against cfg.dscr_floor.
Pre-revenue borrowers (every cfads_t zero) skip the DSCR constraint entirely
and publish min_dscr as None; a period with no debt service due cannot fail
the DSCR constraint. The runway is (cash_cents minus F) / net_burn_cents in
months, divided in MONEY_CONTEXT and compared unquantized.

Published values quantize exactly once at their boundary: facility as integer
cents, post_draw_runway_months at scale 1, and min_dscr at scale 4 taken from
the unquantized minimum across periods (the F1 expected basis). When lo = 0 is
already infeasible the result carries no facility (a zero-sized facility is
never presented as a solution) and the figures are computed at lo = 0 where
meaningful: runway at zero draw, and min_dscr as None when no period carries
debt service, which covers the pre-revenue case.

SolverError is defined here for phase 2; the phase 4 IRR solver needs the
same exception and tech-lead can relocate it to a shared home then.

Depends on the standard library plus vdebt.config, vdebt.money, and
vdebt.schedule only. No network access anywhere in this module.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from vdebt.config import ScenarioConfig
from vdebt.money import MONEY_CONTEXT, quantize
from vdebt.schedule import build_schedule

# Published status vocabulary (snapshot status vocabulary, generator docstring).
STATUS_FEASIBLE: str = "feasible"
STATUS_INFEASIBLE: str = "infeasible"

# Binding constraint names per borrower (architecture section 14).
BINDING_RUNWAY: str = "runway"
BINDING_DSCR: str = "dscr"
BINDING_BAND_CAP: str = "band_cap"

# Canonical structure for every sizing evaluation: bullet balloons would
# degenerate min DSCR (the balloon period repays the whole facility), and one
# basis keeps the 24 capacities directly comparable (architecture P2.5).
_SIZING_STRUCTURE: str = "io_then_straight_line"

# Published metric scales: post_draw_runway_months scale 1, min_dscr scale 4.
_RUNWAY_SCALE: int = 1
_DSCR_SCALE: int = 4


class SolverError(RuntimeError):
    """Hard bisection failure: iteration cap hit or monotone invariant lost.

    The message always prints the current bracket, per architecture section
    14: a non-converging solve fails loudly and never falls back silently.
    """


@dataclass(frozen=True)
class SizingInputs:
    """One borrower's sizing inputs (architecture P2.5).

    Money is integer cents. cfads_cents carries one integer cent per period
    for periods 1 to term_months, from the generator path, and must equal
    term_months in length. close is the facility close date, term_months and
    io_months the structure counts, all_in_rate percent_annual scale 4 in
    exact Decimal, and band_cap_cents the sizing band cap in integer cents
    (band percent of last round, quantized to cents upstream).
    """

    cash_cents: int
    net_burn_cents: int  # monthly
    cfads_cents: tuple[int, ...]  # per-period, from the generator path
    close: date
    term_months: int
    io_months: int
    all_in_rate: Decimal
    band_cap_cents: int  # percent of last_round quantized to cents


@dataclass(frozen=True)
class SizingResult:
    """Sized facility outcome for one borrower (architecture P2.5).

    status is STATUS_FEASIBLE or STATUS_INFEASIBLE. facility_cents is the
    answer in integer cents and None when infeasible (a zero-sized facility
    is never presented as a solution). binding_constraint is BINDING_RUNWAY,
    BINDING_DSCR, or BINDING_BAND_CAP at a feasible answer, and the first
    failing constraint in the order runway then dscr when infeasible.
    post_draw_runway_months is scale 1 and min_dscr scale 4; min_dscr is None
    when not meaningful (pre-revenue borrowers, and zero draw where no period
    carries debt service). iterations counts bisection loop iterations, so
    both early exits record 0; final_lo and final_hi are the bracket at
    return (the initial [0, band_cap_cents] on an early exit).
    """

    status: str
    facility_cents: int | None
    binding_constraint: str | None
    post_draw_runway_months: Decimal | None
    min_dscr: Decimal | None
    iterations: int
    final_lo: int
    final_hi: int


def _validate_inputs(inputs: SizingInputs) -> None:
    """Enforce the sizing guards, raising ValueError on any violation."""
    if not isinstance(inputs, SizingInputs):
        raise TypeError(
            f"inputs must be a SizingInputs, got {type(inputs).__name__}"
        )
    if inputs.net_burn_cents <= 0:
        raise ValueError(
            f"net_burn_cents must be > 0, got {inputs.net_burn_cents}"
        )
    if inputs.band_cap_cents < 0:
        raise ValueError(
            f"band_cap_cents must be >= 0, got {inputs.band_cap_cents}"
        )
    if len(inputs.cfads_cents) != inputs.term_months:
        raise ValueError(
            f"cfads_cents length {len(inputs.cfads_cents)} must equal "
            f"term_months {inputs.term_months}"
        )


def _runway_months(inputs: SizingInputs, facility_cents: int) -> Decimal:
    """Return post-draw runway (cash_cents minus F) / net_burn_cents in months.

    Divided once in MONEY_CONTEXT and never quantized here: the feasibility
    comparison is unquantized, and the published figure quantizes to scale 1
    at its boundary inside size_facility.
    """
    return MONEY_CONTEXT.divide(
        Decimal(inputs.cash_cents - facility_cents),
        Decimal(inputs.net_burn_cents),
    )


def _first_failing_constraint(
    inputs: SizingInputs,
    cfg: ScenarioConfig,
    facility_cents: int,
) -> str | None:
    """Evaluate feasibility at facility_cents and name the first failure.

    Returns BINDING_RUNWAY when the post-draw runway falls below
    cfg.runway_floor_months (equality feasible), then BINDING_DSCR when any
    period DSCR falls below cfg.dscr_floor (equality feasible), and None when
    both constraints hold. The DSCR scan is skipped entirely when every
    cfads_t is zero (pre-revenue), and a period with no debt service due
    cannot fail it (the ratio is undefined or infinite, never below the
    floor). The schedule is rebuilt fresh on every call through build_schedule
    with no caching and no analytic shortcut (F1 steps 21 to 23).
    """
    if _runway_months(inputs, facility_cents) < cfg.runway_floor_months:
        return BINDING_RUNWAY
    if all(cfads_cents == 0 for cfads_cents in inputs.cfads_cents):
        return None
    schedule = build_schedule(
        _SIZING_STRUCTURE,
        facility_cents,
        inputs.close,
        inputs.term_months,
        inputs.io_months,
        inputs.all_in_rate,
    )
    for row, cfads_cents in zip(schedule.rows, inputs.cfads_cents, strict=True):
        service_cents = row.interest_cents + row.principal_cents
        if service_cents == 0:
            if cfads_cents < 0:
                return BINDING_DSCR
            continue
        dscr = MONEY_CONTEXT.divide(Decimal(cfads_cents), Decimal(service_cents))
        if dscr < cfg.dscr_floor:
            return BINDING_DSCR
    return None


def _min_dscr(inputs: SizingInputs, facility_cents: int) -> Decimal | None:
    """Return the minimum period DSCR at facility_cents, scale 4 or None.

    None when not meaningful: every cfads_t zero (pre-revenue, DSCR
    not_applicable) or no period carrying debt service (zero draw, where the
    ratio divides by zero). Otherwise the minimum across periods is taken
    unquantized on a freshly built schedule's quantized row cents and
    quantized exactly once to scale 4 with ROUND_HALF_UP (the F1 expected
    basis: unquantized min, then one quantize at the metric boundary).
    """
    if all(cfads_cents == 0 for cfads_cents in inputs.cfads_cents):
        return None
    schedule = build_schedule(
        _SIZING_STRUCTURE,
        facility_cents,
        inputs.close,
        inputs.term_months,
        inputs.io_months,
        inputs.all_in_rate,
    )
    minimum: Decimal | None = None
    for row, cfads_cents in zip(schedule.rows, inputs.cfads_cents, strict=True):
        service_cents = row.interest_cents + row.principal_cents
        if service_cents == 0:
            # No debt service due: the ratio is undefined or infinite, so the
            # period cannot define the minimum (zero draw leaves minimum None).
            continue
        dscr = MONEY_CONTEXT.divide(Decimal(cfads_cents), Decimal(service_cents))
        if minimum is None or dscr < minimum:
            minimum = dscr
    if minimum is None:
        return None
    return quantize(minimum, _DSCR_SCALE)


def size_facility(inputs: SizingInputs, cfg: ScenarioConfig) -> SizingResult:
    """Size the largest feasible facility by bisection on integer cents.

    Evaluates feasibility at lo = 0 and hi = band_cap_cents first. An
    infeasible lo returns status infeasible with the first failing constraint
    in the order runway then dscr, no facility, and the runway and min_dscr
    figures computed at lo = 0 where meaningful. A feasible hi returns hi with
    binding BINDING_BAND_CAP. Otherwise the loop bisects while hi minus lo is
    greater than 1 (feasible midpoint raises lo, infeasible lowers hi) and
    raises SolverError printing the bracket when
    cfg.solver.bisection_max_iterations is exceeded.

    The answer is lo (the largest proven-feasible cent). The binding
    constraint at the answer is BINDING_BAND_CAP when lo equals
    band_cap_cents, else the first failing constraint at lo + 1, naming
    runway before dscr (F1 step 14). Published values quantize once:
    facility as integer cents, post_draw_runway_months at scale 1, min_dscr
    at scale 4. Guard violations raise ValueError: non-positive net burn,
    negative band cap, or a cfads length other than term_months.
    """
    _validate_inputs(inputs)
    if not isinstance(cfg, ScenarioConfig):
        raise TypeError(
            f"cfg must be a ScenarioConfig, got {type(cfg).__name__}"
        )
    lo = 0
    hi = inputs.band_cap_cents
    lo_failure = _first_failing_constraint(inputs, cfg, lo)
    hi_failure = _first_failing_constraint(inputs, cfg, hi)
    if lo_failure is not None:
        return SizingResult(
            status=STATUS_INFEASIBLE,
            facility_cents=None,
            binding_constraint=lo_failure,
            post_draw_runway_months=quantize(
                _runway_months(inputs, lo), _RUNWAY_SCALE
            ),
            min_dscr=_min_dscr(inputs, lo),
            iterations=0,
            final_lo=lo,
            final_hi=hi,
        )
    if hi_failure is None:
        return SizingResult(
            status=STATUS_FEASIBLE,
            facility_cents=hi,
            binding_constraint=BINDING_BAND_CAP,
            post_draw_runway_months=quantize(
                _runway_months(inputs, hi), _RUNWAY_SCALE
            ),
            min_dscr=_min_dscr(inputs, hi),
            iterations=0,
            final_lo=lo,
            final_hi=hi,
        )
    iterations = 0
    while hi - lo > 1:
        iterations += 1
        if iterations > cfg.solver.bisection_max_iterations:
            raise SolverError(
                f"sizing bisection exceeded "
                f"{cfg.solver.bisection_max_iterations} iterations; "
                f"bracket=[{lo} {hi}]"
            )
        mid = (lo + hi) // 2
        if _first_failing_constraint(inputs, cfg, mid) is None:
            lo = mid
        else:
            hi = mid
    answer = lo
    if answer == inputs.band_cap_cents:
        # A feasible band cap exits early above; this defensive arm keeps the
        # documented answer rule literal if that exit is ever restructured.
        binding: str | None = BINDING_BAND_CAP
    else:
        binding = _first_failing_constraint(inputs, cfg, answer + 1)
        if binding is None:
            raise SolverError(
                f"sizing bisection lost its monotone invariant at cent "
                f"{answer + 1}; bracket=[{lo} {hi}]"
            )
    return SizingResult(
        status=STATUS_FEASIBLE,
        facility_cents=answer,
        binding_constraint=binding,
        post_draw_runway_months=quantize(
            _runway_months(inputs, answer), _RUNWAY_SCALE
        ),
        min_dscr=_min_dscr(inputs, answer),
        iterations=iterations,
        final_lo=lo,
        final_hi=hi,
    )
