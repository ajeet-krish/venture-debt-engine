"""Tests for vdebt.config: pinned scenario.toml values and loader rejections.

Covers architecture section 6.3 and section 7 pins: seed and floor values are
pinned, fifteen bands each carry citation and band keys, pricing bands sample
the spread bounds and keep total rates in extras, warrant coverage keeps all
four sub-bounds, native TOML floats are banned, missing band metadata raises.
Also covers the phase 2 pins: [rates] series and label patterns, [generator]
weights and derived term keys, the four generator bands, and the strata slot
recipes with their id and weight-sum validation.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from vdebt.config import Band, GeneratorConfig, RatesConfig, SlotOverride, load_scenario

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

# Phase 2 sections for minimal scenarios; individual tests override one block
# to plant a single bad value while everything else stays valid.
RATES_BLOCK = (
    "[rates]\n"
    'series = "TB.CDN.90D.MID"\n'
    'fallback_series = "TB.CDN.180D.MID"\n'
    "label_pattern = '(?i)bill.{0,40}3.{0,8}month|3.{0,8}month.{0,40}bill'\n"
    "fallback_label_pattern = '(?i)bill.{0,40}6.{0,8}month|6.{0,8}month.{0,40}bill'\n"
)

GENERATOR_BLOCK = (
    "[generator.weights.lender_type]\n"
    'bank = "0.45"\n'
    'non_bank = "0.55"\n'
    "\n"
    "[generator.weights.structure]\n"
    'bridge = "0.15"\n'
    'bullet = "0.25"\n'
    'io_then_straight_line = "0.35"\n'
    'rbf_capped = "0.25"\n'
    "\n"
    "[generator.weights.pre_revenue]\n"
    'no = "0.90"\n'
    'yes = "0.10"\n'
    "\n"
    "[generator]\n"
    "term_amortizing_periods = 36\n"
    "rbf_horizon_periods = 24\n"
    'rbf_remittance_rate_pct = "5.00"\n'
    "bridge_tenor_months = 3\n"
)


def _minimal_scenario(
    band_lines: list[str],
    rates_block: str = RATES_BLOCK,
    generator_block: str = GENERATOR_BLOCK,
) -> str:
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
    return "\n".join(lines) + "\n" + rates_block + "\n" + generator_block


def test_pinned_top_level_values():
    cfg = load_scenario(SCENARIO_PATH)
    assert cfg.seed == 20261110
    assert cfg.runway_floor_months == Decimal("18.0")
    assert cfg.dscr_floor == Decimal("1.1500")
    assert cfg.currency == "USD"
    assert cfg.rounding_mode == "ROUND_HALF_UP"
    assert isinstance(cfg.runway_floor_months, Decimal)
    assert isinstance(cfg.dscr_floor, Decimal)


def test_fifteen_bands_each_carry_citation_and_band_keys():
    cfg = load_scenario(SCENARIO_PATH)
    assert len(cfg.bands) == 15
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


def test_rates_section_parses_to_pinned_values():
    rates = load_scenario(SCENARIO_PATH).rates
    assert isinstance(rates, RatesConfig)
    assert rates.series == "TB.CDN.90D.MID"
    assert rates.fallback_series == "TB.CDN.180D.MID"
    assert rates.label_pattern == (
        "(?i)bill.{0,40}3.{0,8}month|3.{0,8}month.{0,40}bill"
    )
    assert rates.fallback_label_pattern == (
        "(?i)bill.{0,40}6.{0,8}month|6.{0,8}month.{0,40}bill"
    )


def test_generator_section_parses_to_pinned_values():
    generator = load_scenario(SCENARIO_PATH).generator
    assert isinstance(generator, GeneratorConfig)
    assert generator.term_amortizing_periods == 36
    assert generator.rbf_horizon_periods == 24
    assert generator.rbf_remittance_rate_pct == Decimal("5.00")
    assert generator.bridge_tenor_months == 3
    assert generator.weights == {
        "lender_type": {"bank": Decimal("0.45"), "non_bank": Decimal("0.55")},
        "structure": {
            "bridge": Decimal("0.15"),
            "bullet": Decimal("0.25"),
            "io_then_straight_line": Decimal("0.35"),
            "rbf_capped": Decimal("0.25"),
        },
        "pre_revenue": {"no": Decimal("0.90"), "yes": Decimal("0.10")},
    }


def test_phase_two_bands_carry_citation_and_band_keys():
    bands = load_scenario(SCENARIO_PATH).bands
    pinned = {
        "last_round": (Decimal(1000000000), Decimal(40000000000), "usd_minor_units", 0),
        "arr": (Decimal(200000000), Decimal(15000000000), "usd_minor_units", 0),
        "net_burn": (Decimal(50000000), Decimal(800000000), "usd_minor_units", 0),
        "cash_coverage_months": (Decimal("15.0"), Decimal("150.0"), "months", 1),
    }
    for name, (minimum, maximum, unit, scale) in pinned.items():
        band = bands[name]
        assert isinstance(band, Band), name
        assert band.min == minimum, name
        assert band.max == maximum, name
        assert band.unit == unit, name
        assert band.scale == scale, name
        assert band.citation.strip(), name
        assert "accessed 2026-11-11" in band.citation, name
        assert band.band.strip(), name


def test_strata_slot_recipes_parse_to_pinned_values():
    slots = load_scenario(SCENARIO_PATH).generator.strata_slots
    assert all(isinstance(slot, SlotOverride) for slot in slots)
    assert [slot.borrower_id for slot in slots] == [
        "SYN-0001",
        "SYN-0002",
        "SYN-0003",
        "SYN-0004",
        "SYN-0005",
        "SYN-0006",
    ]
    assert [slot.stratum for slot in slots] == [
        "pre_revenue",
        "zero_warrant_bank",
        "infeasible_at_zero_draw",
        "runway_bound",
        "dscr_bound",
        "rbf_shortfall",
    ]
    assert slots[0].overrides == {"pre_revenue": "yes", "arr": "0"}
    assert slots[1].overrides == {
        "lender_type": "bank",
        "warrant_coverage_pct": "0.00",
        "spread_pct": "3.0000",
    }
    assert slots[2].overrides == {"cash_coverage_months": "15.0"}
    assert slots[3].overrides == {
        "last_round": "40000000000",
        "net_burn": "50000000",
        "cash_coverage_months": "20.0",
        "arr": "15000000000",
        "gross_margin_pct": "80.00",
        "monthly_growth_pct": "0.5000",
    }
    assert slots[4].overrides == {
        "last_round": "1000000000",
        "arr": "200000000",
        "gross_margin_pct": "50.00",
        "monthly_growth_pct": "0.5000",
        "cash_coverage_months": "150.0",
    }
    assert slots[5].overrides == {
        "structure": "rbf_capped",
        "rbf_cap": "2.50",
        "arr": "200000000",
        "monthly_growth_pct": "0.5000",
        "cash_coverage_months": "150.0",
    }


def test_strata_slot_ids_are_unique_and_in_range():
    slots = load_scenario(SCENARIO_PATH).generator.strata_slots
    ids = [slot.borrower_id for slot in slots]
    assert len(ids) == len(set(ids))
    for borrower_id in ids:
        number = int(borrower_id.removeprefix("SYN-"))
        assert 1 <= number <= 24, borrower_id


def test_native_float_on_phase_two_decimal_key_is_rejected(tmp_path: Path):
    float_generator = GENERATOR_BLOCK.replace(
        'rbf_remittance_rate_pct = "5.00"', "rbf_remittance_rate_pct = 5.00"
    )
    assert float_generator != GENERATOR_BLOCK
    path = tmp_path / "scenario.toml"
    path.write_text(
        _minimal_scenario(BAND_FULL, generator_block=float_generator),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="float"):
        load_scenario(path)


def test_each_weight_table_sums_to_one():
    weights = load_scenario(SCENARIO_PATH).generator.weights
    assert set(weights) == {"lender_type", "structure", "pre_revenue"}
    for category, table in weights.items():
        total = sum(table.values(), Decimal(0))
        assert total == Decimal(1), category


def test_weight_table_summing_to_099_raises(tmp_path: Path):
    bad_generator = GENERATOR_BLOCK.replace('non_bank = "0.55"', 'non_bank = "0.54"')
    assert bad_generator != GENERATOR_BLOCK
    path = tmp_path / "scenario.toml"
    path.write_text(
        _minimal_scenario(BAND_FULL, generator_block=bad_generator),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sum"):
        load_scenario(path)


def test_strata_slot_out_of_range_id_raises(tmp_path: Path):
    bad_slot = (
        "[[generator.strata_slots]]\n"
        'borrower_id = "SYN-0025"\n'
        'stratum = "pre_revenue"\n'
        'overrides = { arr = "0" }\n'
    )
    path = tmp_path / "scenario.toml"
    path.write_text(
        _minimal_scenario(BAND_FULL, generator_block=GENERATOR_BLOCK + "\n" + bad_slot),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="SYN-0025"):
        load_scenario(path)


def test_strata_slot_duplicate_id_raises(tmp_path: Path):
    first_slot = (
        "[[generator.strata_slots]]\n"
        'borrower_id = "SYN-0001"\n'
        'stratum = "pre_revenue"\n'
        'overrides = { arr = "0" }\n'
    )
    second_slot = (
        "[[generator.strata_slots]]\n"
        'borrower_id = "SYN-0001"\n'
        'stratum = "runway_bound"\n'
        'overrides = { arr = "1" }\n'
    )
    path = tmp_path / "scenario.toml"
    path.write_text(
        _minimal_scenario(
            BAND_FULL,
            generator_block=GENERATOR_BLOCK + "\n" + first_slot + second_slot,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="SYN-0001"):
        load_scenario(path)


def test_label_patterns_compile():
    rates = load_scenario(SCENARIO_PATH).rates
    assert re.compile(rates.label_pattern) is not None
    assert re.compile(rates.fallback_label_pattern) is not None


def test_bad_label_pattern_raises(tmp_path: Path):
    bad_rates = RATES_BLOCK.replace(
        "label_pattern = '(?i)bill.{0,40}3.{0,8}month|3.{0,8}month.{0,40}bill'",
        "label_pattern = '(['",
    )
    assert bad_rates != RATES_BLOCK
    path = tmp_path / "scenario.toml"
    path.write_text(
        _minimal_scenario(BAND_FULL, rates_block=bad_rates),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="label_pattern"):
        load_scenario(path)
