"""Tests for vdebt.returns: F6 recompute, MOIC closed form, and guards.

Covers architecture P3.3, P3.4, and the binding test_returns entry in the
P3.8 test plan. Fixture F6 (rate_shock_fixture.json) is rebuilt end to end
from engine code: base and shocked bullet schedules at the pinned all-in
rates 7.0000 and 9.0000, dated lender cash flows compared to the fixture
lender_cash_flows_base list exactly (dates and cents, final date combined),
base and shocked IRR solved and bracketed with engine npv at the published
value minus and plus the 0.0001 percent tolerance, the shock delta 222.6 bp
and sensitivity 111.3 bp, total interests 2,895,891 and 3,723,288 cents,
warrant 300,000 cents, and MOIC 1.03. Both all-in rates are read from the
fixture expected block: the input components base_rate 4.0000 plus spread
3.0000 sum to 7.0000 and the pinned expected base is 7.0000, so the
component sum and the pinned value agree.

Also covered: the MOIC closed form on F2 by direct Decimal division at
scale 2 and agreement between schedule.moic and returns.moic on identical
inputs; no_solution with an all positive flow set and a degenerate all zero
set (reason published, no IRR value); SolverError from solve_irr when a
params copy sets max_iterations to 1, with the bracket in the message;
warrant zero coverage returning 0 for the caller to publish as not_granted,
plus a hand product check at coverage 2.50 on a 400,000,000 cent facility;
and the close cash flow equal to closing fee minus facility for the bridge,
both amortizing structures, and the RBF schedule.

Every fixture value is read from the committed JSON; nothing here
regenerates a fixture. Hand product and closed-form checks write their own
Decimal expressions under an explicit local context instead of calling the
engine money helpers, so those spot checks are independent of returns.py.
"""

import json
from dataclasses import replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path

import pytest

from vdebt.config import load_scenario
from vdebt.returns import (
    DatedCashFlow,
    irr_delta_bp,
    lender_cash_flows,
    npv,
    sensitivity_per_100bp,
    solve_irr,
    warrant_value_cents,
)
from vdebt.returns import (
    moic as returns_moic,
)
from vdebt.schedule import (
    Schedule,
    build_bridge_schedule,
    build_rbf_schedule,
    build_schedule,
    closing_fee_cents,
)
from vdebt.schedule import (
    moic as schedule_moic,
)
from vdebt.sizing import SolverError

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "golden"
SCENARIO_PATH = Path(__file__).resolve().parent.parent / "config" / "scenario.toml"

F2 = json.loads((GOLDEN_DIR / "schedule_bullet.json").read_text(encoding="utf-8"))
F6 = json.loads((GOLDEN_DIR / "rate_shock_fixture.json").read_text(encoding="utf-8"))

CFG = load_scenario(SCENARIO_PATH)

# Pinned F6 all-in rates (expected block): base 7.0000, shocked 9.0000.
BASE_ALL_IN = Decimal(F6["expected"]["all_in_rate_base"]["value"])
SHOCKED_ALL_IN = Decimal(F6["expected"]["all_in_rate_shocked"]["value"])

# IRR publish tolerance from the fixture tolerances block (percent units).
IRR_TOLERANCE = Decimal(F6["tolerances"]["base_irr_percent"]["abs"])


def _f6_close() -> date:
    """Return the F6 close date from the committed fixture inputs."""
    return date.fromisoformat(F6["inputs"]["close"]["value"])


def _build_f6_schedule(all_in_rate: Decimal) -> Schedule:
    """Rebuild one F6 bullet schedule at the given pinned all-in rate.

    Facility 100,000,000 cents, close 2026-01-05, term 5, structure bullet
    (inputs.structure), with io_months 0 (bullet has no interest-only
    period).
    """
    inputs = F6["inputs"]
    return build_schedule(
        structure=str(inputs["structure"]["value"]),
        facility_cents=int(inputs["facility"]["value"]),
        close=date.fromisoformat(inputs["close"]["value"]),
        term_months=int(Decimal(str(inputs["term"]["value"]))),
        io_months=0,
        all_in_rate=all_in_rate,
    )


def _f6_fee() -> int:
    """Return the F6 closing fee in cents via closing_fee_cents (1.00 percent)."""
    inputs = F6["inputs"]
    fee = closing_fee_cents(
        int(inputs["facility"]["value"]),
        Decimal(str(inputs["closing_fee_pct"]["value"])),
    )
    assert fee == F6["expected"]["closing_fee"]["value"] == 1_000_000
    return fee


def _f6_warrant() -> int:
    """Return the F6 warrant value in cents (2.00 percent coverage, 0.15 factor)."""
    inputs = F6["inputs"]
    warrant = warrant_value_cents(
        Decimal(str(inputs["warrant_coverage_pct"]["value"])),
        int(inputs["facility"]["value"]),
        Decimal(str(inputs["warrant_valuation_factor"]["value"])),
    )
    assert warrant == F6["expected"]["warrant_value"]["value"] == 300_000
    return warrant


def _f6_flows(all_in_rate: Decimal) -> tuple[DatedCashFlow, ...]:
    """Build the F6 lender cash flows at one pinned all-in rate (P3.3)."""
    schedule = _build_f6_schedule(all_in_rate)
    warrant_date = schedule.rows[-1].payment_date
    return lender_cash_flows(schedule, _f6_fee(), _f6_warrant(), warrant_date)


def test_f6_base_and_shocked_schedules_match_fixture_field_by_field() -> None:
    """F6 base and shocked bullet schedules rebuilt and matched (P3.8 item 7).

    Both schedules share balances and principal; only interest differs. Every
    row (period, payment_date, opening, interest, principal, closing) and
    both totals are compared by exact int equality to the fixture.
    """
    expected = F6["expected"]
    inputs = F6["inputs"]

    assert str(BASE_ALL_IN) == "7.0000"
    assert str(SHOCKED_ALL_IN) == "9.0000"
    assert BASE_ALL_IN.as_tuple().exponent == -4
    assert SHOCKED_ALL_IN.as_tuple().exponent == -4

    base = _build_f6_schedule(BASE_ALL_IN)
    shocked = _build_f6_schedule(SHOCKED_ALL_IN)

    assert base.structure == shocked.structure == "bullet"
    assert base.facility_cents == shocked.facility_cents == 100_000_000
    assert base.close == shocked.close == date(2026, 1, 5)
    assert base.all_in_rate == BASE_ALL_IN
    assert shocked.all_in_rate == SHOCKED_ALL_IN

    expected_dates = [entry["value"] for entry in expected["payment_dates"]]
    for schedule in (base, shocked):
        dates = [row.payment_date.isoformat() for row in schedule.rows]
        assert dates == expected_dates
        assert dates == [
            "2026-02-05",
            "2026-03-05",
            "2026-04-06",
            "2026-05-05",
            "2026-06-05",
        ]

    for schedule, fixture_rows in (
        (base, expected["schedule_base"]),
        (shocked, expected["schedule_shocked"]),
    ):
        assert len(schedule.rows) == len(fixture_rows) == 5
        for row, exp in zip(schedule.rows, fixture_rows, strict=True):
            assert row.period == exp["period"]["value"]
            assert row.payment_date.isoformat() == exp["payment_date"]["value"]
            assert row.opening_cents == exp["opening_balance"]["value"]
            assert row.interest_cents == exp["interest"]["value"]
            assert row.principal_cents == exp["principal"]["value"]
            assert row.closing_cents == exp["closing_balance"]["value"]

    # Totals are exact integer sums of the quantized rows (F6 steps 21, 22).
    assert base.total_interest_cents == expected["total_interest_base"]["value"]
    assert base.total_interest_cents == 2_895_891
    assert shocked.total_interest_cents == expected["total_interest_shocked"]["value"]
    assert shocked.total_interest_cents == 3_723_288
    assert sum(row.interest_cents for row in base.rows) == base.total_interest_cents
    assert sum(row.interest_cents for row in shocked.rows) == (
        shocked.total_interest_cents
    )
    assert base.total_principal_cents == shocked.total_principal_cents == 100_000_000
    assert inputs["term"]["value"] == "5.0"


def test_f6_lender_cash_flows_equal_fixture_exactly() -> None:
    """Aggregated base lender flows equal lender_cash_flows_base (F6 step 25).

    Dates and cents compared exactly, the close flow is fee minus facility,
    and the final payment date carries interest plus principal plus warrant
    as one combined flow.
    """
    expected = F6["expected"]
    schedule = _build_f6_schedule(BASE_ALL_IN)
    fee = _f6_fee()
    warrant = _f6_warrant()
    flows = lender_cash_flows(
        schedule, fee, warrant, schedule.rows[-1].payment_date
    )

    fixture_flows = [
        (entry["date"]["value"], entry["cash_flow"]["value"])
        for entry in expected["lender_cash_flows_base"]
    ]
    engine_flows = [
        (flow.flow_date.isoformat(), flow.amount_cents) for flow in flows
    ]
    assert engine_flows == fixture_flows
    assert len(flows) == 6

    # F6 step 23: close flow = fee 1,000,000 minus facility 100,000,000.
    assert flows[0].flow_date == schedule.close == date(2026, 1, 5)
    assert flows[0].amount_cents == fee - schedule.facility_cents == -99_000_000

    # F6 step 25: final date combined, one flow of interest + principal + warrant.
    final_row = schedule.rows[-1]
    assert flows[-1].flow_date == final_row.payment_date == date(2026, 6, 5)
    assert flows[-1].amount_cents == (
        final_row.interest_cents + final_row.principal_cents + warrant
    )
    assert flows[-1].amount_cents == 594_521 + 100_000_000 + 300_000 == 100_894_521

    # Period receipts equal schedule interest (bullet principal is 0 until the end).
    for flow, row in zip(flows[1:5], schedule.rows[:4], strict=True):
        assert flow.flow_date == row.payment_date
        assert flow.amount_cents == row.interest_cents
        assert flow.amount_cents > 0


def test_f6_base_and_shocked_irr_match_fixture_with_npv_bracket() -> None:
    """Solve both IRRs and bracket them with engine npv (F6 steps 27 to 30).

    Each published IRR must equal the fixture value within abs 0.0001
    percent, and the sign of engine npv at published minus tolerance must be
    positive while published plus tolerance is negative, proving the root
    lies inside the tolerance window.
    """
    expected = F6["expected"]
    close = _f6_close()
    assert IRR_TOLERANCE == Decimal("0.0001")

    base_flows = _f6_flows(BASE_ALL_IN)
    shocked_flows = _f6_flows(SHOCKED_ALL_IN)

    base_result = solve_irr(base_flows, close, CFG.solver)
    shocked_result = solve_irr(shocked_flows, close, CFG.solver)
    assert base_result.status == shocked_result.status == "feasible"
    assert base_result.reason is None
    assert shocked_result.reason is None
    assert base_result.irr_percent is not None
    assert shocked_result.irr_percent is not None
    assert base_result.iterations > 0
    assert shocked_result.iterations > 0

    published_base = Decimal(expected["base_irr_percent"]["value"])
    published_shocked = Decimal(expected["shocked_irr_percent"]["value"])
    assert published_base == Decimal("10.6819")
    assert published_shocked == Decimal("12.9080")

    assert abs(base_result.irr_percent - published_base) <= IRR_TOLERANCE
    assert abs(shocked_result.irr_percent - published_shocked) <= IRR_TOLERANCE
    assert base_result.irr_percent.as_tuple().exponent == -4
    assert shocked_result.irr_percent.as_tuple().exponent == -4

    # NPV bracket: positive below the published root, negative above it.
    for published, flows, label in (
        (published_base, base_flows, "base"),
        (published_shocked, shocked_flows, "shocked"),
    ):
        below = npv(flows, close, (published - IRR_TOLERANCE) / 100)
        above = npv(flows, close, (published + IRR_TOLERANCE) / 100)
        assert below > 0, f"{label} npv at published minus tolerance not positive"
        assert above < 0, f"{label} npv at published plus tolerance not negative"


def test_f6_shock_delta_sensitivity_and_totals_match_fixture() -> None:
    """irr_delta_bp 222.6 and sensitivity_per_100bp 111.3 (F6 steps 31, 32).

    Delta is shocked minus base quantized once to bp scale 1; sensitivity
    divides that delta by the 200 bp shock over 100. Total interests for both
    schedules are exact integer sums pinned above and repeated here beside
    the shock figures they derive from.
    """
    expected = F6["expected"]
    inputs = F6["inputs"]

    base_value = Decimal(expected["base_irr_percent"]["value"])
    shocked_value = Decimal(expected["shocked_irr_percent"]["value"])
    delta = irr_delta_bp(base_value, shocked_value)
    assert delta == Decimal(expected["irr_delta_bp"]["value"]) == Decimal("222.6")
    assert delta.as_tuple().exponent == -1

    shock_bp = Decimal(str(inputs["rate_shock_bp"]["value"]))
    assert shock_bp == Decimal("200.0")
    sensitivity = sensitivity_per_100bp(delta, shock_bp)
    assert sensitivity == Decimal(
        expected["sensitivity_per_100bp_irr_bp"]["value"]
    ) == Decimal("111.3")
    assert sensitivity.as_tuple().exponent == -1

    base = _build_f6_schedule(BASE_ALL_IN)
    shocked = _build_f6_schedule(SHOCKED_ALL_IN)
    assert base.total_interest_cents == 2_895_891
    assert shocked.total_interest_cents == 3_723_288
    assert shocked.total_interest_cents > base.total_interest_cents


def test_f6_warrant_value_moic_and_granted_leaf_shape() -> None:
    """Warrant 300,000 cents, MOIC 1.03, granted leaf carries no status key.

    The warrant value formula (P3.4) recomputes from fixture inputs, the
    granted leaf shape is the fixture-pinned key set without a status key
    (the asymmetric counterpart of the not_granted leaf in F4 and F5), and
    MOIC is receipts (interest plus principal) plus warrant over the gross
    facility with the fee excluded.
    """
    expected = F6["expected"]
    warrant = _f6_warrant()
    assert warrant == 300_000

    leaf = expected["warrant_value"]
    assert "status" not in leaf
    assert sorted(leaf) == ["scale", "unit", "value"]
    assert leaf["value"] == 300_000

    schedule = _build_f6_schedule(BASE_ALL_IN)
    receipts = schedule.total_interest_cents + schedule.total_principal_cents
    assert receipts == 102_895_891
    published = returns_moic(receipts, warrant, schedule.facility_cents)
    assert published == Decimal(expected["moic"]["value"]) == Decimal("1.03")
    assert published.as_tuple().exponent == -2


def test_moic_closed_form_f2_and_schedule_returns_agreement() -> None:
    """F2 MOIC equals the closed form at scale 2; both helpers agree (P3.3).

    Direct Decimal division of (interest + principal + warrant) by the gross
    facility quantized once to scale 2 with ROUND_HALF_UP in a local context,
    compared against schedule.moic and the receipts-based returns.moic on the
    identical F2 inputs.
    """
    inputs = F2["inputs"]
    expected = F2["expected"]
    interest = expected["total_interest"]["value"]
    principal = expected["total_principal"]["value"]
    warrant = int(expected["warrant"]["value"])
    facility = int(inputs["facility"]["value"])
    assert interest == 2_673_972
    assert principal == 100_000_000
    assert warrant == 0
    assert facility == 100_000_000

    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_UP
        closed_form = (
            Decimal(interest + principal + warrant) / Decimal(facility)
        ).quantize(Decimal("0.01"))

    from_schedule = schedule_moic(interest, principal, warrant, facility)
    from_returns = returns_moic(interest + principal, warrant, facility)

    assert closed_form == Decimal(expected["moic"]["value"]) == Decimal("1.03")
    assert from_schedule == from_returns == closed_form
    assert from_schedule.as_tuple().exponent == -2
    assert from_returns.as_tuple().exponent == -2


def test_no_solution_for_all_positive_and_all_zero_flows() -> None:
    """No sign change and degenerate flow sets publish no_solution (P3.3).

    Both return status no_solution with a non-empty reason companion and no
    IRR value; the all zero set gets its own distinct reason, and neither
    solve runs a bisection iteration.
    """
    close = date(2026, 1, 5)

    all_positive = (
        DatedCashFlow(date(2026, 1, 5), 1_000),
        DatedCashFlow(date(2026, 2, 5), 2_000),
        DatedCashFlow(date(2026, 3, 5), 500),
    )
    positive_result = solve_irr(all_positive, close, CFG.solver)
    assert positive_result.status == "no_solution"
    assert positive_result.irr_percent is None
    assert positive_result.reason is not None
    assert positive_result.reason.strip() != ""
    assert positive_result.iterations == 0

    all_zero = (
        DatedCashFlow(date(2026, 1, 5), 0),
        DatedCashFlow(date(2026, 2, 5), 0),
    )
    zero_result = solve_irr(all_zero, close, CFG.solver)
    assert zero_result.status == "no_solution"
    assert zero_result.irr_percent is None
    assert zero_result.reason is not None
    assert zero_result.reason.strip() != ""
    assert zero_result.reason != positive_result.reason
    assert zero_result.iterations == 0


def test_solve_irr_raises_solver_error_when_max_iterations_is_one() -> None:
    """Exceeding max_iterations is a hard SolverError printing the bracket.

    A params copy with max_iterations set to 1 cannot finish the F6 base
    solve (which needs about 24 iterations), so solve_irr raises
    vdebt.sizing.SolverError with the bracket in the message instead of
    falling back silently.
    """
    flows = _f6_flows(BASE_ALL_IN)
    tight_params = replace(CFG.solver, max_iterations=1)
    assert tight_params.max_iterations == 1

    with pytest.raises(SolverError) as excinfo:
        solve_irr(flows, _f6_close(), tight_params)
    message = str(excinfo.value)
    assert "bracket" in message
    assert "1 iterations" in message
    # The printed bracket is the live bisection bracket, not the full range.
    assert "-0.99" in message


def test_warrant_zero_coverage_and_hand_product() -> None:
    """Zero coverage returns 0; hand product check at coverage 2.50 (P3.4).

    warrant_value_cents itself returns 0 for zero coverage (the caller
    publishes the not_granted companion, see the F4 and F5 leaf tests). The
    hand product takes coverage 2.50 percent of a 400,000,000 cent facility
    times the 0.15 factor in a local Decimal context with a single quantize,
    independent of returns.py.
    """
    zero = warrant_value_cents(Decimal("0.00"), 100_000_000, Decimal("0.15"))
    assert zero == 0
    zero_from_zero_factor = warrant_value_cents(Decimal("2.50"), 400_000_000, Decimal("0.00"))
    assert zero_from_zero_factor == 0

    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_UP
        raw = (
            Decimal("2.50") / Decimal(100) * Decimal(400_000_000) * Decimal("0.15")
        )
        hand = int(raw.quantize(Decimal(1)))

    engine = warrant_value_cents(Decimal("2.50"), 400_000_000, Decimal("0.15"))
    assert hand == 1_500_000
    assert engine == hand


def test_close_flow_is_fee_minus_facility_for_all_structures() -> None:
    """The close cash flow is fee minus facility for all four structures.

    Covers bullet and IO-then-straight-line (amortizing), bridge, and RBF:
    the closing fee enters the IRR flows at close only, dated at schedule
    close, for every structure (P3.3 fee treatment).
    """
    amortizing = build_schedule(
        structure="bullet",
        facility_cents=100_000_000,
        close=date(2026, 3, 31),
        term_months=4,
        io_months=0,
        all_in_rate=Decimal("8.0000"),
    )
    io_schedule = build_schedule(
        structure="io_then_straight_line",
        facility_cents=30_000_000,
        close=date(2026, 7, 31),
        term_months=6,
        io_months=3,
        all_in_rate=Decimal("8.0000"),
    )
    bridge = build_bridge_schedule(
        facility_cents=50_000_000,
        close=date(2026, 2, 27),
        tenor_months=3,
        all_in_rate=Decimal("12.0000"),
    )
    rbf = build_rbf_schedule(
        advance_cents=60_000_000,
        close=date(2026, 5, 1),
        horizon=3,
        remittance_rate_pct=Decimal("5.00"),
        cap_multiple=Decimal("1.50"),
        revenue_cents=(400_000_000, 400_000_000, 400_000_000),
    )

    cases = (
        (amortizing, 100_000_000, Decimal("1.00")),
        (io_schedule, 30_000_000, Decimal("1.00")),
        (bridge, 50_000_000, Decimal("2.00")),
        (rbf, 60_000_000, Decimal("1.00")),
    )
    for schedule, facility_cents, fee_pct in cases:
        fee = closing_fee_cents(facility_cents, fee_pct)
        flows = lender_cash_flows(
            schedule, fee, 0, schedule.rows[-1].payment_date
        )
        assert flows[0].flow_date == schedule.close
        assert flows[0].amount_cents == fee - facility_cents
        assert flows[0].amount_cents < 0
        assert flows[0].amount_cents == -(facility_cents - fee)

    # Pin the per-structure fee amounts used above.
    assert closing_fee_cents(100_000_000, Decimal("1.00")) == 1_000_000
    assert closing_fee_cents(30_000_000, Decimal("1.00")) == 300_000
    assert closing_fee_cents(50_000_000, Decimal("2.00")) == 1_000_000
    assert closing_fee_cents(60_000_000, Decimal("1.00")) == 600_000
