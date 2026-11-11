"""Unit tests for vdebt.dates: ACT/365, business days, months, payment dates.

Covers architecture sections 6.2 and 12 pins: full non-leap year is exactly 1
in ACT/365, business days are Monday to Friday with roll forward only, month
arithmetic clamps to month end, payment dates are strictly increasing.
"""

import itertools
from datetime import date
from decimal import Decimal

import pytest

from vdebt.dates import (
    add_months,
    days_between,
    is_business_day,
    payment_dates,
    roll_forward,
    year_fraction_act365,
)
from vdebt.money import MONEY_CONTEXT


def test_year_fraction_full_non_leap_year_is_exactly_one():
    assert year_fraction_act365(date(2026, 1, 1), date(2027, 1, 1)) == Decimal(1)
    assert year_fraction_act365(date(2026, 2, 1), date(2027, 2, 1)) == Decimal(1)
    assert days_between(date(2026, 1, 1), date(2027, 1, 1)) == 365


def test_year_fraction_leap_year_is_366_over_365():
    expected = MONEY_CONTEXT.divide(Decimal(366), Decimal(365))
    assert year_fraction_act365(date(2024, 1, 1), date(2025, 1, 1)) == expected
    assert expected > Decimal(1)


def test_year_fraction_known_spans():
    # 31 days is 31/365 exactly at MONEY_CONTEXT precision.
    assert days_between(date(2026, 1, 1), date(2026, 2, 1)) == 31
    assert year_fraction_act365(date(2026, 1, 1), date(2026, 2, 1)) == (
        MONEY_CONTEXT.divide(Decimal(31), Decimal(365))
    )
    assert year_fraction_act365(date(2026, 1, 1), date(2026, 1, 1)) == Decimal(0)
    # A reversed span is negative, mirroring the day count sign.
    assert year_fraction_act365(date(2026, 2, 1), date(2026, 1, 1)) == (
        MONEY_CONTEXT.divide(Decimal(-31), Decimal(365))
    )


def test_is_business_day_monday_through_friday():
    assert is_business_day(date(2026, 1, 5))  # Monday
    assert is_business_day(date(2026, 1, 6))  # Tuesday
    assert is_business_day(date(2026, 1, 7))  # Wednesday
    assert is_business_day(date(2026, 1, 8))  # Thursday
    assert is_business_day(date(2026, 1, 9))  # Friday
    assert not is_business_day(date(2026, 1, 10))  # Saturday
    assert not is_business_day(date(2026, 1, 11))  # Sunday


def test_roll_forward_moves_only_forward():
    assert roll_forward(date(2026, 1, 10)) == date(2026, 1, 12)  # Saturday to Monday
    assert roll_forward(date(2026, 1, 11)) == date(2026, 1, 12)  # Sunday to Monday
    # A date already on a business day is unchanged, never moved backward.
    assert roll_forward(date(2026, 1, 5)) == date(2026, 1, 5)
    for day in range(1, 15):
        current = date(2026, 1, day)
        rolled = roll_forward(current)
        assert rolled >= current
        assert is_business_day(rolled)


def test_add_months_clamps_to_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 3, 31), 1) == date(2026, 4, 30)
    assert add_months(date(2026, 5, 31), 1) == date(2026, 6, 30)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)  # leap February
    assert add_months(date(2026, 12, 31), 1) == date(2027, 1, 31)  # year rollover
    assert add_months(date(2026, 1, 31), 13) == date(2027, 2, 28)
    assert add_months(date(2026, 1, 31), -1) == date(2025, 12, 31)


def test_add_months_preserves_day_when_target_month_is_longer():
    assert add_months(date(2026, 2, 28), 1) == date(2026, 3, 28)
    assert add_months(date(2026, 1, 15), 1) == date(2026, 2, 15)
    with pytest.raises(TypeError):
        add_months(date(2026, 1, 31), 1.0)


def test_payment_dates_strictly_increasing_over_thirty_periods():
    closes = [date(2026, 5, 1), date(2026, 4, 30)]  # Friday close, month-end close
    for close in closes:
        assert is_business_day(close)
        dates = payment_dates(close, 30)
        assert len(dates) == 30
        for previous, current in itertools.pairwise(dates):
            assert current > previous
        assert all(is_business_day(day) for day in dates)
        assert dates[0] == roll_forward(add_months(close, 1))
        assert dates[-1] == roll_forward(add_months(close, 30))


def test_payment_dates_roll_weekend_targets_forward():
    # Close Friday 2026-05-01: period 3 targets 2026-08-01, a Saturday,
    # so it rolls forward to Monday 2026-08-03.
    dates = payment_dates(date(2026, 5, 1), 3)
    assert dates == [date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 3)]


def test_payment_dates_reject_non_business_day_close():
    with pytest.raises(ValueError):
        payment_dates(date(2026, 1, 10), 3)  # Saturday
    with pytest.raises(ValueError):
        payment_dates(date(2026, 1, 11), 3)  # Sunday
    assert payment_dates(date(2026, 5, 1), 0) == []
