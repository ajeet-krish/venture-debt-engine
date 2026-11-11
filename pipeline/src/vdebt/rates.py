"""Committed Bank of Canada Valet rate reader (architecture P2.1).

Reads the raw Valet response committed under pipeline/data/rates/ and never
touches the network. SHA256SUMS is verified in both directions before any
JSON is parsed: a listed file that is missing, a committed .json file that is
unlisted, a listed entry that is not a .json file, or a digest mismatch each
raise RateError with a precise message, so a missing or tampered commit fails
loudly instead of fabricating a rate.

The committed document is the response of GET
https://www.bankofcanada.ca/valet/observations/<CODE>/json?recent=90: a
seriesDetail object keyed by the requested series code and carrying a label,
plus an observations list whose entries nest the value under that same code,
for example {"d": "2026-11-10", "TB.CDN.90D.MID": {"v": "2.61"}}, with the
value null on days the series does not publish. The list arrives in
descending date order, so the base observation is the LATEST date among the
entries with a non-null value (a lexicographic compare on the ISO date),
never merely the last list entry. The flat {"d": ..., "v": ...} shape is
accepted too, so both stay readable. The chosen value is parsed with Decimal
and quantized exactly once to percent_annual scale 4 (so "2.74" becomes
"2.7400"), and its date must be a business day because it is the book close
date. The label is asserted against the pattern pinned in scenario.toml
[rates]; a mismatch raises SeriesLabelError printing the series code, the
expected pattern, and the actual label verbatim.

Stdlib only: json, hashlib, decimal, datetime, pathlib, and re for the
manifest line and the configured label pattern. No network imports.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from vdebt.config import RatesConfig
from vdebt.dates import is_business_day
from vdebt.money import quantize

# Published base-rate scale: percent_annual with four decimal places.
PERCENT_ANNUAL_SCALE: int = 4

# External provenance source string for the published base-rate envelope.
VALET_SOURCE: str = "Bank of Canada Valet"

# Manifest file name pinned in architecture P2.1.
MANIFEST_NAME: str = "SHA256SUMS"

# One manifest line: 64 lowercase hex characters, two ASCII spaces, file name.
_MANIFEST_LINE: re.Pattern[str] = re.compile(r"([0-9a-f]{64})  (\S+)")


class RateError(Exception):
    """Committed rate data problem: manifest, file, response shape, or date."""


class SeriesLabelError(RateError):
    """Series label does not match the pattern pinned in scenario.toml."""


@dataclass(frozen=True)
class RateSnapshot:
    """One committed base-rate observation with its provenance.

    base_rate is percent_annual quantized to scale 4 (for example "2.7400").
    observation_date is the business-day date of the base observation and
    doubles as source_timestamp in ISO YYYY-MM-DD form. manifest_hashes
    carries the verified SHA256SUMS mapping returned by verify_manifest.
    """

    series: str
    label: str
    observation_date: date
    base_rate: Decimal
    source: str
    source_timestamp: str
    manifest_hashes: dict[str, str]


def read_manifest(rates_dir: Path) -> dict[str, str]:
    """Parse rates_dir/SHA256SUMS into a {file name: hex digest} mapping.

    Each line must be 64 lowercase hex characters, two ASCII spaces, and a
    file name, terminated by LF with no carriage return anywhere. Entries
    must be sorted by file name and unique. Raises RateError on a missing,
    unreadable, or malformed manifest.
    """
    if not isinstance(rates_dir, Path):
        raise TypeError(f"rates_dir must be a Path, got {type(rates_dir).__name__}")
    path = rates_dir / MANIFEST_NAME
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RateError(f"rate manifest missing or unreadable at {path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RateError(f"rate manifest at {path} is not valid UTF-8: {exc}") from exc
    if "\r" in text:
        raise RateError(f"rate manifest at {path} must use LF line endings only, found CR")
    if text and not text.endswith("\n"):
        raise RateError(f"rate manifest at {path} must end with a trailing LF newline")
    entries: dict[str, str] = {}
    previous: str | None = None
    lines = text.split("\n")[:-1] if text else []
    for lineno, line in enumerate(lines, start=1):
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            raise RateError(
                f"rate manifest {path} line {lineno}: expected 64 lowercase hex "
                f"characters, two ASCII spaces, and a file name, got {line!r}"
            )
        digest, name = match.group(1), match.group(2)
        if previous is not None and name < previous:
            raise RateError(
                f"rate manifest {path} line {lineno}: entries must be sorted by "
                f"file name: {name!r} follows {previous!r}"
            )
        if name in entries:
            raise RateError(f"rate manifest {path} line {lineno}: duplicate file name {name!r}")
        entries[name] = digest
        previous = name
    return entries


def verify_manifest(rates_dir: Path) -> dict[str, str]:
    """Verify SHA256SUMS against rates_dir and return the {name: digest} mapping.

    The check is bidirectional: a listed file that is missing fails, a
    committed .json file that is unlisted fails, and a digest mismatch fails.
    A listed entry that is not a .json file also fails, because the manifest
    lists exactly the committed .json files and nothing else. Every failure
    raises RateError with the file name and both digests where relevant.
    """
    entries = read_manifest(rates_dir)
    listed = set(entries)
    present = {item.name for item in rates_dir.glob("*.json") if item.is_file()}
    for name in sorted(listed):
        if not name.endswith(".json"):
            raise RateError(
                f"rate manifest entry {name!r} is not a .json file; the manifest "
                f"lists exactly the committed .json files under {rates_dir}"
            )
    for name in sorted(listed - present):
        raise RateError(f"rate manifest lists {name} but the file is missing under {rates_dir}")
    for name in sorted(present - listed):
        raise RateError(
            f"rate file {name} is present under {rates_dir} but not listed in {MANIFEST_NAME}"
        )
    for name in sorted(entries):
        expected = entries[name]
        actual = hashlib.sha256((rates_dir / name).read_bytes()).hexdigest()
        if actual != expected:
            raise RateError(
                f"SHA256 mismatch for {name}: manifest has {expected}, file hashes to {actual}"
            )
    return dict(entries)


def assert_series_label(series: str, label: str, cfg: RatesConfig) -> None:
    """Raise SeriesLabelError unless cfg.label_pattern matches the label.

    The pattern is the regex pinned in scenario.toml [rates], compiled here
    with re.compile. On a mismatch the message prints the series code, the
    expected pattern, and the actual label verbatim, so a wrong or renamed
    series fails loudly at engine load and in the manual refresh workflow.
    """
    pattern = re.compile(cfg.label_pattern)
    if pattern.search(label) is None:
        raise SeriesLabelError(
            f"series {series}: label does not match expected pattern "
            f"'{cfg.label_pattern}'; actual label: '{label}'"
        )


def load_rate_snapshot(rates_dir: Path, cfg: RatesConfig) -> RateSnapshot:
    """Load and verify the committed snapshot for cfg.series.

    Steps in order: verify the manifest in both directions, read and parse
    <series>.json, assert the response seriesDetail key equals the requested
    series code, take the entry with the latest date among the observations
    whose value is non-null (the live list arrives descending, so list order
    never decides the base), parse that value with Decimal and quantize once
    to percent_annual scale 4, require the observation date to be a business
    day, and assert the series label against cfg.label_pattern. Every failure
    raises RateError (or its subclass SeriesLabelError) with a precise
    message; a missing manifest or a missing series file is a loud failure
    and never a fabricated rate.
    """
    entries = verify_manifest(rates_dir)
    series = cfg.series
    if "/" in series or "\\" in series or series in (".", ".."):
        raise RateError(f"invalid series code in configuration: {series!r}")
    path = rates_dir / f"{series}.json"
    if not path.is_file():
        raise RateError(
            f"committed rate file missing: {path}; commit {series}.json with an "
            f"updated {MANIFEST_NAME} before loading a snapshot"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RateError(f"cannot read committed rate file {path}: {exc}") from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise RateError(f"committed rate file {path} is not valid UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RateError(f"committed rate file {path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise RateError(
            f"{path}: response must be a JSON object, got {type(document).__name__}"
        )
    detail = document.get("seriesDetail")
    if not isinstance(detail, dict):
        raise RateError(f"{path}: missing or non-object 'seriesDetail' in the Valet response")
    if set(detail) != {series}:
        actual = ", ".join(sorted(detail)) if detail else "(none)"
        raise RateError(
            f"{path}: seriesDetail key mismatch: requested {series!r}, response has {actual}"
        )
    entry = detail[series]
    if not isinstance(entry, dict):
        raise RateError(f"{path}: seriesDetail[{series!r}] must be a JSON object")
    label = entry.get("label")
    if not isinstance(label, str) or not label:
        raise RateError(f"{path}: seriesDetail[{series!r}] missing a non-empty string 'label'")
    observations = document.get("observations")
    if not isinstance(observations, list) or not observations:
        raise RateError(f"{path}: missing or empty 'observations' list in the Valet response")
    candidates: dict[str, object] = {}
    for item in observations:
        if not isinstance(item, dict):
            raise RateError(
                f"{path}: every observation must be a JSON object, got {type(item).__name__}"
            )
        if "d" not in item:
            raise RateError(f"{path}: observation must carry a 'd' key: {item!r}")
        if "v" in item:
            value = item["v"]
        elif series in item:
            nested = item[series]
            if not isinstance(nested, dict) or "v" not in nested:
                raise RateError(
                    f"{path}: observation {series!r} entry must be an object "
                    f"carrying 'v': {item!r}"
                )
            value = nested["v"]
        else:
            raise RateError(
                f"{path}: observation must carry 'd' and either 'v' or a nested "
                f"{series!r} object: {item!r}"
            )
        if value is None:
            continue
        raw_date = item["d"]
        if not isinstance(raw_date, str):
            raise RateError(
                f"{path}: observation date must be a string, got {type(raw_date).__name__}"
            )
        candidates[raw_date] = value
    if not candidates:
        raise RateError(f"{path}: no observation carries a non-null value")
    # ISO YYYY-MM-DD dates compare lexicographically, so the lexicographic
    # maximum is the latest date. The live list arrives descending, so list
    # order alone would pick the newest first and a planted older non-null
    # entry at the end would be ignored; the maximum keeps both orders correct.
    raw_date = max(candidates)
    raw_value = candidates[raw_date]
    try:
        observation_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise RateError(
            f"{path}: observation date {raw_date!r} is not an ISO YYYY-MM-DD date"
        ) from exc
    if not is_business_day(observation_date):
        raise RateError(
            f"{path}: observation date {observation_date.isoformat()} is not a "
            "business day (Monday to Friday, no holiday table)"
        )
    if isinstance(raw_value, bool) or not isinstance(raw_value, (str, int)):
        raise RateError(
            f"{path}: observation value must be a decimal string, got "
            f"{type(raw_value).__name__}"
        )
    try:
        rate = Decimal(raw_value)
    except InvalidOperation as exc:
        raise RateError(f"{path}: observation value {raw_value!r} is not a valid decimal") from exc
    if not rate.is_finite():
        raise RateError(f"{path}: observation value {raw_value!r} must be finite")
    base_rate = quantize(rate, PERCENT_ANNUAL_SCALE)
    assert_series_label(series, label, cfg)
    return RateSnapshot(
        series=series,
        label=label,
        observation_date=observation_date,
        base_rate=base_rate,
        source=VALET_SOURCE,
        source_timestamp=observation_date.isoformat(),
        manifest_hashes=entries,
    )
