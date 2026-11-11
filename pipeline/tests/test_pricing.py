"""Tests for vdebt.pricing: all-in exactness, excursion edges, shock, price().

Covers architecture P2.3 and the binding test_pricing entry in the P2.7 test
plan. The all-in sum is exact at percent_annual scale 4 (fixture F2 basis:
4.0000 plus 4.0000 equals 8.0000) and is asserted both as a Decimal and as its
fixed-scale string form. The band-excursion check is probed at both inclusive
total-rate edges and one tick outside for each lender band from the committed
scenario.toml, and an unknown lender_type raises ValueError.

The rate shock uses the [scenario].rate_shock_bp pin of 200 basis points:
8.0000 plus 200 bp is 10.0000. price() runs against a directly constructed
RateSnapshot with a Decimal base rate and a Friday observation date: the
returned Pricing carries consistent fields (spread quantized once to scale 4,
all-in equal to base plus spread, shocked equal to all-in plus 2.0000), and
band_excursion is computed from the base all-in alone, proven by a case whose
shocked rate falls outside the bank band while the base stays inside. A
non-integral rate_shock_bp loaded from a temporary scenario.toml copy raises
ValueError.

No test here touches the network; the snapshot is built in memory.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from vdebt.config import load_scenario
from vdebt.money import format_fixed
from vdebt.pricing import (
    STATUS_EXCURSION,
    STATUS_WITHIN_BAND,
    Pricing,
    all_in_rate,
    check_excursion,
    price,
    shocked_rate,
)
from vdebt.rates import RateSnapshot

PIPELINE_DIR = Path(__file__).resolve().parent.parent
SCENARIO_PATH = PIPELINE_DIR / "config" / "scenario.toml"

# Loaded from the real committed scenario.toml so the pricing bands and the
# shock pin exercised below are exactly the values the engine runs.
CFG = load_scenario(SCENARIO_PATH)

# 2026-11-06 is a Friday, the business-day observation date P2.1 requires.
FRIDAY_OBSERVATION = date(2026, 11, 6)


def _snapshot(base_rate: str) -> RateSnapshot:
    """Build an in-memory RateSnapshot with full external provenance."""
    return RateSnapshot(
        series="BTB3M",
        label="Bank of Canada 3 month treasury bill",
        observation_date=FRIDAY_OBSERVATION,
        base_rate=Decimal(base_rate),
        source="Bank of Canada Valet",
        source_timestamp=FRIDAY_OBSERVATION.isoformat(),
        manifest_hashes={"BTB3M.json": "0" * 64},
    )


def test_all_in_rate_is_exact_at_scale_4() -> None:
    """F2 basis: 4.0000 plus 4.0000 equals 8.0000 exactly, no drift."""
    total = all_in_rate(Decimal("4.0000"), Decimal("4.0000"))
    assert total == Decimal("8.0000")
    assert total.as_tuple().exponent == -4
    assert str(total) == "8.0000"
    assert format_fixed(total, 4) == "8.0000"


@pytest.mark.parametrize(
    ("lender_type", "rate_text", "expected"),
    [
        ("bank", "5.0000", STATUS_WITHIN_BAND),
        ("bank", "8.0000", STATUS_WITHIN_BAND),
        ("bank", "4.9999", STATUS_EXCURSION),
        ("bank", "8.0001", STATUS_EXCURSION),
        ("non_bank", "10.0000", STATUS_WITHIN_BAND),
        ("non_bank", "14.0000", STATUS_WITHIN_BAND),
        ("non_bank", "9.9999", STATUS_EXCURSION),
        ("non_bank", "14.0001", STATUS_EXCURSION),
    ],
)
def test_excursion_band_edges_are_inclusive_per_lender(
    lender_type: str, rate_text: str, expected: str
) -> None:
    """Both band ends are inclusive; one tick outside flags an excursion."""
    assert check_excursion(Decimal(rate_text), lender_type, CFG) == expected


def test_unknown_lender_type_raises_value_error() -> None:
    """A lender type outside the band map fails loudly, never a silent guess."""
    with pytest.raises(ValueError, match="unknown lender_type"):
        check_excursion(Decimal("8.0000"), "credit_union", CFG)


def test_shocked_rate_uses_scenario_shock_pin() -> None:
    """200 bp from [scenario] moves 8.0000 to 10.0000 through price()."""
    assert CFG.rate_shock_bp == Decimal(200)
    assert shocked_rate(Decimal("8.0000"), 200) == Decimal("10.0000")
    pricing = price(Decimal("4.0000"), _snapshot("4.0000"), CFG, "bank")
    assert pricing.shocked_all_in_rate == Decimal("10.0000")
    assert pricing.shocked_all_in_rate == pricing.all_in_rate + Decimal("2.0000")


def test_price_returns_consistent_fields() -> None:
    """Every field of the frozen Pricing agrees with the snapshot and cfg."""
    snapshot = _snapshot("4.0000")
    pricing = price(Decimal("4.0000"), snapshot, CFG, "bank")
    assert isinstance(pricing, Pricing)
    assert pricing.base_rate == snapshot.base_rate == Decimal("4.0000")
    assert pricing.spread_pct == Decimal("4.0000")
    assert pricing.spread_pct.as_tuple().exponent == -4
    assert pricing.all_in_rate == Decimal("8.0000")
    assert pricing.all_in_rate == pricing.base_rate + pricing.spread_pct
    assert pricing.shocked_all_in_rate == Decimal("10.0000")
    assert pricing.band_excursion == STATUS_WITHIN_BAND


def test_price_quantizes_spread_once_to_scale_4() -> None:
    """A scale 0 spread arrives quantized once at the storage boundary."""
    pricing = price(Decimal(4), _snapshot("4.0000"), CFG, "bank")
    assert pricing.spread_pct == Decimal("4.0000")
    assert pricing.spread_pct.as_tuple().exponent == -4
    assert pricing.all_in_rate == Decimal("8.0000")


def test_band_excursion_uses_base_rate_only() -> None:
    """The shocked rate never feeds the check: base in band stays within."""
    pricing = price(Decimal("4.0000"), _snapshot("4.0000"), CFG, "bank")
    # Base 8.0000 sits inside the bank band 5.0000 to 8.0000 while the
    # shocked 10.0000 falls outside it, so a base-only check reports within.
    assert pricing.all_in_rate <= Decimal("8.0000")
    assert pricing.shocked_all_in_rate > Decimal("8.0000")
    assert pricing.band_excursion == STATUS_WITHIN_BAND
    # The same base all-in is an excursion for a non-bank lender (10 to 14).
    non_bank = price(Decimal("4.0000"), _snapshot("4.0000"), CFG, "non_bank")
    assert non_bank.band_excursion == STATUS_EXCURSION


def test_price_rejects_non_integral_rate_shock_bp(tmp_path: Path) -> None:
    """A quoted 200.5 bp pin loads as a Decimal but price() refuses it."""
    text = SCENARIO_PATH.read_text(encoding="utf-8")
    patched = text.replace("rate_shock_bp = 200", 'rate_shock_bp = "200.5"')
    assert patched != text, "scenario.toml shock pin line not found"
    path = tmp_path / "scenario.toml"
    path.write_text(patched, encoding="utf-8")
    cfg = load_scenario(path)
    assert cfg.rate_shock_bp == Decimal("200.5")
    with pytest.raises(ValueError, match="whole number of basis points"):
        price(Decimal("4.0000"), _snapshot("4.0000"), cfg, "bank")
