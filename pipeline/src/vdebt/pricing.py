"""Borrower pricing: all-in rate, rate shock, and band excursion (architecture P2.3).

all_in_rate = base_rate + spread_pct, both percent_annual, summed in exact
Decimal and quantized exactly once at the storage boundary to scale 4 with
vdebt.money.quantize (fixture F2: "4.0000" + "4.0000" = "8.0000"). The rate
shock adds rate_shock_bp / 100 (200 basis points is +2.0000) and also
quantizes once at scale 4; phase 2 publishes the shocked all-in beside the
base all-in, and phase 4 feeds it to shocked schedules and sensitivity.

The band-excursion check compares the base all-in against the total-rate
columns of the lender type's pricing band (5.0000 to 8.0000 for bank,
10.0000 to 14.0000 for non_bank) with inclusive bounds and returns
"within_band" or "excursion". It is report-only: it flags and reports and
never fails the build (decisions entry "Solver fidelity and excursion
policy", spec section 10.4). The shocked all-in is a scenario figure and is
never checked for excursion.

Provenance is the caller's envelope duty: this module computes rate figures
only. Every rate figure published by callers must carry snapshot.source and
snapshot.source_timestamp from the RateSnapshot in its envelope.

Depends on the standard library plus vdebt.config, vdebt.money, and
vdebt.rates types only. No network access anywhere in this module.
"""

from dataclasses import dataclass
from decimal import Decimal

from vdebt.config import ScenarioConfig
from vdebt.money import MONEY_CONTEXT, quantize
from vdebt.rates import PERCENT_ANNUAL_SCALE, RateSnapshot

# Excursion status values published per borrower (report only, never a failure).
STATUS_WITHIN_BAND: str = "within_band"
STATUS_EXCURSION: str = "excursion"

# Lender type to pricing band name; any other lender_type raises ValueError.
_LENDER_TYPE_BANDS: dict[str, str] = {
    "bank": "pricing_bank",
    "non_bank": "pricing_nonbank",
}

# Total-rate bound keys kept in each pricing band's extras (config.py).
_TOTAL_RATE_MIN_KEY: str = "total_rate_min_pct"
_TOTAL_RATE_MAX_KEY: str = "total_rate_max_pct"


@dataclass(frozen=True)
class Pricing:
    """One borrower's published rate figures, all percent_annual scale 4.

    band_excursion is STATUS_WITHIN_BAND or STATUS_EXCURSION, evaluated on
    the base all-in only. This dataclass holds rate figures alone: source and
    source_timestamp provenance belongs to the caller's published envelope.
    """

    base_rate: Decimal
    spread_pct: Decimal
    all_in_rate: Decimal
    shocked_all_in_rate: Decimal
    band_excursion: str  # STATUS_WITHIN_BAND | STATUS_EXCURSION


def _require_decimal(value: object, name: str) -> Decimal:
    """Validate a finite Decimal rate input, rejecting float loudly."""
    if isinstance(value, float):
        raise TypeError(f"{name} must not be a float; use Decimal")
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}")
    return value


def _require_shock_bp(value: object) -> int:
    """Validate a whole number of basis points, rejecting bool and float."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"shock_bp must be an int, got {type(value).__name__}")
    return value


def _require_scenario(value: object, name: str) -> ScenarioConfig:
    """Validate a ScenarioConfig argument, failing loudly on any other type."""
    if not isinstance(value, ScenarioConfig):
        raise TypeError(
            f"{name} must be a ScenarioConfig, got {type(value).__name__}"
        )
    return value


def all_in_rate(base_rate: Decimal, spread_pct: Decimal) -> Decimal:
    """Return base_rate + spread_pct quantized once to percent_annual scale 4.

    The sum is taken in exact Decimal arithmetic in MONEY_CONTEXT and
    quantized exactly once at the storage boundary, so the result is exact at
    scale 4 (fixture F2: "4.0000" + "4.0000" = "8.0000"). Neither operand is
    rounded before the sum; both arrive at scale 4 from the committed
    snapshot and the pricing band draw.
    """
    _require_decimal(base_rate, "base_rate")
    _require_decimal(spread_pct, "spread_pct")
    total = MONEY_CONTEXT.add(base_rate, spread_pct)
    return quantize(total, PERCENT_ANNUAL_SCALE)


def shocked_rate(all_in_rate: Decimal, shock_bp: int) -> Decimal:
    """Return all_in_rate + shock_bp / 100 quantized once to scale 4.

    shock_bp is a whole number of basis points, the [scenario].rate_shock_bp
    pin from scenario.toml (200 gives +2.0000, so 8.0000 becomes 10.0000).
    The shock division and sum run in MONEY_CONTEXT and the result is
    quantized exactly once at the storage boundary. The shocked rate is a
    scenario figure: phase 2 publishes it beside the base all-in and phase 4
    feeds shocked schedules and sensitivity from it.
    """
    _require_decimal(all_in_rate, "all_in_rate")
    shock = _require_shock_bp(shock_bp)
    shift = MONEY_CONTEXT.divide(Decimal(shock), Decimal(100))
    total = MONEY_CONTEXT.add(all_in_rate, shift)
    return quantize(total, PERCENT_ANNUAL_SCALE)


def check_excursion(all_in_rate: Decimal, lender_type: str, cfg: ScenarioConfig) -> str:
    """Compare the base all-in against the lender type's total-rate band.

    Returns STATUS_WITHIN_BAND when low <= all_in_rate <= high, inclusive on
    both ends, where low and high are the total-rate columns in the extras of
    the pricing band selected by lender_type ("bank" reads bands.pricing_bank
    5 to 8, "non_bank" reads bands.pricing_nonbank 10 to 14), and
    STATUS_EXCURSION otherwise. An unknown lender_type raises ValueError, and
    a missing band or missing total-rate column also raises ValueError so
    configuration drift fails loudly instead of reporting a false within_band.

    Only the base all-in is checked. The shocked all-in is a scenario figure
    and is never checked (decisions entry "Solver fidelity and excursion
    policy"): the check compares the published base rate against the cited
    band, and the short-rate restatement is disclosed on the methods page.
    The check is report-only: a real excursion returns STATUS_EXCURSION and
    never raises or fails the build.
    """
    _require_decimal(all_in_rate, "all_in_rate")
    _require_scenario(cfg, "cfg")
    if not isinstance(lender_type, str):
        raise TypeError(
            f"lender_type must be a str, got {type(lender_type).__name__}"
        )
    band_name = _LENDER_TYPE_BANDS.get(lender_type)
    if band_name is None:
        allowed = ", ".join(sorted(_LENDER_TYPE_BANDS))
        raise ValueError(
            f"unknown lender_type {lender_type!r}; expected one of: {allowed}"
        )
    band = cfg.bands.get(band_name)
    if band is None:
        raise ValueError(
            f"scenario configuration is missing band {band_name!r}, required "
            f"to check excursion for lender_type {lender_type!r}"
        )
    missing = [
        key
        for key in (_TOTAL_RATE_MIN_KEY, _TOTAL_RATE_MAX_KEY)
        if key not in band.extras
    ]
    if missing:
        raise ValueError(
            f"band {band_name!r} is missing total-rate bound key(s): "
            f"{', '.join(missing)}"
        )
    low = band.extras[_TOTAL_RATE_MIN_KEY]
    high = band.extras[_TOTAL_RATE_MAX_KEY]
    if low <= all_in_rate <= high:
        return STATUS_WITHIN_BAND
    return STATUS_EXCURSION


def _shock_basis_points(cfg: ScenarioConfig) -> int:
    """Read cfg.rate_shock_bp as whole basis points or raise ValueError."""
    shock = cfg.rate_shock_bp
    if shock != MONEY_CONTEXT.to_integral_value(shock):
        raise ValueError(
            f"scenario.rate_shock_bp must be a whole number of basis points, "
            f"got {shock}"
        )
    return int(shock)


def price(
    spread_pct: Decimal,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
    lender_type: str,
) -> Pricing:
    """Price one borrower off the committed base-rate snapshot.

    Builds the frozen Pricing: snapshot.base_rate verbatim, spread_pct
    quantized once to percent_annual scale 4 at the storage boundary, the
    exact sum from all_in_rate, the shocked sum from shocked_rate at
    cfg.rate_shock_bp, and the band_excursion report from check_excursion on
    the base all-in only.

    lender_type selects the pricing band for band_excursion. It is required
    because the excursion check is per borrower: the P2.3 signature sketch
    omits it, but band_excursion cannot be computed without it.

    Provenance is the caller's envelope duty: this function does not attach
    it. Every rate figure published by callers must carry snapshot.source
    and snapshot.source_timestamp from the RateSnapshot in its envelope.
    """
    if not isinstance(snapshot, RateSnapshot):
        raise TypeError(
            f"snapshot must be a RateSnapshot, got {type(snapshot).__name__}"
        )
    _require_scenario(cfg, "cfg")
    spread = quantize(
        _require_decimal(spread_pct, "spread_pct"), PERCENT_ANNUAL_SCALE
    )
    base_rate = snapshot.base_rate
    total = all_in_rate(base_rate, spread)
    shocked = shocked_rate(total, _shock_basis_points(cfg))
    excursion = check_excursion(total, lender_type, cfg)
    return Pricing(
        base_rate=base_rate,
        spread_pct=spread,
        all_in_rate=total,
        shocked_all_in_rate=shocked,
        band_excursion=excursion,
    )
