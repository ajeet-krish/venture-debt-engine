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

from vdebt.schedule import Schedule, build_schedule, closing_fee_cents, moic

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
