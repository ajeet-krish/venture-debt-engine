"""Date conventions for the venture debt engine.

ACT/365 year fractions are Decimal divisions performed in MONEY_CONTEXT, so a
full non-leap year is exactly 1. Business days are Monday to Friday with no
holiday table, which is disclosed on the methods page. Month arithmetic
preserves the day of month and clamps to the target month's last day. Payment
dates roll forward only, never backward, and must be strictly increasing.
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from vdebt.money import MONEY_CONTEXT


def days_between(start: date, end: date) -> int:
    """Return whole days from start to end (negative when end precedes start)."""
    return (end - start).days


def year_fraction_act365(start: date, end: date) -> Decimal:
    """Return the ACT/365 year fraction: actual days divided by 365.

    The division runs in MONEY_CONTEXT, passed explicitly. A full non-leap year
    is exactly 1.
    """
    days = Decimal(days_between(start, end))
    return MONEY_CONTEXT.divide(days, Decimal(365))


def is_business_day(d: date) -> bool:
    """Return True when d falls Monday to Friday (no holiday table)."""
    return d.weekday() < 5


def roll_forward(d: date) -> date:
    """Return the next business day on or after d, never moving backward.

    A date that is already a business day is returned unchanged.
    """
    current = d
    while not is_business_day(current):
        current += timedelta(days=1)
    return current


def add_months(d: date, months: int) -> date:
    """Shift d by months, preserving day of month and clamping to month end.

    Jan 31 plus 1 month gives Feb 28 in 2026 (a non-leap year). The day is
    clamped to the target month's last day whenever the month is shorter.
    """
    if isinstance(months, float):
        raise TypeError("months must not be a float; use int")
    if not isinstance(months, int):
        raise TypeError(f"months must be an int, got {type(months).__name__}")
    month_index = d.year * 12 + (d.month - 1) + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def payment_dates(close: date, n_periods: int) -> list[date]:
    """Return payment dates for k = 1..n_periods.

    Each date is roll_forward(add_months(close, k)), so the first payment is
    exactly one calendar month after close, rolled forward when the target is
    not a business day. The close date must be a business day. Dates are
    asserted strictly increasing; a violation raises ValueError instead of
    returning silently.
    """
    if not is_business_day(close):
        raise ValueError(f"close date must be a business day, got {close.isoformat()}")
    if isinstance(n_periods, float):
        raise TypeError("n_periods must not be a float; use int")
    if not isinstance(n_periods, int):
        raise TypeError(f"n_periods must be an int, got {type(n_periods).__name__}")
    if n_periods < 0:
        raise ValueError(f"n_periods must be >= 0, got {n_periods}")
    dates: list[date] = []
    previous = close
    for k in range(1, n_periods + 1):
        candidate = roll_forward(add_months(close, k))
        if candidate <= previous:
            raise ValueError(
                f"payment dates must be strictly increasing: period {k} on "
                f"{candidate.isoformat()} does not follow {previous.isoformat()}"
            )
        dates.append(candidate)
        previous = candidate
    return dates
