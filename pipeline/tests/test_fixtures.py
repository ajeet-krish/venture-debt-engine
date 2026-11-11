"""Tests for the six golden fixtures: envelope fields, numerics, case shapes.

Covers architecture section 8 pins: every fixture file exists with the common
envelope, rate_source is fixture_pinned, arithmetic is non-empty, numeric
leaves carry unit and scale and never float, tolerances are IRR only, F1 has
three cases with expected statuses and its infeasible case publishes no
facility, F4 has two cases.
"""

import json
import re
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "golden"
FIXTURE_FILES = {
    "F1": "sizing_fixture.json",
    "F2": "schedule_bullet.json",
    "F3": "schedule_io_amort.json",
    "F4": "schedule_rbf.json",
    "F5": "bridge_fixture.json",
    "F6": "rate_shock_fixture.json",
}
# Common envelope shared by every fixture file. F1 and F4 carry their
# expectations inside a "cases" array instead of a top-level "expected" map,
# so the expectation shape is asserted separately.
ENVELOPE_FIELDS = (
    "fixture_id",
    "title",
    "authored_by",
    "authored_phase",
    "rate_source",
    "_comment",
    "arithmetic",
    "inputs",
    "tolerances",
)
NUMERIC_TEXT = re.compile(r"-?\d+(?:\.\d+)?")


def _load(fixture_id: str) -> dict:
    path = GOLDEN_DIR / FIXTURE_FILES[fixture_id]
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _numeric_leaf_errors(node: object, path: str, errors: list[str]) -> None:
    if isinstance(node, float):
        errors.append(f"{path}: float value banned in fixtures")
        return
    if isinstance(node, dict):
        value = node.get("value")
        numeric_int = isinstance(value, int) and not isinstance(value, bool)
        numeric_str = (
            isinstance(value, str) and NUMERIC_TEXT.fullmatch(value) is not None
        )
        if numeric_int or numeric_str:
            if "unit" not in node:
                errors.append(f"{path}: numeric leaf missing unit")
            if not isinstance(node.get("scale"), int):
                errors.append(f"{path}: numeric leaf missing integer scale")
            elif numeric_int and node["scale"] != 0:
                errors.append(f"{path}: integer leaf scale must be 0")
            elif numeric_str:
                text = str(value)
                decimals = len(text.split(".")[1]) if "." in text else 0
                if decimals != node["scale"]:
                    errors.append(f"{path}: scale {node['scale']} mismatches {text!r}")
        for key, child in node.items():
            _numeric_leaf_errors(child, f"{path}.{key}", errors)
    elif isinstance(node, list):
        for index, child in enumerate(node):
            _numeric_leaf_errors(child, f"{path}[{index}]", errors)


def test_all_six_fixture_files_exist_and_load():
    for fixture_id, filename in FIXTURE_FILES.items():
        path = GOLDEN_DIR / filename
        assert path.is_file(), filename
        data = _load(fixture_id)
        assert data["fixture_id"] == fixture_id
        assert data["rate_source"] == "fixture_pinned"
        assert data["authored_phase"] == 1
        for field in ENVELOPE_FIELDS:
            assert field in data, (filename, field)
        arithmetic = data["arithmetic"]
        assert isinstance(arithmetic, list) and arithmetic, filename
        assert all(isinstance(step, str) and step.strip() for step in arithmetic)
        # Each fixture publishes expectations either at the top level or as a
        # non-empty array of cases.
        if "cases" in data:
            assert isinstance(data["cases"], list) and data["cases"], filename
        else:
            assert isinstance(data.get("expected"), dict), filename


def test_numeric_leaves_carry_unit_and_scale_with_no_floats():
    for fixture_id in FIXTURE_FILES:
        errors: list[str] = []
        _numeric_leaf_errors(_load(fixture_id), fixture_id, errors)
        assert errors == [], errors


def test_tolerances_only_allow_irr_keys():
    for fixture_id in FIXTURE_FILES:
        tolerances = _load(fixture_id)["tolerances"]
        assert isinstance(tolerances, dict), fixture_id
        for key in tolerances:
            assert "irr" in key.lower(), (fixture_id, key)
    # Only F6 pins IRR tolerances, absolute 0.0001 in percent units.
    f6_tolerances = _load("F6")["tolerances"]
    assert f6_tolerances["base_irr_percent"]["abs"] == "0.0001"
    assert f6_tolerances["shocked_irr_percent"]["abs"] == "0.0001"


def test_f1_has_three_cases_with_expected_statuses():
    cases = _load("F1")["cases"]
    assert len(cases) == 3
    statuses = [case["expected"]["status"]["value"] for case in cases]
    assert statuses == ["feasible", "infeasible", "feasible"]
    for case in cases:
        assert case["case_id"]
        constraint = case["expected"]["binding_constraint"]["value"]
        assert constraint in ("runway", "dscr")


def test_f1_infeasible_case_has_no_facility_key():
    cases = _load("F1")["cases"]
    infeasible = [
        case for case in cases if case["expected"]["status"]["value"] == "infeasible"
    ]
    assert len(infeasible) == 1
    expected = infeasible[0]["expected"]
    assert "facility_usd" not in expected
    assert "facility" not in expected
    assert "min_dscr" not in expected


def test_f4_has_two_cases():
    cases = _load("F4")["cases"]
    assert len(cases) == 2
    assert [case["case_id"] for case in cases] == ["cap_hit", "shortfall"]
    assert cases[0]["expected"]["status"]["value"] == "feasible"
    assert cases[1]["expected"]["status"]["value"] == "shortfall"
    assert cases[1]["expected"]["residual"]["value"] == 75000000
    assert cases[0]["expected"]["residual"]["value"] == 0
