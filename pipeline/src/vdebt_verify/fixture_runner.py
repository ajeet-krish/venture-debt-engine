"""Fixture validation for phase 1 (architecture section 8, validate_only).

Loads each of the six golden fixture files under pipeline/fixtures/golden,
enforces the envelope rules, and treats an absent file as an error reported as
"missing fixture: <path>". All six fixtures F1 to F6 are committed in phase 1,
so a missing file fails the gate instead of skipping it. Compare mode against
engine-emitted fixture outputs arrives in phase 3; both sides are read as plain
JSON and the engine is never imported here.

Envelope rules enforced in validate_only mode:
- fixture_id is one of F1 to F6 and matches the file mapping;
- title, authored_by, authored_phase, and rate_source are present and non-empty;
- rate_source equals "fixture_pinned";
- arithmetic is a non-empty list of non-empty strings;
- no reference to Valet or pipeline/data/rates anywhere in the file except the
  one mandated fixture rate source string;
- numeric leaves are int or fixed-scale strings carrying unit and scale, never
  floats; bool envelope values are rejected, and null is tolerated only as an
  absent value for infeasible cases;
- tolerances keys are only IRR fields, and each tolerance entry is an object
  with exactly an "abs" key whose value is a positive decimal string.

The SHA256 of every file read is recorded so the gate can print it.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from vdebt_verify import dash_scan

FIXTURE_FILES: dict[str, str] = {
    "F1": "sizing_fixture.json",
    "F2": "schedule_bullet.json",
    "F3": "schedule_io_amort.json",
    "F4": "schedule_rbf.json",
    "F5": "bridge_fixture.json",
    "F6": "rate_shock_fixture.json",
}
VALID_FIXTURE_IDS = frozenset(FIXTURE_FILES)
REQUIRED_FIELDS = ("title", "authored_by", "authored_phase", "rate_source")
RATE_SOURCE_FIELD = "fixture_pinned"
MANDATED_RATE_SOURCE_STRING = "fixture pinned rate, not a Valet observation"
NUMERIC_STRING_RE = re.compile(r"-?\d+(?:\.\d+)?")


def default_fixture_dir() -> Path:
    """Golden fixture directory resolved relative to the package location."""
    return dash_scan.default_root() / "pipeline" / "fixtures" / "golden"


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(dash_scan.default_root()).as_posix()
    except ValueError:
        return str(path)


@dataclass
class FixtureReport:
    """Result of one fixture validation run.

    errors is non-empty on any problem, including a missing fixture file, so
    passed is False and status() is FAIL. skipped stays empty now that an
    absent file is an error rather than a skip.
    """

    validated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    read_files: list[tuple[str, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors

    def status(self) -> str:
        if self.errors:
            return "FAIL"
        if not self.validated:
            return "SKIP"
        return "PASS"

    def summary(self) -> str:
        return f"{len(self.validated)} validated, {len(self.skipped)} skipped"


def _check_scale(where: str, text: str, scale: object, errors: list[str]) -> None:
    if not isinstance(scale, int) or isinstance(scale, bool):
        return  # missing or invalid scale is already reported by _require_unit_scale
    decimals = len(text.split(".")[1]) if "." in text else 0
    if scale != decimals:
        errors.append(f"{where}.scale {scale} does not match {text!r} ({decimals} decimals)")


def _require_unit_scale(where: str, node: dict[str, object], errors: list[str]) -> None:
    unit = node.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        errors.append(f"{where}: numeric value needs a non-empty 'unit'")
    scale = node.get("scale")
    if not isinstance(scale, int) or isinstance(scale, bool) or scale < 0:
        errors.append(f"{where}: numeric value needs an integer 'scale' of 0 or more")


def _check_envelope(where: str, node: dict[str, object], errors: list[str]) -> None:
    value = node.get("value")
    if value is None:
        return  # infeasible cases may carry an explicit empty value
    if isinstance(value, (float, bool)):
        errors.append(f"{where}.value must be an int or a fixed-scale string, got {value!r}")
        return
    if isinstance(value, dict):
        return  # nested object; the walker visits it separately
    if isinstance(value, list):
        _require_unit_scale(where, node, errors)
        return
    numeric = isinstance(value, str) and NUMERIC_STRING_RE.fullmatch(value) is not None
    if isinstance(value, int) or numeric:
        _require_unit_scale(where, node, errors)
    if numeric:
        _check_scale(where, str(value), node.get("scale"), errors)


def _walk(where: str, node: object, errors: list[str]) -> None:
    if isinstance(node, float):
        errors.append(f"{where}: float {node!r} is banned; use int or a fixed-scale string")
        return
    if isinstance(node, dict):
        has_value = "value" in node
        if has_value:
            _check_envelope(where, node, errors)
        for key, child in node.items():
            if has_value and key == "value" and not isinstance(child, (dict, list)):
                continue  # scalar envelope values are checked by _check_envelope
            _walk(f"{where}.{key}", child, errors)
        return
    if isinstance(node, list):
        for index, child in enumerate(node):
            _walk(f"{where}[{index}]", child, errors)


def _validate_tolerance_entry(where: str, entry: object, errors: list[str]) -> None:
    """Require one table with exactly an 'abs' key holding a positive decimal string."""
    if not isinstance(entry, dict):
        errors.append(f"{where}: tolerance entry must be an object with exactly an 'abs' key")
        return
    if set(entry) != {"abs"}:
        keys = ", ".join(sorted(entry)) if entry else "<none>"
        errors.append(f"{where}: tolerance entry must have exactly an 'abs' key, got {keys}")
        return
    value = entry["abs"]
    if not isinstance(value, str) or NUMERIC_STRING_RE.fullmatch(value) is None:
        errors.append(f"{where}.abs must be a decimal string, got {value!r}")
        return
    if Decimal(value) <= 0:
        errors.append(f"{where}.abs must be positive, got {value!r}")


def _validate_fixture(label: str, expected_id: str, raw: str) -> list[str]:
    errors: list[str] = []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"{label}: invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return [f"{label}: top level must be a JSON object"]

    fixture_id = data.get("fixture_id")
    if not isinstance(fixture_id, str) or fixture_id not in VALID_FIXTURE_IDS:
        errors.append(f"{label}: fixture_id {fixture_id!r} is not one of F1 to F6")
    elif fixture_id != expected_id:
        errors.append(
            f"{label}: fixture_id {fixture_id!r} does not match file mapping {expected_id}"
        )

    for key in REQUIRED_FIELDS:
        if key not in data:
            errors.append(f"{label}: missing required field '{key}'")
        elif data[key] is None or (isinstance(data[key], str) and not data[key].strip()):
            errors.append(f"{label}: required field '{key}' is empty")

    rate_source = data.get("rate_source")
    if rate_source is not None and rate_source != RATE_SOURCE_FIELD:
        errors.append(f"{label}: rate_source {rate_source!r} must be {RATE_SOURCE_FIELD!r}")

    arithmetic = data.get("arithmetic")
    if not isinstance(arithmetic, list) or not arithmetic:
        errors.append(f"{label}: arithmetic must be a non-empty list")
    else:
        for index, step in enumerate(arithmetic):
            if not isinstance(step, str) or not step.strip():
                errors.append(f"{label}: arithmetic[{index}] must be a non-empty string")

    scrubbed = raw.replace(MANDATED_RATE_SOURCE_STRING, "")
    lowered = scrubbed.lower()
    if "valet" in lowered:
        errors.append(f"{label}: references Valet outside the mandated fixture rate source string")
    if "pipeline/data/rates" in lowered:
        errors.append(f"{label}: references pipeline/data/rates; fixtures must be self-contained")

    if isinstance(data, dict):
        _walk(label, data, errors)

    tolerances = data.get("tolerances")
    if tolerances is not None:
        if not isinstance(tolerances, dict):
            errors.append(f"{label}: tolerances must be an object")
        else:
            for key, entry in tolerances.items():
                if "irr" not in key.lower():
                    errors.append(f"{label}: tolerance key {key!r} is not an IRR field")
                _validate_tolerance_entry(f"{label}: tolerances.{key}", entry, errors)

    return errors


def run_fixture_validation(fixture_dir: Path | None = None) -> FixtureReport:
    """Validate every fixture file under fixture_dir; an absent file is an error.

    All six fixtures F1 to F6 are committed in phase 1, so a missing file is
    reported as "missing fixture: <path>" and the report fails (phase 1 item 3:
    the gate exits non-zero on any fixture problem).
    """
    base = fixture_dir if fixture_dir is not None else default_fixture_dir()
    report = FixtureReport()
    for fixture_id, filename in FIXTURE_FILES.items():
        path = base / filename
        if not path.is_file():
            report.errors.append(f"missing fixture: {_display_path(path)}")
            continue
        try:
            raw_bytes = path.read_bytes()
        except OSError as exc:
            report.errors.append(f"{filename}: unreadable ({exc})")
            continue
        digest = hashlib.sha256(raw_bytes).hexdigest()
        report.read_files.append((_display_path(path), digest))
        try:
            raw = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            report.errors.append(f"{filename}: not valid UTF-8 ({exc})")
            continue
        errors = _validate_fixture(filename, fixture_id, raw)
        if errors:
            report.errors.extend(errors)
        else:
            report.validated.append(fixture_id)
    return report
