"""Tests for vdebt.config: pinned scenario.toml values and loader rejections.

Covers architecture section 6.3 and section 7 pins: seed and floor values are
pinned, eleven bands each carry citation and band keys, pricing bands sample
the spread bounds and keep total rates in extras, warrant coverage keeps all
four sub-bounds, native TOML floats are banned, missing band metadata raises.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from vdebt.config import Band, load_scenario

SCENARIO_PATH = Path(__file__).resolve().parent.parent / "config" / "scenario.toml"

# A complete minimal scenario where only the [bands.example] table varies, so
# the loader reaches the band parse before it can raise on anything else.
BAND_FULL = [
    'min_pct = "1"',
    'max_pct = "2"',
    'unit = "percent"',
    "scale = 2",
    'band = "example_band"',
    'citation = "Example source (accessed 2026-11-10)"',
]


def _minimal_scenario(band_lines: list[str]) -> str:
    lines = [
        "[meta]",
        "seed = 20261110",
        'generator_version = "v1"',
        'currency = "USD"',
        'rounding_mode = "ROUND_HALF_UP"',
        "",
        "[sizing]",
        'runway_floor_months = "18.0"',
        'dscr_floor = "1.1500"',
        "bisection_max_iterations = 60",
        "",
        "[solver.irr]",
        'bracket_low = "-0.99"',
        'bracket_high = "10.00"',
        'tolerance_decimal = "0.000001"',
        "max_iterations = 200",
        "",
        "[scenario]",
        "rate_shock_bp = 200",
        "",
        "[downside]",
        'burn_uplift_pct = "50.00"',
        'revenue_downlift_pct = "30.00"',
        "",
        "[warrant]",
        'valuation_factor = "0.15"',
        "",
        "[bands.example]",
    ]
    lines.extend(band_lines)
    return "\n".join(lines) + "\n"


def test_pinned_top_level_values():
    cfg = load_scenario(SCENARIO_PATH)
    assert cfg.seed == 20261110
    assert cfg.runway_floor_months == Decimal("18.0")
    assert cfg.dscr_floor == Decimal("1.1500")
    assert cfg.currency == "USD"
    assert cfg.rounding_mode == "ROUND_HALF_UP"
    assert isinstance(cfg.runway_floor_months, Decimal)
    assert isinstance(cfg.dscr_floor, Decimal)


def test_eleven_bands_each_carry_citation_and_band_keys():
    cfg = load_scenario(SCENARIO_PATH)
    assert len(cfg.bands) == 11
    for name, band in cfg.bands.items():
        assert isinstance(band, Band), name
        assert band.citation.strip(), name
        assert "accessed" in band.citation, name
        assert band.band.strip(), name
        assert isinstance(band.min, Decimal), name
        assert isinstance(band.max, Decimal), name
        assert isinstance(band.scale, int), name
        assert band.min <= band.max, name


def test_pricing_bank_samples_spread_and_keeps_total_rates_in_extras():
    band = load_scenario(SCENARIO_PATH).bands["pricing_bank"]
    assert band.min == Decimal(1)
    assert band.max == Decimal(3)
    assert band.unit == "percent_annual"
    assert band.scale == 4
    assert band.extras["total_rate_min_pct"] == Decimal(5)
    assert band.extras["total_rate_max_pct"] == Decimal(8)


def test_warrant_coverage_samples_overall_bounds_with_four_sub_bounds():
    band = load_scenario(SCENARIO_PATH).bands["warrant_coverage"]
    assert band.min == Decimal(0)
    assert band.max == Decimal(20)
    assert set(band.extras) == {
        "bank_min_pct",
        "bank_max_pct",
        "nonbank_min_pct",
        "nonbank_max_pct",
    }
    assert band.extras["bank_max_pct"] == Decimal(5)
    assert band.extras["nonbank_min_pct"] == Decimal(10)


def test_dscr_floor_reference_single_value_feeds_both_bounds():
    band = load_scenario(SCENARIO_PATH).bands["dscr_floor_reference"]
    assert band.min == band.max == Decimal("1.1500")
    assert band.unit == "ratio"
    assert band.scale == 4


def test_minimal_scenario_with_full_band_loads(tmp_path: Path):
    path = tmp_path / "scenario.toml"
    path.write_text(_minimal_scenario(BAND_FULL), encoding="utf-8")
    cfg = load_scenario(path)
    assert cfg.seed == 20261110
    assert set(cfg.bands) == {"example"}
    assert cfg.bands["example"].min == Decimal(1)


def test_native_toml_float_is_rejected(tmp_path: Path):
    path = tmp_path / "scenario.toml"
    path.write_text(
        "[sizing]\n" "runway_floor_months = 18.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="float"):
        load_scenario(path)


def test_band_missing_citation_is_rejected(tmp_path: Path):
    band_lines = [line for line in BAND_FULL if not line.startswith("citation")]
    path = tmp_path / "scenario.toml"
    path.write_text(_minimal_scenario(band_lines), encoding="utf-8")
    with pytest.raises(ValueError, match="example.citation"):
        load_scenario(path)


def test_band_missing_band_key_is_rejected(tmp_path: Path):
    band_lines = [line for line in BAND_FULL if not line.startswith("band ")]
    path = tmp_path / "scenario.toml"
    path.write_text(_minimal_scenario(band_lines), encoding="utf-8")
    with pytest.raises(ValueError, match="example.band"):
        load_scenario(path)
