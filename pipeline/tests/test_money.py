"""Unit tests for vdebt.money: cents round trips, quantize, fixed text, allocation.

Covers architecture section 6.1 pins: to_cents is the single ROUND_HALF_UP money
boundary, from_cents is exact, allocate_cents preserves the sum, float is banned
for money inputs and outputs.
"""

from decimal import Decimal

import pytest

from vdebt.money import (
    add_cents,
    allocate_cents,
    format_fixed,
    from_cents,
    parse_fixed,
    quantize,
    to_cents,
)


def test_to_cents_from_cents_round_trip_is_idempotent():
    spread = [0, 1, -1, 2, 99, 100, 101, 12345, 99999999, 100000000,
              700000000, 2000000000, 10**12, 10**18, -(10**18)]
    for cents in spread:
        dollars = from_cents(cents)
        assert to_cents(dollars) == cents
        assert to_cents(str(dollars)) == cents


def test_to_cents_is_idempotent_over_dollar_amounts():
    amounts = ["0", "0.01", "0.005", "1.005", "-1.005", "12345.678",
               "0.004999999", "1000000000000.005"]
    for text in amounts:
        first = to_cents(text)
        assert isinstance(first, int)
        assert to_cents(from_cents(first)) == first


def test_half_cent_ties_round_half_up_away_from_zero():
    # 1.005 dollars is exactly 100.5 cents; ROUND_HALF_UP sends the tie away
    # from zero, so 1.005 becomes 101 cents and 1.004 becomes 100 cents.
    assert to_cents(Decimal("1.005")) == 101
    assert to_cents(Decimal("1.004")) == 100
    # Negative ties also move away from zero: -1.005 is exactly -100.5 cents.
    assert to_cents(Decimal("-1.005")) == -101
    assert to_cents(Decimal("-1.004")) == -100
    assert to_cents(Decimal("0.005")) == 1
    assert to_cents(Decimal("-0.005")) == -1


def test_large_values_round_trip_exactly():
    big_cents = 123456789012345678901
    assert from_cents(big_cents) == Decimal("1234567890123456789.01")
    assert to_cents(Decimal("1234567890123456789.01")) == big_cents
    assert from_cents(0) == Decimal("0.00")
    assert from_cents(-1) == Decimal("-0.01")


def test_quantize_rounds_once_with_half_up():
    assert quantize(Decimal("1.005"), 2) == Decimal("1.01")
    assert quantize(Decimal("1.004"), 2) == Decimal("1.00")
    # Exact Decimal 2.675 with ROUND_HALF_UP gives 2.68, where binary float
    # rounding would give 2.67.
    assert quantize(Decimal("2.675"), 2) == Decimal("2.68")
    assert quantize(Decimal("-2.675"), 2) == Decimal("-2.68")
    # Trailing zeros survive so the formatter sees a fixed exponent.
    assert quantize(Decimal("1.2"), 4) == Decimal("1.2000")
    assert quantize(Decimal(42), 0) == Decimal(42)


def test_format_fixed_parse_fixed_round_trip_across_scales():
    cases = [
        (Decimal(42), 0, "42"),
        (Decimal("18.0"), 1, "18.0"),
        (Decimal("1.21"), 2, "1.21"),
        (Decimal("1.21"), 4, "1.2100"),
        (Decimal("-1.21"), 4, "-1.2100"),
    ]
    for value, scale, expected in cases:
        text = format_fixed(value, scale)
        assert text == expected
        assert parse_fixed(text, scale) == quantize(value, scale)


def test_parse_fixed_rejects_wrong_scale():
    with pytest.raises(ValueError):
        parse_fixed("1.21", 4)  # too few fraction digits
    with pytest.raises(ValueError):
        parse_fixed("1.21000", 4)  # too many fraction digits
    with pytest.raises(ValueError):
        parse_fixed("121", 2)  # missing decimal point
    with pytest.raises(ValueError):
        parse_fixed("1.2100", 0)  # fraction digits at scale 0
    with pytest.raises(ValueError):
        parse_fixed("1e-2", 2)  # exponent notation
    with pytest.raises(ValueError):
        parse_fixed(" 1.21", 2)  # surrounding whitespace


def test_allocate_cents_preserves_sum_and_expected_splits():
    assert allocate_cents(30000000, 3) == [10000000, 10000000, 10000000]
    assert allocate_cents(100000001, 3) == [33333334, 33333334, 33333333]
    assert allocate_cents(7, 2) == [4, 3]
    assert allocate_cents(1, 3) == [1, 0, 0]
    assert allocate_cents(0, 4) == [0, 0, 0, 0]
    for total, n in [(30000000, 3), (100000001, 3), (7, 2), (1, 3), (0, 4)]:
        parts = allocate_cents(total, n)
        assert len(parts) == n
        assert sum(parts) == total
        assert max(parts) - min(parts) <= 1


def test_allocate_cents_rejects_zero_parts():
    with pytest.raises(ValueError):
        allocate_cents(100, 0)


def test_float_inputs_raise_type_error():
    with pytest.raises(TypeError):
        to_cents(1.05)
    with pytest.raises(TypeError):
        from_cents(1.5)
    with pytest.raises(TypeError):
        quantize(1.5, 2)
    with pytest.raises(TypeError):
        add_cents([1, 2.0])
    with pytest.raises(TypeError):
        allocate_cents(10.0, 2)
    with pytest.raises(TypeError):
        format_fixed(1.5, 2)


def test_money_functions_never_return_float():
    cents = to_cents(Decimal("1.01"))
    assert isinstance(cents, int) and not isinstance(cents, bool)
    assert isinstance(from_cents(101), Decimal)
    assert isinstance(quantize(Decimal("1.01"), 2), Decimal)
    assert isinstance(format_fixed(Decimal("1.01"), 2), str)
    assert isinstance(parse_fixed("1.01", 2), Decimal)
    total = add_cents([1, 2])
    assert isinstance(total, int) and not isinstance(total, bool)
    parts = allocate_cents(5, 2)
    assert all(isinstance(part, int) for part in parts)
