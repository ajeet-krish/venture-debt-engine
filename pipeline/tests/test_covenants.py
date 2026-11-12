"""Tests for vdebt.covenants: headroom signs, cash path, applicability, order.

Covers the binding test_covenants entry in the architecture P4.8 test plan
against the P4.1 pin. Every expected figure below is hand arithmetic from the
pin (limit minus actual for max types, actual minus limit for min types, the
cash path recurrence, the burn floor multiply quantized once, net debt as
closing balance minus cash, cfads as the EBITDA proxy), written before the
engine ran, so no expectation is copied from engine output.

Also covered: the pinned not_applicable reason strings verbatim, excluded
zero-service periods publishing no DSCR row, first breach month and minimum
headroom on synthetic paths, exact integer cent min cash headroom, package
output order regardless of input order with unknown and missing name
rejections, and validate_payload accepting the breach status member (marked
xfail until the enum lands in phase 4 slice 4).
"""

from datetime import date
from decimal import Decimal

import pytest

from vdebt.covenants import (
    CovenantResult,
    CovenantSpec,
    burn_floor_cents,
    cash_path,
    evaluate_covenant,
    evaluate_package,
)
from vdebt.snapshot import envelope, validate_payload

CITATION = "stated covenant assumption (accessed 2026-11-12)"
DATE_1 = date(2026, 12, 1)
DATE_2 = date(2027, 1, 1)
DATE_3 = date(2027, 2, 1)
DATE_4 = date(2027, 3, 1)
DATES_2 = (DATE_1, DATE_2)
DATES_3 = (DATE_1, DATE_2, DATE_3)
DATES_4 = (DATE_1, DATE_2, DATE_3, DATE_4)


def _net_leverage(limit: str = "3.0000") -> CovenantSpec:
    return CovenantSpec("net_leverage", "max", Decimal(limit), "ratio", CITATION, None)


def _interest_cover(limit: str = "2.0000") -> CovenantSpec:
    return CovenantSpec("interest_cover", "min", Decimal(limit), "ratio", CITATION, None)


def _min_cash(limit: int = 500_000) -> CovenantSpec:
    return CovenantSpec("min_cash", "min", limit, "usd_minor_units", CITATION, None)


def _dscr_floor(limit: str = "1.1500") -> CovenantSpec:
    return CovenantSpec(
        "dscr_floor", "min", Decimal(limit), "ratio", CITATION, "dscr_floor_reference"
    )


def _evaluate(
    spec: CovenantSpec,
    *,
    ebitda: tuple[int, ...],
    dates: tuple[date, ...],
    net_debt: tuple[int, ...] | None = None,
    cash: tuple[int, ...] | None = None,
    cash_interest: tuple[int, ...] | None = None,
    debt_service: tuple[int, ...] | None = None,
) -> CovenantResult:
    """Evaluate spec over dates, defaulting unused paths to zero cents."""
    zeros = (0,) * len(dates)
    return evaluate_covenant(
        spec,
        ebitda=ebitda,
        net_debt=zeros if net_debt is None else net_debt,
        cash=zeros if cash is None else cash,
        cash_interest=cash_interest,
        debt_service=debt_service,
        dates=dates,
    )


def test_max_headroom_is_limit_minus_actual():
    # Hand: net debt 400,000 / EBITDA 100,000 = 4.0000; limit 3.0000 minus
    # actual 4.0000 = -1.0000, so period 1 breaches.
    result = _evaluate(
        _net_leverage("3.0000"),
        ebitda=(100_000,),
        dates=(DATE_1,),
        net_debt=(400_000,),
        cash=(250_000,),
    )
    row = result.periods[0]
    assert row.actual == Decimal("4.0000")
    assert row.headroom == Decimal("-1.0000")
    assert row.status == "breach"
    assert result.status == "breach"
    assert result.first_breach_month == 1
    assert result.min_headroom == Decimal("-1.0000")


def test_min_headroom_is_actual_minus_limit():
    # Hand: EBITDA 150,000 / cash interest 100,000 = 1.5000; actual 1.5000
    # minus limit 2.0000 = -0.5000, so period 1 breaches.
    result = _evaluate(
        _interest_cover("2.0000"),
        ebitda=(150_000,),
        dates=(DATE_1,),
        cash_interest=(100_000,),
    )
    row = result.periods[0]
    assert row.actual == Decimal("1.5000")
    assert row.headroom == Decimal("-0.5000")
    assert row.status == "breach"
    assert result.status == "breach"
    assert result.first_breach_month == 1


def test_equality_is_feasible_with_zero_headroom():
    # Hand max: 300,000 / 100,000 = 3.0000 equals the 3.0000 limit.
    max_result = _evaluate(
        _net_leverage("3.0000"),
        ebitda=(100_000,),
        dates=(DATE_1,),
        net_debt=(300_000,),
    )
    max_row = max_result.periods[0]
    assert max_row.headroom == Decimal("0.0000")
    assert max_row.headroom.as_tuple().exponent == -4
    assert max_row.status == "feasible"
    assert max_result.status == "feasible"
    assert max_result.first_breach_month is None
    # Hand min: 200,000 / 100,000 = 2.0000 equals the 2.0000 limit.
    min_result = _evaluate(
        _interest_cover("2.0000"),
        ebitda=(200_000,),
        dates=(DATE_1,),
        cash_interest=(100_000,),
    )
    min_row = min_result.periods[0]
    assert min_row.actual == Decimal("2.0000")
    assert min_row.headroom == Decimal("0.0000")
    assert min_row.status == "feasible"
    assert min_result.status == "feasible"
    assert min_result.first_breach_month is None


def test_cash_path_recurrence_hand_case():
    # Hand: 1,000,000 minus 100,000 minus 50,000 = 850,000; 850,000 minus
    # 100,000 minus 60,000 = 690,000.
    path = cash_path(1_000_000, 100_000, (50_000, 60_000))
    assert path == (850_000, 690_000)
    assert all(isinstance(value, int) for value in path)


def test_cash_path_has_no_zero_clamp():
    # Hand: 100,000 minus 60,000 minus 70,000 = -30,000; -30,000 minus
    # 60,000 minus 80,000 = -170,000. Negative balances publish signed.
    path = cash_path(100_000, 60_000, (70_000, 80_000))
    assert path == (-30_000, -170_000)
    assert all(isinstance(value, int) for value in path)
    assert any(value < 0 for value in path)


def test_cash_path_rejects_list_service_and_float_burn():
    with pytest.raises(TypeError, match="must be a tuple"):
        cash_path(100_000, 10_000, [5_000])
    with pytest.raises(TypeError, match="float"):
        cash_path(100_000, 10_000, (5_000.0,))
    with pytest.raises(TypeError, match="float"):
        cash_path(100_000, 10_000.5, (5_000,))


def test_path_length_mismatch_raises():
    with pytest.raises(ValueError, match="ebitda length 1 must equal dates length 2"):
        _evaluate(
            _net_leverage(),
            ebitda=(100_000,),
            dates=DATES_2,
        )
    with pytest.raises(ValueError, match="cash length 1 must equal dates length 2"):
        _evaluate(
            _min_cash(),
            ebitda=(100_000, 100_000),
            dates=DATES_2,
            cash=(500_000,),
        )


def test_burn_floor_quantizes_once_round_half_up():
    # Hand: 100,000 x 2.5 months = 250,000 cents exactly.
    assert burn_floor_cents(100_000, Decimal("2.5")) == 250_000
    # Hand: 100,001 x 0.5 = 50,000.5, ROUND_HALF_UP to 50,001 cents.
    assert burn_floor_cents(100_001, Decimal("0.5")) == 50_001
    with pytest.raises(TypeError, match="float"):
        burn_floor_cents(100_000, 2.5)


def test_ebitda_proxy_is_cfads_published_at_scale_4():
    # Hand: the EBITDA proxy is the cfads tuple; with 1 cent of cash interest
    # per period the cover actual equals cfads, published at ratio scale 4.
    cfads = (300_000, 450_000)
    result = _evaluate(
        _interest_cover(),
        ebitda=cfads,
        dates=DATES_2,
        cash_interest=(1, 1),
    )
    actuals = [row.actual for row in result.periods]
    assert actuals == [Decimal("300000.0000"), Decimal("450000.0000")]
    assert all(isinstance(value, Decimal) for value in actuals)
    assert all(value.as_tuple().exponent == -4 for value in actuals)
    assert all(row.status == "feasible" for row in result.periods)


def test_net_debt_is_closing_minus_cash_including_negative_net_cash():
    # Hand net debt: 1,000,000 minus 300,000 = 700,000; 900,000 minus
    # 1,200,000 = -300,000 (a net cash position, so negative net debt).
    closing = (1_000_000, 900_000)
    cash = (300_000, 1_200_000)
    hand_net_debt = (700_000, -300_000)
    recomputed = tuple(c - h for c, h in zip(closing, cash, strict=True))
    assert recomputed == hand_net_debt
    # Hand leverage: 700,000 / 100,000 = 7.0000, headroom 3.0000 minus
    # 7.0000 = -4.0000 (breach); -300,000 / 100,000 = -3.0000, headroom
    # 3.0000 minus -3.0000 = 6.0000 (feasible).
    result = _evaluate(
        _net_leverage("3.0000"),
        ebitda=(100_000, 100_000),
        dates=DATES_2,
        net_debt=hand_net_debt,
        cash=cash,
    )
    first, second = result.periods
    assert first.actual == Decimal("7.0000")
    assert first.headroom == Decimal("-4.0000")
    assert first.status == "breach"
    assert second.actual == Decimal("-3.0000")
    assert second.headroom == Decimal("6.0000")
    assert second.status == "feasible"
    assert result.status == "breach"
    assert result.first_breach_month == 1
    assert result.min_headroom == Decimal("-4.0000")


def test_pre_revenue_zero_ebitda_is_not_applicable_with_pinned_reasons():
    # Hand pin P4.1: zero cfads in every period makes both ratios undefined.
    leverage = _evaluate(
        _net_leverage(),
        ebitda=(0, 0),
        dates=DATES_2,
    )
    assert leverage.status == "not_applicable"
    assert leverage.reason == (
        "pre-revenue borrower, EBITDA proxy (CFADS) is zero so net leverage is undefined"
    )
    assert leverage.periods == ()
    assert leverage.first_breach_month is None
    assert leverage.min_headroom is None
    cover = _evaluate(
        _interest_cover(),
        ebitda=(0, 0),
        dates=DATES_2,
        cash_interest=(50_000, 50_000),
    )
    assert cover.status == "not_applicable"
    assert cover.reason == (
        "pre-revenue borrower, EBITDA proxy (CFADS) is zero so interest cover is undefined"
    )
    assert cover.periods == ()
    assert cover.first_breach_month is None
    assert cover.min_headroom is None


def test_rbf_not_applicable_reasons_are_pinned_verbatim():
    cover = _evaluate(
        _interest_cover(),
        ebitda=(100_000, 100_000),
        dates=DATES_2,
        cash_interest=None,
    )
    assert cover.status == "not_applicable"
    assert cover.reason == "RBF carries no interest term so cash interest is undefined"
    assert cover.periods == ()
    assert cover.first_breach_month is None
    assert cover.min_headroom is None
    dscr_zero = _evaluate(
        _dscr_floor(),
        ebitda=(100_000, 100_000),
        dates=DATES_2,
        debt_service=(0, 0),
    )
    assert dscr_zero.status == "not_applicable"
    assert dscr_zero.reason == (
        "no period carries positive remittance so RBF DSCR is undefined"
    )
    assert dscr_zero.periods == ()
    dscr_none = _evaluate(
        _dscr_floor(),
        ebitda=(100_000, 100_000),
        dates=DATES_2,
        debt_service=None,
    )
    assert dscr_none.status == "not_applicable"
    assert dscr_none.reason == (
        "no period carries positive remittance so RBF DSCR is undefined"
    )
    assert dscr_none.periods == ()
    assert dscr_none.first_breach_month is None
    assert dscr_none.min_headroom is None


def test_zero_service_periods_publish_no_dscr_row():
    # Hand DSCR: period 1 has zero service so it publishes no row; period 2
    # is 150,000 / 50,000 = 3.0000, headroom 3.0000 minus 1.1500 = 1.8500.
    result = _evaluate(
        _dscr_floor(),
        ebitda=(200_000, 150_000),
        dates=DATES_2,
        debt_service=(0, 50_000),
    )
    assert len(result.periods) == 1
    row = result.periods[0]
    assert row.period == 2
    assert row.payment_date == DATE_2
    assert row.actual == Decimal("3.0000")
    assert row.headroom == Decimal("1.8500")
    assert row.status == "feasible"
    assert result.status == "feasible"
    assert result.first_breach_month is None
    assert result.min_headroom == Decimal("1.8500")


def test_first_breach_month_is_three_on_synthetic_breach_path():
    # Hand cover actuals: 400,000 / 100,000 = 4.0000, 300,000 / 100,000 =
    # 3.0000, 100,000 / 100,000 = 1.0000 (breach), 200,000 / 100,000 =
    # 2.0000 (equality feasible). Headrooms against the 2.0000 min limit:
    # +2.0000, +1.0000, -1.0000, 0.0000.
    result = _evaluate(
        _interest_cover("2.0000"),
        ebitda=(400_000, 300_000, 100_000, 200_000),
        dates=DATES_4,
        cash_interest=(100_000, 100_000, 100_000, 100_000),
    )
    assert [row.status for row in result.periods] == [
        "feasible",
        "feasible",
        "breach",
        "feasible",
    ]
    assert result.status == "breach"
    assert result.first_breach_month == 3
    assert result.min_headroom == Decimal("-1.0000")


def test_no_breach_is_feasible_with_none_first_breach_month():
    # Hand cover actuals: 4.0000, 3.0000, 3.0000 all clear the 2.0000 floor.
    result = _evaluate(
        _interest_cover("2.0000"),
        ebitda=(400_000, 300_000, 300_000),
        dates=DATES_3,
        cash_interest=(100_000, 100_000, 100_000),
    )
    assert all(row.status == "feasible" for row in result.periods)
    assert result.status == "feasible"
    assert result.first_breach_month is None
    assert result.min_headroom == Decimal("1.0000")


def test_min_headroom_tracks_the_worst_period_not_the_first_breach():
    # Hand headrooms against the 2.0000 min limit: 4.0000 gives +2.0000,
    # 1.0000 gives -1.0000 (first breach at period 2), 0.5000 gives
    # -1.5000 (the worst, at period 3).
    result = _evaluate(
        _interest_cover("2.0000"),
        ebitda=(400_000, 100_000, 50_000),
        dates=DATES_3,
        cash_interest=(100_000, 100_000, 100_000),
    )
    assert [row.status for row in result.periods] == ["feasible", "breach", "breach"]
    assert result.first_breach_month == 2
    assert result.min_headroom == Decimal("-1.5000")


def test_validate_payload_accepts_breach_status():
    status = envelope(
        "breach",
        "status",
        provenance="derived: covenant period headroom below the limit",
        asof="2026-11-10",
    )
    payload = {"covenants": {"period_status": status}}
    assert validate_payload(payload) == []


def test_min_cash_headroom_is_exact_integer_cents():
    # Hand: 600,000 minus 500,000 = +100,000; 400,000 minus 500,000 =
    # -100,000, so period 2 breaches.
    result = _evaluate(
        _min_cash(500_000),
        ebitda=(100_000, 100_000),
        dates=DATES_2,
        cash=(600_000, 400_000),
    )
    first, second = result.periods
    assert first.actual == 600_000
    assert first.headroom == 100_000
    assert first.status == "feasible"
    assert second.actual == 400_000
    assert second.headroom == -100_000
    assert second.status == "breach"
    assert isinstance(first.headroom, int)
    assert isinstance(second.headroom, int)
    assert not isinstance(first.headroom, Decimal)
    assert result.status == "breach"
    assert result.first_breach_month == 2
    assert result.min_headroom == -100_000


def test_package_returns_pinned_order_regardless_of_input_order():
    specs = (_net_leverage(), _interest_cover(), _min_cash(), _dscr_floor())
    paths = {
        "ebitda": (400_000, 400_000),
        "net_debt": (300_000, 300_000),
        "cash": (600_000, 550_000),
        "cash_interest": (100_000, 100_000),
        "debt_service": (50_000, 50_000),
        "dates": DATES_2,
    }
    pinned = ("net_leverage", "interest_cover", "min_cash", "dscr_floor")
    first = evaluate_package(tuple(reversed(specs)), **paths)
    assert tuple(result.spec.name for result in first) == pinned
    assert all(isinstance(result, CovenantResult) for result in first)
    assert all(result.status == "feasible" for result in first)
    second = evaluate_package((specs[2], specs[0], specs[3], specs[1]), **paths)
    assert tuple(result.spec.name for result in second) == pinned
    # Each output slot carries the input spec object for its pinned name.
    assert first[0].spec is specs[0]
    assert first[1].spec is specs[1]
    assert first[2].spec is specs[2]
    assert first[3].spec is specs[3]


def test_package_rejects_unknown_missing_and_duplicate_names():
    specs = (_net_leverage(), _interest_cover(), _min_cash(), _dscr_floor())
    paths = {
        "ebitda": (400_000,),
        "net_debt": (300_000,),
        "cash": (600_000,),
        "cash_interest": (100_000,),
        "debt_service": (50_000,),
        "dates": (DATE_1,),
    }
    unknown = CovenantSpec("debt_yield", "min", Decimal("1.5000"), "ratio", CITATION, None)
    with pytest.raises(ValueError, match="unknown covenant specs: debt_yield"):
        evaluate_package(specs[:3] + (unknown,), **paths)
    with pytest.raises(ValueError, match="missing covenant specs: dscr_floor"):
        evaluate_package(specs[:3], **paths)
    duplicate = (specs[0], _net_leverage("4.0000"), specs[1], specs[2], specs[3])
    with pytest.raises(ValueError, match="duplicate covenant spec"):
        evaluate_package(duplicate, **paths)
    with pytest.raises(TypeError, match="must be a tuple"):
        evaluate_package(list(specs), **paths)
