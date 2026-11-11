"""Tests for vdebt.sizing: the F1 golden fixture kill gate plus solver guards.

Covers architecture P2.5, the section 14 bisection pins, and the binding
test_sizing entry in the P2.7 test plan. Cases F1a, F1b, and F1c are sized
from the committed fixture inputs through size_facility and compared against
the committed expected values to the cent: status, facility in integer cents,
binding constraint, post-draw runway at scale 1, and min DSCR at scale 4,
plus the infeasible companion (no facility, runway at zero draw).

Also covered: the exact feasibility boundary at the F1b answer and at the
next cent (both through the predicate and through size_facility with the cap
set to the failing cent), band_cap binding on a synthetic case where the cap
itself is feasible, populated iteration and bracket fields with a one-cent
final bracket, the pre-revenue runway-only path (an all-zero CFADS path
where the DSCR scan is never consulted, min_dscr stays None, and binding
never names dscr), the ValueError guards for a non-positive net burn and a
cfads length mismatch, and the SolverError hard failure that prints the
bracket when an oversized band cap forces the 60-iteration ceiling.

Every expected value is read from the committed JSON fixture; nothing here
regenerates a fixture. The fixture pins term 30 with io 12 and close
2026-04-30 for these cases (architecture section 8), so SizingInputs carry
term_months 30 and one cfads cent per period; the fixture is the binding
ground truth for these test vectors.
"""

import json
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from vdebt.config import ScenarioConfig, load_scenario
from vdebt.money import quantize
from vdebt.sizing import (
    BINDING_BAND_CAP,
    BINDING_DSCR,
    BINDING_RUNWAY,
    STATUS_FEASIBLE,
    STATUS_INFEASIBLE,
    SizingInputs,
    SolverError,
    _first_failing_constraint,
    size_facility,
)

PIPELINE_DIR = Path(__file__).resolve().parent.parent
SCENARIO_PATH = PIPELINE_DIR / "config" / "scenario.toml"
GOLDEN_DIR = PIPELINE_DIR / "fixtures" / "golden"

# Loaded from the real committed scenario.toml so the runway floor (18.0), the
# DSCR floor (1.1500), and the bisection cap (60) are the engine's own pins.
CFG: ScenarioConfig = load_scenario(SCENARIO_PATH)

F1 = json.loads((GOLDEN_DIR / "sizing_fixture.json").read_text(encoding="utf-8"))
SHARED = F1["inputs"]
CASES = {case["case_id"]: case for case in F1["cases"]}

# The F1b analytic boundary in integer cents, pinned by fixture step 23.
DSCR_ANSWER_CENTS = 418_394_934
DSCR_NEXT_CENT = 418_394_935


def _case_inputs(case_id: str) -> SizingInputs:
    """Build SizingInputs from the committed fixture shared and case inputs.

    Money comes straight from the fixture envelopes: cash and net burn as
    integer cents, band cap as band_cap_pct of last_round quantized once to
    cents, CFADS as revenue times gross margin quantized once to cents (F1
    step 4), and one cfads cent per fixture term period (F1 step 4 basis of a
    constant path at 0.0000 percent growth).
    """
    case = CASES[case_id]
    term = int(SHARED["term"]["value"])
    io = int(SHARED["io"]["value"])
    revenue = int(case["inputs"]["revenue_monthly"]["value"])
    margin = Decimal(str(SHARED["gross_margin_pct"]["value"]))
    cfads_cents = int(quantize(Decimal(revenue) * margin / Decimal(100), 0))
    last_round = Decimal(str(SHARED["last_round"]["value"]))
    band_pct = Decimal(str(SHARED["band_cap_pct"]["value"]))
    band_cap_cents = int(quantize(last_round * band_pct / Decimal(100), 0))
    return SizingInputs(
        cash_cents=int(case["inputs"]["cash"]["value"]),
        net_burn_cents=int(SHARED["net_burn_monthly"]["value"]),
        cfads_cents=tuple([cfads_cents] * term),
        close=date.fromisoformat(str(SHARED["close"]["value"])),
        term_months=term,
        io_months=io,
        all_in_rate=Decimal(str(SHARED["all_in_rate"]["value"])),
        band_cap_cents=band_cap_cents,
    )


def test_fixture_shared_inputs_pin_structure_and_counts() -> None:
    """The fixture, not the generator derivation, fixes these test vectors."""
    assert SHARED["structure"]["value"] == "io_then_straight_line"
    assert int(SHARED["term"]["value"]) == 30
    assert int(SHARED["io"]["value"]) == 12
    assert SHARED["close"]["value"] == "2026-04-30"
    assert Decimal(str(SHARED["all_in_rate"]["value"])) == Decimal("8.0000")
    # Derived inputs land on the fixture pins: CFADS 55,000,000 cents for the
    # 110,000,000 revenue case and band cap 1,000,000,000 cents (F1 steps 4, 5).
    assert _case_inputs("runway_bound").cfads_cents[0] == 55_000_000
    assert _case_inputs("runway_bound").band_cap_cents == 1_000_000_000


def test_f1a_runway_bound_matches_fixture_to_the_cent() -> None:
    """F1a: runway binds at exactly 700,000,000 cents with min DSCR 1.2602."""
    result = size_facility(_case_inputs("runway_bound"), CFG)
    expected = CASES["runway_bound"]["expected"]
    assert result.status == STATUS_FEASIBLE == expected["status"]["value"]
    assert result.facility_cents == expected["facility_usd"]["value"] == 700_000_000
    assert result.binding_constraint == expected["binding_constraint"]["value"]
    assert result.binding_constraint == BINDING_RUNWAY
    runway = result.post_draw_runway_months
    assert runway == Decimal(expected["post_draw_runway_months"]["value"])
    assert str(runway) == "18.0"
    assert runway is not None and runway.as_tuple().exponent == -1
    min_dscr = result.min_dscr
    assert min_dscr == Decimal(expected["min_dscr"]["value"]) == Decimal("1.2602")
    assert min_dscr is not None and min_dscr.as_tuple().exponent == -4


def test_f1b_dscr_bound_matches_fixture_to_the_cent() -> None:
    """F1b: quantized rows bind at 418,394,934 cents, min DSCR at the floor."""
    result = size_facility(_case_inputs("dscr_bound"), CFG)
    expected = CASES["dscr_bound"]["expected"]
    assert result.status == STATUS_FEASIBLE == expected["status"]["value"]
    assert result.facility_cents == expected["facility_usd"]["value"]
    assert result.facility_cents == DSCR_ANSWER_CENTS
    assert result.binding_constraint == expected["binding_constraint"]["value"]
    assert result.binding_constraint == BINDING_DSCR
    runway = result.post_draw_runway_months
    assert runway == Decimal(expected["post_draw_runway_months"]["value"])
    assert str(runway) == "20.8"
    min_dscr = result.min_dscr
    assert min_dscr == Decimal(expected["min_dscr"]["value"]) == Decimal("1.1500")
    assert min_dscr is not None and min_dscr.as_tuple().exponent == -4


def test_f1c_infeasible_at_zero_draw_matches_fixture() -> None:
    """F1c: zero draw already fails the runway floor, no facility published."""
    result = size_facility(_case_inputs("infeasible"), CFG)
    expected = CASES["infeasible"]["expected"]
    assert result.status == STATUS_INFEASIBLE == expected["status"]["value"]
    assert result.binding_constraint == expected["binding_constraint"]["value"]
    assert result.binding_constraint == BINDING_RUNWAY
    assert result.facility_cents is None
    # Companion figures: 1.5 months of runway at zero draw, and no DSCR
    # because no period carries debt service at a zero-sized facility.
    assert result.post_draw_runway_months == Decimal("1.5")
    assert result.min_dscr is None
    assert result.iterations == 0


def test_pre_revenue_zero_cfads_reduces_to_runway_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-revenue sizing consults the runway floor alone, never DSCR.

    An all-zero CFADS path of term length short-circuits the DSCR scan, so
    the schedule builder is replaced with a tripwire that raises: a passing
    run proves no schedule (and therefore no DSCR evaluation) is ever built,
    and min_dscr stays None. With feasible cash the runway floor alone sets
    the answer and the binding constraint; with cash already below the floor
    at zero draw the case is infeasible on runway. No all-zero-cfads
    evaluation ever returns binding dscr.
    """
    term = int(SHARED["term"]["value"])
    close = date.fromisoformat(str(SHARED["close"]["value"]))
    net_burn_cents = 100_000_000

    def _inputs(cash_cents: int) -> SizingInputs:
        return SizingInputs(
            cash_cents=cash_cents,
            net_burn_cents=net_burn_cents,
            cfads_cents=tuple([0] * term),
            close=close,
            term_months=term,
            io_months=int(SHARED["io"]["value"]),
            all_in_rate=Decimal(str(SHARED["all_in_rate"]["value"])),
            band_cap_cents=3_000_000_000,
        )

    def _tripwire(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("DSCR schedule built for a pre-revenue borrower")

    monkeypatch.setattr("vdebt.sizing.build_schedule", _tripwire)

    # Feasible cash: 4,000,000,000 minus F must cover 18 months of burn at
    # 100,000,000 per month, so the runway floor alone caps F at
    # 2,200,000,000 cents, strictly under the 3,000,000,000 band cap.
    feasible = size_facility(_inputs(4_000_000_000), CFG)
    assert feasible.status == STATUS_FEASIBLE
    assert feasible.facility_cents == 2_200_000_000
    assert feasible.binding_constraint == BINDING_RUNWAY
    assert feasible.binding_constraint != BINDING_DSCR
    assert feasible.min_dscr is None
    assert feasible.post_draw_runway_months == Decimal("18.0")

    # Cash below the runway floor at zero draw: infeasible on runway alone.
    infeasible = size_facility(_inputs(1_000_000_000), CFG)
    assert infeasible.status == STATUS_INFEASIBLE
    assert infeasible.facility_cents is None
    assert infeasible.binding_constraint == BINDING_RUNWAY
    assert infeasible.binding_constraint != BINDING_DSCR
    assert infeasible.min_dscr is None
    assert infeasible.post_draw_runway_months == Decimal("10.0")
    assert infeasible.iterations == 0

    # Every feasibility probe on an all-zero CFADS path stays runway-only:
    # the first failing constraint can only be None or runway, never dscr.
    inputs = _inputs(4_000_000_000)
    for cents in (0, 1, 2_200_000_000, 2_200_000_001, 3_000_000_000):
        binding = _first_failing_constraint(inputs, CFG, cents)
        assert binding != BINDING_DSCR
        assert binding in (None, BINDING_RUNWAY)


def test_f1b_boundary_feasible_at_answer_infeasible_next_cent() -> None:
    """The answer is the largest feasible cent: F passes, F plus 1 fails."""
    inputs = _case_inputs("dscr_bound")
    assert _first_failing_constraint(inputs, CFG, DSCR_ANSWER_CENTS) is None
    assert _first_failing_constraint(inputs, CFG, DSCR_NEXT_CENT) == BINDING_DSCR
    # Public API route: cap the domain at the failing cent and the bisection
    # must still land on the answer with DSCR named as the binding constraint.
    result = size_facility(replace(inputs, band_cap_cents=DSCR_NEXT_CENT), CFG)
    assert result.status == STATUS_FEASIBLE
    assert result.facility_cents == DSCR_ANSWER_CENTS
    assert result.binding_constraint == BINDING_DSCR
    assert result.final_lo == DSCR_ANSWER_CENTS
    assert result.final_hi == DSCR_NEXT_CENT


def test_band_cap_binds_when_the_cap_itself_is_feasible() -> None:
    """A synthetic huge-cash, huge-cfads case exits early at the cap."""
    term = int(SHARED["term"]["value"])
    inputs = SizingInputs(
        cash_cents=10**13,
        net_burn_cents=100_000_000,
        cfads_cents=tuple([10**10] * term),
        close=date.fromisoformat(str(SHARED["close"]["value"])),
        term_months=term,
        io_months=int(SHARED["io"]["value"]),
        all_in_rate=Decimal("8.0000"),
        band_cap_cents=1_000_000_000,
    )
    result = size_facility(inputs, CFG)
    assert result.status == STATUS_FEASIBLE
    assert result.facility_cents == 1_000_000_000
    assert result.binding_constraint == BINDING_BAND_CAP
    assert result.iterations == 0
    assert result.final_lo == 0
    assert result.final_hi == 1_000_000_000


def test_bisection_iterations_and_final_bracket_are_populated() -> None:
    """Both bisection cases record iterations and a one-cent final bracket."""
    for case_id in ("runway_bound", "dscr_bound"):
        result = size_facility(_case_inputs(case_id), CFG)
        assert result.status == STATUS_FEASIBLE
        assert result.iterations > 0
        assert result.final_hi - result.final_lo == 1
        assert result.final_lo == result.facility_cents
        assert result.final_hi == result.facility_cents + 1


def test_zero_net_burn_raises_value_error() -> None:
    """A non-positive monthly burn makes the runway ratio undefined."""
    inputs = replace(_case_inputs("runway_bound"), net_burn_cents=0)
    with pytest.raises(ValueError, match="net_burn_cents must be > 0"):
        size_facility(inputs, CFG)


def test_cfads_length_mismatch_raises_value_error() -> None:
    """One cfads cent per period is enforced against term_months."""
    inputs = replace(_case_inputs("runway_bound"), cfads_cents=(55_000_000,))
    with pytest.raises(ValueError, match="cfads_cents length"):
        size_facility(inputs, CFG)


def test_oversized_band_cap_hits_iteration_ceiling_with_bracket() -> None:
    """A cap needing more than 60 halvings is a hard SolverError, printed."""
    inputs = replace(_case_inputs("runway_bound"), band_cap_cents=2**64)
    with pytest.raises(SolverError) as excinfo:
        size_facility(inputs, CFG)
    message = str(excinfo.value)
    assert f"{CFG.solver.bisection_max_iterations} iterations" in message
    match = re.search(r"bracket=\[(\d+) (\d+)\]", message)
    assert match is not None, message
    bracket_lo = int(match.group(1))
    bracket_hi = int(match.group(2))
    # The printed bracket must still straddle the 700,000,000 boundary and
    # remain unconverged, exactly the state the hard failure reports.
    assert bracket_lo <= 700_000_000
    assert bracket_hi >= 700_000_001
    assert bracket_hi - bracket_lo > 1
