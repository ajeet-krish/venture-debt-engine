"""Money as integer minor units with an explicit Decimal context.

Money is stored and transported as a plain Python int of minor units (USD
cents). Decimal carries dollar amounts and metric values, always with
MONEY_CONTEXT passed explicitly to every Decimal operation. The global decimal
context is never mutated, so decimal.setcontext is banned. Float is banned for
money anywhere: no function in this module accepts or returns a float.

Quantization happens exactly once per value at its storage or metric boundary
with ROUND_HALF_UP. Internal intermediates are never rounded.
"""

import decimal
import re
from collections.abc import Iterable
from decimal import Decimal

MINOR_UNITS: dict[str, int] = {"USD": 2}
DEFAULT_CURRENCY: str = "USD"

# Single module-level context: prec=28, ROUND_HALF_UP, created once.
MONEY_CONTEXT = decimal.Context(prec=28, rounding=decimal.ROUND_HALF_UP)

# Fixed-scale text: optional minus sign, digits, optional dot plus digits.
_FIXED_TEXT = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


def _require_scale(scale: int) -> None:
    """Validate that scale is a non-negative int."""
    if not isinstance(scale, int):
        raise TypeError(f"scale must be an int, got {type(scale).__name__}")
    if scale < 0:
        raise ValueError(f"scale must be >= 0, got {scale}")


def _require_int(value: object, name: str) -> int:
    """Validate that value is an int and return it (float is banned for money)."""
    if isinstance(value, float):
        raise TypeError(f"{name} must not be a float; money uses int minor units")
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def minor_units(currency: str = DEFAULT_CURRENCY) -> int:
    """Return the number of minor units (decimal places) for a currency."""
    try:
        return MINOR_UNITS[currency]
    except KeyError:
        raise ValueError(f"unsupported currency: {currency!r}") from None


def quantize(value: Decimal, scale: int) -> Decimal:
    """Round value to scale decimal places with a single ROUND_HALF_UP quantize.

    The quantize runs in MONEY_CONTEXT, which is passed explicitly. The result
    carries exponent -scale, so trailing zeros are preserved for formatting.
    """
    if isinstance(value, float):
        raise TypeError("value must not be a float; use Decimal")
    if not isinstance(value, Decimal):
        raise TypeError(f"value must be a Decimal, got {type(value).__name__}")
    _require_scale(scale)
    if not value.is_finite():
        raise ValueError(f"value must be finite, got {value}")
    exponent = Decimal(f"1e-{scale}")
    return value.quantize(exponent, context=MONEY_CONTEXT)


def to_cents(amount: Decimal | str, currency: str = DEFAULT_CURRENCY) -> int:
    """Convert Decimal dollars to integer cents at the money boundary.

    This conversion is the single ROUND_HALF_UP quantization for the value: the
    amount is quantized to the currency's minor units, then read as an exact
    integer. A str is parsed as an exact Decimal. Float input is rejected.
    """
    if isinstance(amount, float):
        raise TypeError("amount must not be a float; use Decimal or str for money")
    if isinstance(amount, Decimal):
        value = amount
    elif isinstance(amount, str):
        try:
            value = Decimal(amount)
        except (decimal.InvalidOperation, ValueError) as exc:
            raise ValueError(f"amount is not a valid decimal string: {amount!r}") from exc
    else:
        raise TypeError(f"amount must be Decimal or str, got {type(amount).__name__}")
    scale = minor_units(currency)
    quantized = quantize(value, scale)
    # The quantized value has exponent -scale, so its coefficient is the cents.
    parts = quantized.as_tuple()
    coefficient = int("".join(str(d) for d in parts.digits))
    return -coefficient if parts.sign else coefficient


def from_cents(cents: int, currency: str = DEFAULT_CURRENCY) -> Decimal:
    """Convert integer cents back to an exact Decimal dollar amount.

    The conversion is exact: it builds the decimal string directly, so no
    context and no rounding are involved.
    """
    _require_int(cents, "cents")
    scale = minor_units(currency)
    if scale == 0:
        return Decimal(cents)
    sign = "-" if cents < 0 else ""
    digits = str(abs(cents)).rjust(scale + 1, "0")
    return Decimal(f"{sign}{digits[:-scale]}.{digits[-scale:]}")


def format_fixed(value: Decimal, scale: int) -> str:
    """Render value as a fixed-scale string, for example "1.2100" at scale 4."""
    _require_scale(scale)
    return format(quantize(value, scale), f".{scale}f")


def parse_fixed(text: str, scale: int) -> Decimal:
    """Parse a fixed-scale string, rejecting wrong scale or extra digits.

    The text must carry exactly scale digits after the decimal point, and no
    decimal point at all when scale is 0. An optional leading minus sign is
    allowed. Exponent notation, whitespace, and other decorations are rejected.
    """
    _require_scale(scale)
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}")
    if _FIXED_TEXT.fullmatch(text) is None:
        raise ValueError(f"not a fixed-scale numeric literal: {text!r}")
    body = text.removeprefix("-")
    _, dot, fraction = body.partition(".")
    if scale == 0 and dot:
        raise ValueError(f"text has fraction digits but scale is 0: {text!r}")
    if scale > 0 and (not dot or len(fraction) != scale):
        raise ValueError(f"text must have exactly {scale} fraction digits: {text!r}")
    return Decimal(text)


def add_cents(values: Iterable[int]) -> int:
    """Sum integer cents exactly in plain int arithmetic (no rounding)."""
    total = 0
    for value in values:
        total += _require_int(value, "values element")
    return total


def allocate_cents(total: int, n: int) -> list[int]:
    """Split total cents into n parts as equal as possible, preserving the sum.

    Each part gets floor(total / n) and the first (total mod n) parts get one
    extra cent. Used for straight-line principal and equal splits.
    """
    _require_int(total, "total")
    _require_int(n, "n")
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    base, remainder = divmod(total, n)
    return [base + 1 if i < remainder else base for i in range(n)]
