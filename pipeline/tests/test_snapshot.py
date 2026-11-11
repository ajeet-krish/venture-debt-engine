"""Tests for vdebt.snapshot: envelope matrix, status companions, canonical JSON.

Covers architecture section 6.4 pins: JSON integer iff usd_minor_units or
count, fixed-scale strings elsewhere, exactly one provenance class, band values
need citation with an access date, status companions are enforced by
validate_payload, canonical_dumps sorts keys and bans floats, write_payload
returns the SHA256 of the bytes it wrote.
"""

import hashlib
import json
from pathlib import Path

import pytest

from vdebt.snapshot import canonical_dumps, envelope, validate_payload, write_payload

DERIVED = {
    "provenance": "derived: min over periods of CFADS / debt service",
    "asof": "2026-11-10",
}
GENERATED = {"provenance": "synthetic seed=20261110 generator v1", "asof": "2026-11-10"}
EXTERNAL = {"source": "fixture pinned rate", "source_timestamp": "2026-11-10"}


def test_integer_units_accept_only_json_integer():
    env = envelope(700000000, "usd_minor_units", **DERIVED)
    assert env["value"] == 700000000
    assert "scale" not in env
    assert envelope(30, "count", **DERIVED)["value"] == 30
    with pytest.raises(ValueError, match="JSON integer"):
        envelope("700000000", "usd_minor_units", **DERIVED)
    with pytest.raises(ValueError, match="must not carry scale"):
        envelope(700000000, "usd_minor_units", scale=0, **DERIVED)


def test_fixed_scale_units_require_string_value_with_scale():
    env = envelope("1.2602", "ratio", scale=4, **DERIVED)
    assert env == {"value": "1.2602", "unit": "ratio", "scale": 4, **DERIVED}
    with pytest.raises(ValueError, match="fixed-scale string"):
        envelope(1.26, "ratio", scale=4, **DERIVED)  # float is banned
    with pytest.raises(ValueError, match="fixed-scale string"):
        envelope(5, "ratio", scale=4, **DERIVED)
    with pytest.raises(ValueError, match="requires scale"):
        envelope("1.2602", "ratio", **DERIVED)
    with pytest.raises(ValueError, match="2 fraction digits"):
        envelope("1.2602", "ratio", scale=2, **DERIVED)  # wrong scale digits
    with pytest.raises(ValueError, match="4 fraction digits"):
        envelope("1.26", "ratio", scale=4, **DERIVED)


def test_fixed_scale_vocabulary_units():
    assert envelope("50.00", "percent", scale=2, **DERIVED)["value"] == "50.00"
    assert envelope("8.0000", "percent_annual", scale=4, **DERIVED)["value"] == "8.0000"
    assert envelope("1.50", "multiple", scale=2, **DERIVED)["value"] == "1.50"
    assert envelope("200.0", "bp", scale=1, **DERIVED)["value"] == "200.0"
    assert envelope("18.0", "months", scale=1, **DERIVED)["value"] == "18.0"


def test_plain_string_units_carry_no_scale():
    env = envelope("2026-11-10", "date", **EXTERNAL)
    assert env["value"] == "2026-11-10"
    assert "scale" not in env
    with pytest.raises(ValueError, match="must not carry scale"):
        envelope("2026-11-10", "date", scale=0, **EXTERNAL)
    with pytest.raises(ValueError, match="STATUS_VALUES"):
        envelope("unknown", "status", **DERIVED)
    assert envelope("feasible", "status", **DERIVED)["value"] == "feasible"


def test_band_values_require_citation_band_and_access_date():
    with pytest.raises(ValueError, match="band value requires citation"):
        envelope("1.1500", "ratio", scale=4, **DERIVED, band="dscr_floor_1_15x_reference")
    with pytest.raises(ValueError, match="band value requires band"):
        envelope(
            "1.1500",
            "ratio",
            scale=4,
            **DERIVED,
            citation="Example source (accessed 2026-11-10)",
        )
    with pytest.raises(ValueError, match="access date"):
        envelope(
            "1.1500", "ratio", scale=4, **DERIVED, citation="No date here", band="b"
        )
    ok = envelope(
        "1.1500",
        "ratio",
        scale=4,
        **DERIVED,
        citation="SBA DSCR reference via 2026-11-10 research (accessed 2026-11-10)",
        band="dscr_floor_1_15x_reference",
    )
    assert ok["band"] == "dscr_floor_1_15x_reference"


def test_provenance_classes_exactly_one_with_companions():
    with pytest.raises(ValueError, match="derived provenance requires asof"):
        envelope("1.1500", "ratio", scale=4, provenance="derived: min DSCR")
    with pytest.raises(ValueError, match="generated provenance requires asof"):
        envelope("1.1500", "ratio", scale=4, provenance="synthetic seed=20261110 generator v1")
    assert envelope("1.1500", "ratio", scale=4, **GENERATED)["asof"] == "2026-11-10"
    with pytest.raises(ValueError, match="source_timestamp"):
        envelope("4.0000", "percent_annual", scale=4, source="fixture pinned rate")
    assert envelope("4.0000", "percent_annual", scale=4, **EXTERNAL)["source"]
    with pytest.raises(ValueError, match="multiple provenance classes"):
        envelope("1.1500", "ratio", scale=4, **DERIVED, **EXTERNAL)
    with pytest.raises(ValueError, match="exactly one provenance class"):
        envelope("1.1500", "ratio", scale=4)


def test_status_companions_enforced_by_validate_payload():
    companions = {
        "infeasible": "binding_constraint",
        "not_applicable": "reason",
        "shortfall": "residual",
        "no_solution": "reason",
    }
    for status, companion in companions.items():
        missing = {"metrics": {"s": envelope(status, "status", **DERIVED)}}
        errors = validate_payload(missing)
        assert len(errors) == 1, (status, errors)
        assert companion in errors[0], (status, errors)
        present = {
            "metrics": {"s": envelope(status, "status", **DERIVED), companion: "named"}
        }
        assert validate_payload(present) == []
    for status in ("feasible", "not_granted"):
        payload = {"metrics": {"s": envelope(status, "status", **DERIVED)}}
        assert validate_payload(payload) == []


def test_valid_payload_returns_no_errors():
    payload = {
        "facility": envelope(700000000, "usd_minor_units", **DERIVED),
        "min_dscr": envelope("1.2602", "ratio", scale=4, **DERIVED),
        "close": envelope("2026-11-10", "date", **EXTERNAL),
        "sizing_status": envelope("feasible", "status", **DERIVED),
    }
    assert validate_payload(payload) == []


def test_validate_payload_reports_floats():
    errors = validate_payload({"a": 0.5})
    assert any("float" in error for error in errors)


def test_canonical_dumps_sorts_keys_and_ends_with_one_newline():
    payload = {"zeta": {"b": 1, "a": 2}, "alpha": 0}
    text = canonical_dumps(payload)
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "\r" not in text
    assert text.index('"a"') < text.index('"b"')
    assert text.index('"alpha"') < text.index('"zeta"')
    expected = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert text == expected


def test_canonical_dumps_rejects_float_anywhere():
    with pytest.raises(TypeError, match="float"):
        canonical_dumps({"a": {"value": 0.5, "unit": "count", "scale": 0}})
    with pytest.raises(TypeError, match="float"):
        canonical_dumps({"rows": [1, 2.25]})
    with pytest.raises(TypeError, match="float"):
        canonical_dumps({"top": 1.5})


def test_canonical_dumps_round_trip_is_stable():
    payload = {"b": [1, "2.00"], "a": {"nested": True}}
    first = canonical_dumps(payload)
    assert canonical_dumps(payload) == first
    assert canonical_dumps(json.loads(first)) == first


def test_write_payload_returns_sha256_of_written_bytes(tmp_path: Path):
    payload = {"metric": envelope("1.2602", "ratio", scale=4, **DERIVED)}
    path = tmp_path / "payloads" / "out.json"
    digest = write_payload(path, payload)
    data = path.read_bytes()
    assert len(digest) == 64
    assert all(char in "0123456789abcdef" for char in digest)
    assert hashlib.sha256(data).hexdigest() == digest
    assert data == canonical_dumps(payload).encode("utf-8")
    assert data.endswith(b"\n")
