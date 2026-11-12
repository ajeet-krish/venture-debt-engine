"""Tests for vdebt.schedule: F2 and F3 golden fixture recomputes.

Covers architecture P2.4 and the binding test_schedule entry in the P2.7
test plan. The bullet fixture (F2) and the IO-then-straight-line fixture
(F3) are rebuilt from engine code using the committed fixture inputs and
compared field by field against the committed expected values: payment
dates as ISO strings, every row (period, payment_date, opening, interest,
principal, closing), totals, closing fee via closing_fee_cents, MOIC via
the moic helper, and the warrant companion (value 0, status not_granted).
Also covered: payment dates strictly increasing and business days in both
schedules, final closing balance 0 in both, NotImplementedError for the
phase 3 structures rbf and bridge, an independent ACT/365 hand
recomputation of an F3 interest row, the ValueError guards for an empty
term and a closing balance driven negative, and the recorded phase 1
decision that MOIC excludes the closing fee.

Every fixture value is read from the committed JSON; nothing here
regenerates a fixture. The hand interest check writes its own Decimal
expression under an explicit local context instead of calling the engine
money or dates helpers, so the spot check is independent of
schedule.py.
"""

import itertools
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path

import pytest

from vdebt.schedule import (
    RbfSchedule,
    Schedule,
    build_bridge_schedule,
    build_rbf_schedule,
    build_schedule,
    closing_fee_cents,
    moic,
)

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "golden"

F2 = json.loads((GOLDEN_DIR / "schedule_bullet.json").read_text(encoding="utf-8"))
F3 = json.loads((GOLDEN_DIR / "schedule_io_amort.json").read_text(encoding="utf-8"))


def _build_f2() -> Schedule:
    """Rebuild fixture F2: bullet, close 2026-03-31, term 4, rate from parts."""
    inputs = F2["inputs"]
    rate = Decimal(str(inputs["base_rate"]["value"])) + Decimal(
        str(inputs["spread"]["value"])
    )
    return build_schedule(
        structure="bullet",
        facility_cents=int(inputs["facility"]["value"]),
        close=date.fromisoformat(inputs["close"]["value"]),
        term_months=int(Decimal(str(inputs["term"]["value"]))),
        io_months=0,
        all_in_rate=rate,
    )


def _build_f3() -> Schedule:
    """Rebuild fixture F3: IO then straight line, io 3 of term 6."""
    inputs = F3["inputs"]
    return build_schedule(
        structure="io_then_straight_line",
        facility_cents=int(inputs["facility"]["value"]),
        close=date.fromisoformat(inputs["close"]["value"]),
        term_months=int(inputs["term"]["value"]),
        io_months=int(inputs["io"]["value"]),
        all_in_rate=Decimal(str(inputs["all_in_rate"]["value"])),
    )


def test_f2_bullet_recompute_matches_fixture_field_by_field() -> None:
    schedule = _build_f2()
    expected = F2["expected"]
    inputs = F2["inputs"]

    assert schedule.structure == "bullet"
    assert schedule.facility_cents == inputs["facility"]["value"] == 100_000_000
    assert schedule.close == date(2026, 3, 31)
    # Rate derived from the fixture parts: base 4.0000 + spread 4.0000.
    assert schedule.all_in_rate == Decimal(expected["all_in_rate"]["value"])
    assert schedule.all_in_rate == Decimal("8.0000")
    assert schedule.all_in_rate.as_tuple().exponent == -4

    dates = [row.payment_date.isoformat() for row in schedule.rows]
    expected_dates = [entry["value"] for entry in expected["payment_dates"]]
    assert dates == expected_dates
    assert dates == ["2026-04-30", "2026-06-01", "2026-06-30", "2026-07-31"]

    fixture_rows = expected["schedule"]
    assert len(schedule.rows) == len(fixture_rows) == 4
    for row, exp in zip(schedule.rows, fixture_rows, strict=True):
        assert row.period == exp["period"]["value"]
        assert row.payment_date.isoformat() == exp["payment_date"]["value"]
        assert row.opening_cents == exp["opening_balance"]["value"]
        assert row.interest_cents == exp["interest"]["value"]
        assert row.principal_cents == exp["principal"]["value"]
        assert row.closing_cents == exp["closing_balance"]["value"]

    # Totals are exact integer sums of the quantized rows.
    assert schedule.total_interest_cents == expected["total_interest"]["value"]
    assert schedule.total_principal_cents == expected["total_principal"]["value"]
    assert schedule.total_interest_cents == 2_673_972
    assert schedule.total_principal_cents == 100_000_000
    assert sum(row.interest_cents for row in schedule.rows) == (
        schedule.total_interest_cents
    )
    assert sum(row.principal_cents for row in schedule.rows) == (
        schedule.total_principal_cents
    )

    fee_pct = Decimal(str(inputs["closing_fee_pct"]["value"]))
    assert fee_pct == Decimal("1.00")
    assert closing_fee_cents(schedule.facility_cents, fee_pct) == (
        expected["closing_fee"]["value"]
    )

    # Warrant companion: value 0 with status not_granted, never missing.
    warrant = expected["warrant"]
    assert warrant["value"] == 0
    assert warrant["status"] == "not_granted"
    published = moic(
        schedule.total_interest_cents,
        schedule.total_principal_cents,
        int(warrant["value"]),
        schedule.facility_cents,
    )
    assert published == Decimal(expected["moic"]["value"])
    assert str(published) == "1.03"
    assert published.as_tuple().exponent == -2


def test_f3_io_straight_line_recompute_matches_fixture_field_by_field() -> None:
    schedule = _build_f3()
    expected = F3["expected"]
    inputs = F3["inputs"]

    assert schedule.structure == "io_then_straight_line"
    assert schedule.facility_cents == inputs["facility"]["value"] == 30_000_000
    assert schedule.close == date(2026, 7, 31)
    assert schedule.all_in_rate == Decimal("8.0000")

    dates = [row.payment_date.isoformat() for row in schedule.rows]
    expected_dates = [entry["value"] for entry in expected["payment_dates"]]
    assert dates == expected_dates
    assert dates == [
        "2026-08-31",
        "2026-09-30",
        "2026-11-02",
        "2026-11-30",
        "2026-12-31",
        "2027-02-01",
    ]

    fixture_rows = expected["schedule"]
    assert len(schedule.rows) == len(fixture_rows) == 6
    for row, exp in zip(schedule.rows, fixture_rows, strict=True):
        assert row.period == exp["period"]["value"]
        assert row.payment_date.isoformat() == exp["payment_date"]["value"]
        assert row.opening_cents == exp["opening_balance"]["value"]
        assert row.interest_cents == exp["interest"]["value"]
        assert row.principal_cents == exp["principal"]["value"]
        assert row.closing_cents == exp["closing_balance"]["value"]

    # io 3 of term 6: periods 1 to 3 interest only, then the
    # allocate_cents split over the 3 amortizing periods (10,000,000 each).
    principals = [row.principal_cents for row in schedule.rows]
    assert principals[:3] == [0, 0, 0]
    assert principals[3:] == [10_000_000, 10_000_000, 10_000_000]

    assert schedule.total_interest_cents == expected["total_interest"]["value"]
    assert schedule.total_principal_cents == expected["total_principal"]["value"]
    assert schedule.total_interest_cents == 1_008_219
    assert schedule.total_principal_cents == 30_000_000
    assert sum(row.interest_cents for row in schedule.rows) == (
        schedule.total_interest_cents
    )
    assert sum(row.principal_cents for row in schedule.rows) == (
        schedule.total_principal_cents
    )

    fee_pct = Decimal(str(inputs["closing_fee_pct"]["value"]))
    assert closing_fee_cents(schedule.facility_cents, fee_pct) == (
        expected["closing_fee"]["value"]
    )
    assert expected["closing_fee"]["value"] == 300_000

    warrant = expected["warrant"]
    assert warrant["value"] == 0
    assert warrant["status"] == "not_granted"
    published = moic(
        schedule.total_interest_cents,
        schedule.total_principal_cents,
        int(warrant["value"]),
        schedule.facility_cents,
    )
    assert published == Decimal(expected["moic"]["value"])
    assert str(published) == "1.03"
    assert published.as_tuple().exponent == -2


def test_payment_dates_strictly_increasing_and_business_days() -> None:
    for schedule in (_build_f2(), _build_f3()):
        dates = [row.payment_date for row in schedule.rows]
        assert len(dates) >= 2
        for earlier, later in itertools.pairwise(dates):
            assert later > earlier, f"dates not strictly increasing: {dates}"
        for d in dates:
            # Local weekday check: business days are Monday to Friday.
            assert d.weekday() < 5, f"{d.isoformat()} is not a business day"


def test_final_closing_balance_is_zero_in_both_schedules() -> None:
    for schedule in (_build_f2(), _build_f3()):
        assert schedule.rows[-1].closing_cents == 0
        assert all(row.closing_cents >= 0 for row in schedule.rows)


@pytest.mark.parametrize("structure", ["rbf", "bridge"])
def test_phase_3_structures_raise_not_implemented(structure: str) -> None:
    with pytest.raises(NotImplementedError) as excinfo:
        build_schedule(
            structure=structure,
            facility_cents=100_000_000,
            close=date(2026, 3, 31),
            term_months=4,
            io_months=0,
            all_in_rate=Decimal("8.0000"),
        )
    message = str(excinfo.value)
    assert "build_rbf_schedule" in message and "build_bridge_schedule" in message


def test_f3_period_1_interest_hand_recompute_act365() -> None:
    """F3 arithmetic step 10 recomputed by hand, independent of the engine.

    interest = 0.08 x 30,000,000 cents x 31/365 quantized once to cents
    with ROUND_HALF_UP, written as a local Decimal expression under an
    explicit local context (no engine money or dates helpers).
    """
    fixture_row = F3["expected"]["schedule"][0]
    facility_cents = 30_000_000
    days = 31  # span 2026-07-31 to 2026-08-31 per F3 arithmetic step 10
    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_UP
        raw = Decimal("0.08") * Decimal(facility_cents) * (
            Decimal(days) / Decimal(365)
        )
        hand_interest = int(raw.quantize(Decimal(1)))

    schedule = _build_f3()
    row = schedule.rows[0]
    assert (row.payment_date - schedule.close).days == days
    assert hand_interest == fixture_row["interest"]["value"] == 203_836
    assert row.interest_cents == hand_interest


def test_empty_term_raises_value_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        build_schedule(
            structure="bullet",
            facility_cents=100_000_000,
            close=date(2026, 3, 31),
            term_months=0,
            io_months=0,
            all_in_rate=Decimal("8.0000"),
        )
    assert "term_months must be >= 1" in str(excinfo.value)


def test_closing_balance_guard_rejects_negative_facility() -> None:
    """The closing never goes negative guard is engine-asserted.

    With any positive facility the principal plan ties out to the opening
    balance by construction (bullet repays exactly the facility in the last
    period, allocate_cents splits it exactly), so closing can never go
    negative through the public API. A negative facility makes period 1
    close below zero and reaches the guard directly.
    """
    with pytest.raises(ValueError) as excinfo:
        build_schedule(
            structure="bullet",
            facility_cents=-100_000_000,
            close=date(2026, 3, 31),
            term_months=4,
            io_months=0,
            all_in_rate=Decimal("8.0000"),
        )
    assert "closing balance went negative" in str(excinfo.value)


def test_moic_excludes_closing_fee() -> None:
    """MOIC numerator is interest plus principal plus warrant, no fee.

    F2 numbers make the distinction visible: including the 1,000,000 cent
    closing fee would push the published value from 1.03 to 1.04.
    """
    expected = F2["expected"]
    interest = expected["total_interest"]["value"]
    principal = expected["total_principal"]["value"]
    facility = F2["inputs"]["facility"]["value"]
    fee = expected["closing_fee"]["value"]
    warrant = expected["warrant"]["value"]
    assert fee == 1_000_000

    published = moic(interest, principal, warrant, facility)
    numerator = Decimal(interest + principal + warrant)
    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_UP
        without_fee = (numerator / Decimal(facility)).quantize(Decimal("0.01"))
        with_fee = (
            (numerator + Decimal(fee)) / Decimal(facility)
        ).quantize(Decimal("0.01"))

    assert published == without_fee == Decimal("1.03")
    assert str(published) == expected["moic"]["value"]
    assert with_fee == Decimal("1.04")
    assert published != with_fee


# Phase 3 additions (architecture P3.1, P3.2, and the binding test_schedule
# entry in P3.8). Everything below appends to the phase 2 suite above and
# changes none of it: F4 RBF recompute for both cases, the synthetic early
# cap case, RBF build invariants, F5 bridge recompute, and the end-of-month
# bridge maturity clamp and roll.

F4 = json.loads((GOLDEN_DIR / "schedule_rbf.json").read_text(encoding="utf-8"))
F5 = json.loads((GOLDEN_DIR / "bridge_fixture.json").read_text(encoding="utf-8"))


def _build_f4_case(case: dict) -> RbfSchedule:
    """Rebuild one F4 case from the committed fixture inputs (P3.1).

    Advance 60,000,000 cents, close 2026-05-01, remittance rate 5.00 percent,
    cap multiple 1.50 (cap 90,000,000 cents), with the per-case horizon and
    revenue array taken from the case inputs.
    """
    inputs = F4["inputs"]
    case_inputs = case["inputs"]
    return build_rbf_schedule(
        advance_cents=int(inputs["advance"]["value"]),
        close=date.fromisoformat(inputs["close"]["value"]),
        horizon=int(case_inputs["horizon"]["value"]),
        remittance_rate_pct=Decimal(str(inputs["remittance_rate_pct"]["value"])),
        cap_multiple=Decimal(str(inputs["cap_multiple"]["value"])),
        revenue_cents=tuple(entry["value"] for entry in case_inputs["revenue"]),
    )


def test_f4_cap_hit_recompute_matches_fixture_field_by_field() -> None:
    """F4 case cap_hit rebuilt from engine code, field by field (P3.1).

    Payment dates, every row (revenue, remittance, cumulative),
    final_cumulative, residual, status, and the warrant not_granted leaf are
    compared to the committed fixture by exact string or int equality.
    """
    case = next(c for c in F4["cases"] if c["case_id"] == "cap_hit")
    schedule = _build_f4_case(case)
    expected = case["expected"]

    assert schedule.close == date(2026, 5, 1)
    assert schedule.advance_cents == 60_000_000
    assert schedule.remittance_rate_pct == Decimal("5.00")
    assert schedule.cap_multiple == Decimal("1.50")
    assert schedule.horizon == 5
    # F4 step 2: 1.50 x 60,000,000 = 90,000,000 cents, quantized once.
    assert schedule.cap_cents == 90_000_000

    dates = [row.payment_date.isoformat() for row in schedule.rows]
    expected_dates = [entry["value"] for entry in expected["payment_dates"]]
    assert dates == expected_dates
    assert dates == [
        "2026-06-01",
        "2026-07-01",
        "2026-08-03",
        "2026-09-01",
        "2026-10-01",
    ]

    fixture_rows = expected["periods"]
    assert len(schedule.rows) == len(fixture_rows) == 5
    for row, exp in zip(schedule.rows, fixture_rows, strict=True):
        assert row.period == exp["period"]["value"]
        assert row.payment_date.isoformat() == exp["payment_date"]["value"]
        assert row.revenue_cents == exp["revenue"]["value"]
        assert row.remittance_cents == exp["remittance"]["value"]
        assert row.cumulative_cents == exp["cumulative"]["value"]

    assert schedule.final_cumulative_cents == expected["final_cumulative"]["value"]
    assert schedule.final_cumulative_cents == 90_000_000
    assert schedule.residual_cents == expected["residual"]["value"] == 0
    assert schedule.status == expected["status"]["value"] == "feasible"
    assert sum(row.remittance_cents for row in schedule.rows) == (
        schedule.final_cumulative_cents
    )

    # Warrant not_granted leaf: fixture-pinned key set (P3.4 shape).
    warrant = expected["warrant"]
    assert warrant == {
        "value": 0,
        "unit": "usd_minor_units",
        "scale": 0,
        "status": "not_granted",
    }


def test_f4_shortfall_recompute_matches_fixture_field_by_field() -> None:
    """F4 case shortfall rebuilt from engine code, field by field (P3.1).

    The residual travels twice: as the standalone residual leaf and as the
    residual companion inside the status envelope (section 6.4 matrix). Both
    must repeat the same 75,000,000 cents.
    """
    case = next(c for c in F4["cases"] if c["case_id"] == "shortfall")
    schedule = _build_f4_case(case)
    expected = case["expected"]

    assert schedule.close == date(2026, 5, 1)
    assert schedule.advance_cents == 60_000_000
    assert schedule.cap_cents == 90_000_000
    assert schedule.horizon == 3

    dates = [row.payment_date.isoformat() for row in schedule.rows]
    expected_dates = [entry["value"] for entry in expected["payment_dates"]]
    assert dates == expected_dates
    assert dates == ["2026-06-01", "2026-07-01", "2026-08-03"]

    fixture_rows = expected["periods"]
    assert len(schedule.rows) == len(fixture_rows) == 3
    for row, exp in zip(schedule.rows, fixture_rows, strict=True):
        assert row.period == exp["period"]["value"]
        assert row.payment_date.isoformat() == exp["payment_date"]["value"]
        assert row.revenue_cents == exp["revenue"]["value"]
        assert row.remittance_cents == exp["remittance"]["value"]
        assert row.cumulative_cents == exp["cumulative"]["value"]

    assert schedule.final_cumulative_cents == expected["final_cumulative"]["value"]
    assert schedule.final_cumulative_cents == 15_000_000
    assert schedule.residual_cents == expected["residual"]["value"] == 75_000_000

    status_leaf = expected["status"]
    assert status_leaf["value"] == "shortfall"
    # Residual companion inside the status envelope (F4 step 19).
    assert status_leaf["residual"]["value"] == 75_000_000
    assert schedule.status == "shortfall"
    assert schedule.residual_cents == status_leaf["residual"]["value"]
    assert schedule.residual_cents == expected["residual"]["value"]

    warrant = expected["warrant"]
    assert warrant == {
        "value": 0,
        "unit": "usd_minor_units",
        "scale": 0,
        "status": "not_granted",
    }


def test_rbf_synthetic_cap_hit_before_horizon_stops_rows() -> None:
    """Rows stop at the cap-hit period with no trailing rows (P3.1 stop rule).

    Synthetic revenue of 600,000,000 cents per period at 5.00 percent gives a
    30,000,000 cent nominal remittance per period, so the 90,000,000 cent cap
    is reached exactly in period 3 of a 6 period horizon. The row list must
    end there with residual 0 and status feasible, never carrying trailing
    rows for periods 4 to 6.
    """
    horizon = 6
    revenue = (600_000_000,) * horizon
    schedule = build_rbf_schedule(
        advance_cents=60_000_000,
        close=date(2026, 5, 1),
        horizon=horizon,
        remittance_rate_pct=Decimal("5.00"),
        cap_multiple=Decimal("1.50"),
        revenue_cents=revenue,
    )
    assert schedule.horizon == horizon
    assert [row.period for row in schedule.rows] == [1, 2, 3]
    assert len(schedule.rows) == 3
    assert [row.remittance_cents for row in schedule.rows] == [
        30_000_000,
        30_000_000,
        30_000_000,
    ]
    assert [row.revenue_cents for row in schedule.rows] == [600_000_000] * 3
    assert schedule.rows[-1].cumulative_cents == schedule.cap_cents == 90_000_000
    assert schedule.final_cumulative_cents == 90_000_000
    assert schedule.residual_cents == 0
    assert schedule.status == "feasible"


def test_rbf_invariants_remittance_residual_cap_and_dates() -> None:
    """Build-time invariants from P3.1 hold on both F4 cases plus one cap case.

    Per row: the remittance never exceeds the residual still owed to the cap
    before it, the cumulative never exceeds the cap, and the cumulative equals
    the running integer sum of remittances. Dates are strictly increasing, and
    the residual plus the final cumulative always reconstruct the cap.
    """
    synthetic = build_rbf_schedule(
        advance_cents=60_000_000,
        close=date(2026, 5, 1),
        horizon=6,
        remittance_rate_pct=Decimal("5.00"),
        cap_multiple=Decimal("1.50"),
        revenue_cents=(600_000_000,) * 6,
    )
    schedules = [_build_f4_case(case) for case in F4["cases"]] + [synthetic]
    assert len(schedules) == 3

    for schedule in schedules:
        running = 0
        dates: list[date] = []
        for row in schedule.rows:
            residual_before = schedule.cap_cents - running
            assert 0 <= row.remittance_cents <= residual_before
            running += row.remittance_cents
            assert row.cumulative_cents == running
            assert row.cumulative_cents <= schedule.cap_cents
            dates.append(row.payment_date)
        assert schedule.final_cumulative_cents == running
        assert schedule.residual_cents == schedule.cap_cents - running
        assert 0 <= schedule.residual_cents <= schedule.cap_cents
        assert schedule.final_cumulative_cents + schedule.residual_cents == (
            schedule.cap_cents
        )
        for earlier, later in itertools.pairwise(dates):
            assert later > earlier, f"dates not strictly increasing: {dates}"


def test_rbf_pre_revenue_zero_remitances_and_full_residual_shortfall() -> None:
    """Pre-revenue borrowers feed all-zero revenue and end in shortfall (P3.1).

    Every remittance is a real 0 cents (never a fabricated number), the final
    cumulative is 0, and the residual equals the whole cap with status
    shortfall.
    """
    schedule = build_rbf_schedule(
        advance_cents=60_000_000,
        close=date(2026, 5, 1),
        horizon=4,
        remittance_rate_pct=Decimal("5.00"),
        cap_multiple=Decimal("1.50"),
        revenue_cents=(0, 0, 0, 0),
    )
    assert len(schedule.rows) == 4
    assert [row.revenue_cents for row in schedule.rows] == [0, 0, 0, 0]
    assert [row.remittance_cents for row in schedule.rows] == [0, 0, 0, 0]
    assert schedule.final_cumulative_cents == 0
    assert schedule.cap_cents == 90_000_000
    assert schedule.residual_cents == schedule.cap_cents == 90_000_000
    assert schedule.status == "shortfall"


def test_rbf_revenue_length_mismatch_raises_value_error() -> None:
    """A revenue tuple shorter or longer than the horizon is rejected (P3.1)."""
    with pytest.raises(ValueError) as excinfo:
        build_rbf_schedule(
            advance_cents=60_000_000,
            close=date(2026, 5, 1),
            horizon=5,
            remittance_rate_pct=Decimal("5.00"),
            cap_multiple=Decimal("1.50"),
            revenue_cents=(400_000_000, 400_000_000),
        )
    message = str(excinfo.value)
    assert "revenue_cents length 2 does not equal horizon 5" in message


def test_f5_bridge_recompute_matches_fixture_field_by_field() -> None:
    """F5 bridge rebuilt from fixture inputs, field by field (P3.2).

    Single row shape (period 1 at maturity, opening facility, accrued
    interest, principal, closing 0), maturity date, interest 1,463,014,
    total repayment 51,463,014 as the integer sum of the row values, closing
    fee via closing_fee_cents, MOIC 1.03, and the warrant not_granted leaf.
    """
    inputs = F5["inputs"]
    expected = F5["expected"]
    schedule = build_bridge_schedule(
        facility_cents=int(inputs["facility"]["value"]),
        close=date.fromisoformat(inputs["close"]["value"]),
        tenor_months=int(Decimal(str(inputs["tenor"]["value"]))),
        all_in_rate=Decimal(str(inputs["all_in_rate"]["value"])),
    )

    assert schedule.structure == "bridge"
    assert schedule.facility_cents == 50_000_000
    assert schedule.close == date(2026, 2, 27)
    assert schedule.all_in_rate == Decimal("12.0000")
    assert schedule.all_in_rate.as_tuple().exponent == -4

    # Single repayment row: exactly one row, period 1 at maturity.
    assert len(schedule.rows) == 1
    row = schedule.rows[0]
    assert row.period == 1
    maturity = row.payment_date
    assert maturity.isoformat() == expected["maturity_date"]["value"] == "2026-05-27"
    # F5 step 2: the span runs 89 days, ACT/365 documents the accrual.
    assert (maturity - schedule.close).days == 89
    assert row.opening_cents == 50_000_000
    assert row.interest_cents == expected["interest"]["value"] == 1_463_014
    assert row.principal_cents == 50_000_000
    assert row.closing_cents == 0

    # F5 step 4: total repayment is the exact integer sum of the row values.
    total_repayment = row.interest_cents + row.principal_cents
    assert total_repayment == expected["total_repayment"]["value"] == 51_463_014
    assert schedule.total_interest_cents == 1_463_014
    assert schedule.total_principal_cents == 50_000_000
    assert row.interest_cents + row.principal_cents == total_repayment

    fee_pct = Decimal(str(inputs["closing_fee_pct"]["value"]))
    assert fee_pct == Decimal("2.00")
    assert closing_fee_cents(schedule.facility_cents, fee_pct) == (
        expected["closing_fee"]["value"]
    )
    assert expected["closing_fee"]["value"] == 1_000_000

    warrant = expected["warrant"]
    assert warrant == {
        "value": 0,
        "unit": "usd_minor_units",
        "scale": 0,
        "status": "not_granted",
    }
    published = moic(
        schedule.total_interest_cents,
        schedule.total_principal_cents,
        int(warrant["value"]),
        schedule.facility_cents,
    )
    assert published == Decimal(expected["moic"]["value"]) == Decimal("1.03")
    assert published.as_tuple().exponent == -2


def test_bridge_end_of_month_close_clamps_and_rolls_maturity() -> None:
    """Jan 31 close, tenor 1: maturity clamps to month end then rolls (P3.2).

    The section 12 rule preserves the close day of month, clamps to the
    target month's last day, then rolls forward only. Jan 31 2026 falls on a
    Saturday and is not a legal close, so the two legal Jan 31 business day
    closes are used: 2025 (Friday, non-leap, target Feb 28 already a business
    day) and 2048 (Friday, leap year, target Feb 29 Saturday rolls forward).
    """
    # Premise checks so the chosen years cannot drift silently.
    assert date(2025, 1, 31).weekday() == 4  # Friday, a business day
    assert date(2048, 1, 31).weekday() == 4  # Friday, a business day
    assert date(2026, 1, 31).weekday() == 5  # Saturday, not a legal close
    assert date(2025, 2, 28).weekday() == 4  # Friday, already a business day
    assert date(2048, 2, 29).weekday() == 5  # Saturday, needs a roll

    # Non-leap clamp: Jan 31 plus 1 month clamps to Feb 28, no roll needed.
    early = build_bridge_schedule(
        facility_cents=50_000_000,
        close=date(2025, 1, 31),
        tenor_months=1,
        all_in_rate=Decimal("12.0000"),
    )
    assert len(early.rows) == 1
    early_maturity = early.rows[0].payment_date
    assert early_maturity == date(2025, 2, 28)
    assert early_maturity.weekday() < 5  # roll_forward left it unchanged

    # Leap-year clamp plus roll: Jan 31 clamps to Sat Feb 29, rolls to Mon.
    leap = build_bridge_schedule(
        facility_cents=50_000_000,
        close=date(2048, 1, 31),
        tenor_months=1,
        all_in_rate=Decimal("12.0000"),
    )
    assert len(leap.rows) == 1
    leap_maturity = leap.rows[0].payment_date
    assert leap_maturity == date(2048, 3, 2)
    assert leap_maturity.weekday() == 0  # Monday, a business day
    # Roll moves forward only, never backward.
    assert leap_maturity > date(2048, 2, 29)
