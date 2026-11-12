"""Canonical snapshot writer: envelopes, payload validation, canonical JSON.

Every numeric leaf in a payload is an envelope: a mapping carrying value,
unit, and (for fixed-scale string units) scale, plus exactly one provenance
class. This module enforces the envelope validation matrix from architecture
section 6.4, walks a payload to collect validation errors instead of raising,
and serializes payloads to canonical JSON: UTF-8, LF line endings, sorted keys,
two-space indent, trailing newline, floats banned anywhere. Byte identity over
payloads is defined over this encoding (architecture section 11).
"""

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Literal, get_args

from vdebt.money import parse_fixed

Status = Literal[
    "breach",
    "feasible",
    "infeasible",
    "not_applicable",
    "not_granted",
    "shortfall",
    "no_solution",
]

STATUS_VALUES: frozenset[str] = frozenset(get_args(Status))

UNIT_VALUES: frozenset[str] = frozenset(
    {
        "usd_minor_units",
        "percent",
        "percent_annual",
        "multiple",
        "ratio",
        "bp",
        "months",
        "count",
        "date",
        "sha256_hex",
        "status",
    }
)

# Units whose value is a JSON integer; these never carry a scale key.
_INTEGER_UNITS: frozenset[str] = frozenset({"usd_minor_units", "count"})

# Units whose value is a plain string with no scale key.
_PLAIN_STRING_UNITS: frozenset[str] = frozenset({"date", "sha256_hex", "status"})

# Status value to required companion key, architecture section 6.4.
_STATUS_COMPANIONS: dict[str, str] = {
    "infeasible": "binding_constraint",
    "not_applicable": "reason",
    "shortfall": "residual",
    "no_solution": "reason",
}

# Generated provenance form pinned in architecture section 6.4.
_GENERATED_PROVENANCE = re.compile(r"synthetic seed=[0-9]+ generator v1")

# Access date inside a band citation, for example (accessed 2026-11-10).
_ACCESS_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

_STRING_METADATA_KEYS: tuple[str, ...] = (
    "provenance",
    "source",
    "source_timestamp",
    "asof",
    "citation",
    "band",
)


def _require_optional_str(mapping: Mapping[str, object], key: str) -> None:
    """Check that an optional metadata key, when present, is a non-empty string."""
    value = mapping.get(key)
    if value is None:
        return
    if not isinstance(value, str):
        # The envelope contract requires ValueError on every matrix violation.
        raise ValueError(f"{key} must be a string, got {type(value).__name__}")  # noqa: TRY004
    if not value.strip():
        raise ValueError(f"{key} must not be empty")


def _has_access_date(citation: str) -> bool:
    """Return True when the citation contains a valid YYYY-MM-DD access date."""
    for match in _ACCESS_DATE.finditer(citation):
        try:
            date.fromisoformat(match.group(0))
        except ValueError:
            continue
        return True
    return False


def _validate_provenance_class(mapping: Mapping[str, object]) -> None:
    """Require exactly one provenance class and its companion keys.

    External envelopes declare provenance with source and source_timestamp, so
    they must not carry a stray free-text provenance key that declares no class.
    """
    provenance = mapping.get("provenance")
    source = mapping.get("source")
    source_timestamp = mapping.get("source_timestamp")
    asof = mapping.get("asof")

    classes: list[str] = []
    if source is not None or source_timestamp is not None:
        classes.append("external")
    if isinstance(provenance, str) and provenance.startswith("derived: "):
        classes.append("derived")
    if isinstance(provenance, str) and _GENERATED_PROVENANCE.fullmatch(provenance):
        classes.append("generated")

    if not classes:
        if provenance is not None:
            raise ValueError(
                f"provenance {provenance!r} does not declare a provenance class; "
                "it must start with 'derived: ' or match 'synthetic seed=<seed> generator v1'"
            )
        raise ValueError(
            "exactly one provenance class required: "
            "external (source and source_timestamp), "
            "derived (provenance starting with 'derived: ' and asof), "
            "generated ('synthetic seed=<seed> generator v1' and asof)"
        )
    if len(classes) > 1:
        joined = ", ".join(classes)
        raise ValueError(f"multiple provenance classes present: {joined}; exactly one allowed")

    only = classes[0]
    if only == "external":
        if source is None:
            raise ValueError("external provenance requires source")
        if source_timestamp is None:
            raise ValueError("external provenance requires source_timestamp")
        if provenance is not None:
            raise ValueError(
                "external provenance (source and source_timestamp) must not carry a "
                f"stray free-text 'provenance' key: {provenance!r} declares no "
                "provenance class; drop it or declare derived/generated instead"
            )
        return
    if asof is None:
        raise ValueError(f"{only} provenance requires asof")


def _validate_band(mapping: Mapping[str, object]) -> None:
    """Require citation with an access date plus band on band values."""
    citation = mapping.get("citation")
    band = mapping.get("band")
    if citation is None and band is None:
        return
    if citation is None:
        raise ValueError("band value requires citation")
    if band is None:
        raise ValueError("band value requires band")
    if not _has_access_date(citation):
        raise ValueError(
            f"citation must contain an access date in YYYY-MM-DD form: {citation!r}"
        )


def _validate_envelope_mapping(mapping: Mapping[str, object]) -> None:
    """Raise ValueError on the first envelope validation matrix violation."""
    if "value" not in mapping:
        raise ValueError("envelope is missing required key 'value'")
    if "unit" not in mapping:
        raise ValueError("envelope is missing required key 'unit'")

    unit = mapping["unit"]
    if not isinstance(unit, str):
        # The envelope contract requires ValueError on every matrix violation.
        raise ValueError(f"unit must be a string, got {type(unit).__name__}")  # noqa: TRY004
    if unit not in UNIT_VALUES:
        allowed = ", ".join(sorted(UNIT_VALUES))
        raise ValueError(f"unit {unit!r} is not in the vocabulary: {allowed}")

    value = mapping["value"]
    scale = mapping.get("scale")

    if unit in _INTEGER_UNITS:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                f"unit {unit} requires a JSON integer value, "
                f"got {type(value).__name__} ({value!r})"
            )
        if scale is not None:
            raise ValueError(f"unit {unit} stores a JSON integer and must not carry scale")
    elif unit in _PLAIN_STRING_UNITS:
        if not isinstance(value, str):
            raise ValueError(
                f"unit {unit} requires a plain string value, "
                f"got {type(value).__name__} ({value!r})"
            )
        if scale is not None:
            raise ValueError(f"unit {unit} must not carry scale")
        if unit == "status" and value not in STATUS_VALUES:
            allowed = ", ".join(sorted(STATUS_VALUES))
            raise ValueError(f"status value {value!r} is not in STATUS_VALUES: {allowed}")
    else:
        if not isinstance(value, str):
            raise ValueError(
                f"unit {unit} requires a fixed-scale string value, "
                f"got {type(value).__name__} ({value!r})"
            )
        if scale is None:
            raise ValueError(f"unit {unit} requires scale")
        if isinstance(scale, bool) or not isinstance(scale, int):
            raise ValueError(f"scale must be an int, got {type(scale).__name__}")
        if scale < 0:
            raise ValueError(f"scale must be >= 0, got {scale}")
        try:
            parse_fixed(value, scale)
        except ValueError as exc:
            raise ValueError(
                f"value {value!r} is not a fixed-scale string with {scale} fraction "
                f"digits for unit {unit!r}"
            ) from exc

    for key in _STRING_METADATA_KEYS:
        _require_optional_str(mapping, key)

    _validate_provenance_class(mapping)
    _validate_band(mapping)


def envelope(
    value: int | str,
    unit: str,
    *,
    scale: int | None = None,
    provenance: str | None = None,
    source: str | None = None,
    source_timestamp: str | None = None,
    asof: str | None = None,
    citation: str | None = None,
    band: str | None = None,
) -> dict[str, object]:
    """Build a metric envelope and enforce the validation matrix.

    Raises ValueError with a precise message on any matrix violation: unit
    vocabulary, value shape (JSON integer for usd_minor_units and count,
    fixed-scale string with scale elsewhere, plain string with no scale for
    date, sha256_hex, and status), exactly one provenance class with its
    companions (external envelopes reject a stray free-text provenance key),
    and band values with citation carrying an access date plus
    band. Status companions (binding_constraint, reason, residual) travel as
    sibling keys of the status envelope and are checked by validate_payload,
    because this signature cannot carry them.
    """
    mapping: dict[str, object] = {"value": value, "unit": unit}
    optional: dict[str, object] = {
        "scale": scale,
        "provenance": provenance,
        "source": source,
        "source_timestamp": source_timestamp,
        "asof": asof,
        "citation": citation,
        "band": band,
    }
    for key, item in optional.items():
        if item is not None:
            mapping[key] = item
    _validate_envelope_mapping(mapping)
    return mapping


def _check_status_companion(
    mapping: Mapping[str, object],
    path: str,
    scope: Mapping[str, object] | None,
    errors: list[str],
) -> None:
    """Report a missing status companion in the envelope or its container."""
    unit = mapping.get("unit")
    value = mapping.get("value")
    if unit != "status" or not isinstance(value, str):
        return
    companion = _STATUS_COMPANIONS.get(value)
    if companion is None:
        return
    for candidate in (mapping, scope):
        if candidate is None:
            continue
        present = candidate.get(companion)
        if present is None:
            continue
        if isinstance(present, str) and not present.strip():
            continue
        return
    errors.append(
        f"{path}: status {value!r} requires companion key {companion!r} "
        "in the envelope or its containing object"
    )


def _walk(node: object, path: str, scope: Mapping[str, object] | None, errors: list[str]) -> None:
    """Walk a payload node, collecting errors with their location paths."""
    if isinstance(node, float):
        errors.append(f"{path}: float values are banned in payloads, got {node!r}")
        return
    if isinstance(node, Mapping):
        if "value" in node or "unit" in node:
            try:
                _validate_envelope_mapping(node)
            except ValueError as exc:
                errors.append(f"{path}: {exc}")
            _check_status_companion(node, path, scope, errors)
        for key, item in node.items():
            _walk(item, f"{path}.{key}", node, errors)
        return
    if isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            _walk(item, f"{path}[{index}]", None, errors)
        return
    if node is None or isinstance(node, (str, bool, int)):
        return
    errors.append(f"{path}: unsupported type {type(node).__name__} is not JSON serializable")


def validate_payload(payload: Mapping[str, object]) -> list[str]:
    """Return every validation error in the payload as prefixed strings.

    Unlike envelope(), this never raises: each violation becomes one error
    string rooted at its location, for example
    "$.metrics.min_dscr: derived provenance requires asof".
    """
    errors: list[str] = []
    _walk(payload, "$", None, errors)
    return errors


def _reject_floats(node: object, path: str) -> None:
    """Raise TypeError on any float value anywhere in the structure."""
    if isinstance(node, float):
        raise TypeError(
            f"float value at {path}: floats are banned in canonical payloads, got {node!r}"
        )
    if isinstance(node, Mapping):
        for key, item in node.items():
            _reject_floats(item, f"{path}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            _reject_floats(item, f"{path}[{index}]")


def canonical_dumps(payload: Mapping[str, object]) -> str:
    """Serialize payload to canonical JSON text ending in one LF.

    Keys sort recursively, indentation is two spaces, non-ASCII characters
    stay literal (ensure_ascii=False), NaN and Infinity are rejected
    (allow_nan=False), and any float anywhere in the structure raises
    TypeError before serialization. The text uses LF line endings only and
    ends with exactly one trailing newline.
    """
    _reject_floats(payload, "$")
    obj: object = payload if isinstance(payload, dict) else dict(payload)
    text = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return text


def write_payload(path: Path, payload: Mapping[str, object]) -> str:
    """Write payload as canonical UTF-8 JSON with LF and return its SHA256.

    The returned hex digest covers exactly the bytes written, so equal
    payloads produce equal digests (architecture section 11).
    """
    data = canonical_dumps(payload).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()
