"""Manual rate refresh workflow: the only network code in the repository.

Implements the architecture P2.1 manual refresh workflow with the standard
library only: argparse, urllib.request (and urllib.error), json, hashlib, and
pathlib, plus vdebt.config and vdebt.rates from the engine. Run it from the
pipeline directory with `uv run python scripts/refresh_rates.py`.

Request strategy, verified against the live Valet API: the script makes ONE
GET of the observations endpoint
https://www.bankofcanada.ca/valet/observations/<CODE>/json?recent=90 because
that document already carries seriesDetail (keyed by the series code)
alongside observations. Only when the observations document lacks
seriesDetail does it make a second GET of the series-detail endpoint
https://www.bankofcanada.ca/valet/series/<CODE>/json, whose document uses
the seriesDetails shape (a name and label object rather than a keyed map).
The mode actually used is printed with the label check.

The live observation entries nest the value under the series code, for
example {"d": "2026-11-10", "TB.CDN.90D.MID": {"v": "2.61"}}, and the list
comes back in descending date order. The parser accepts that nested shape
and the flat {"d": ..., "v": ...} shape, and it picks the latest date among
the observations that carry a non-null value, so response order never
decides which observation is the base.

Steps in order: (1) fetch and parse the document; (2) extract seriesDetail
and assert the label through vdebt.rates.assert_series_label with the pattern
pinned in scenario.toml; on a mismatch the assertion message prints the actual
label and the script exits non-zero with NOTHING written; (3) parse the
observations and take the date and value of the latest non-null entry; (4)
write the raw response bytes to pipeline/data/rates/<CODE>.json; (5)
regenerate SHA256SUMS over exactly the committed *.json files (sorted by file
name, 64 lowercase hex characters, two ASCII spaces, file name, LF); (6)
print the old and new digests and the observation date.

With --check the script runs steps 1 to 3 and stops: it proves the fetch, the
label assertion, and the observation parse without writing any file, which is
how a candidate series is verified before a scenario.toml change lands.

Failure modes exit non-zero with a clear message: network error, HTTP error,
label mismatch, and bad JSON. Nothing is written before the label assertion
passes.
"""

import argparse
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

from vdebt.config import RatesConfig, load_scenario
from vdebt.rates import MANIFEST_NAME, SeriesLabelError, assert_series_label

# Repository layout resolved from this file so the script runs from any CWD.
PIPELINE_DIR: Path = Path(__file__).resolve().parents[1]
SCENARIO_PATH: Path = PIPELINE_DIR / "config" / "scenario.toml"
RATES_DIR: Path = PIPELINE_DIR / "data" / "rates"

# Bank of Canada Valet endpoints: public data, no API key. The observations
# path serves the document with observations; the series path serves only the
# series detail and is the label-check fallback.
VALET_OBSERVATIONS_URL: str = "https://www.bankofcanada.ca/valet/observations"
VALET_SERIES_URL: str = "https://www.bankofcanada.ca/valet/series"

# Observation window requested from the Valet endpoint, in calendar days.
RECENT_WINDOW_DAYS: int = 90

# Network timeout for each GET, in seconds.
REQUEST_TIMEOUT_SECONDS: int = 30


def _fail(message: str) -> NoReturn:
    """Print one clear error line and exit non-zero with nothing written."""
    print(f"ERROR: {message}")
    raise SystemExit(1)


def _get(url: str) -> bytes:
    """GET url and return the raw response bytes, or fail with a clear message."""
    print(f"GET {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "vdebt-refresh/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        _fail(f"HTTP error {exc.code} fetching {url}: {exc.reason}")
    except urllib.error.URLError as exc:
        _fail(f"network error fetching {url}: {exc.reason}")
    except TimeoutError as exc:
        _fail(f"timeout after {REQUEST_TIMEOUT_SECONDS}s fetching {url}: {exc}")


def _parse_json_document(raw: bytes, url: str) -> dict:
    """Decode and parse the response body as a JSON object, or fail clearly."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(f"response from {url} is not valid UTF-8: {exc}")
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        _fail(f"response from {url} is not valid JSON: {exc}")
    if not isinstance(document, dict):
        _fail(f"response from {url} must be a JSON object, got {type(document).__name__}")
    return document


def _series_label(document: dict, series: str, url: str) -> str:
    """Extract the keyed seriesDetail label for series from document, or fail."""
    detail = document.get("seriesDetail")
    if not isinstance(detail, dict):
        _fail(f"{url}: missing or non-object 'seriesDetail' in the Valet response")
    if set(detail) != {series}:
        actual = ", ".join(sorted(detail)) if detail else "(none)"
        _fail(f"{url}: seriesDetail key mismatch: requested {series!r}, response has {actual}")
    entry = detail[series]
    if not isinstance(entry, dict):
        _fail(f"{url}: seriesDetail[{series!r}] must be a JSON object")
    label = entry.get("label")
    if not isinstance(label, str) or not label:
        _fail(f"{url}: seriesDetail[{series!r}] missing a non-empty string 'label'")
    return label


def _detail_series_label(document: dict, series: str, url: str) -> str:
    """Extract the label from a series-detail document (the seriesDetails shape)."""
    detail = document.get("seriesDetails")
    if not isinstance(detail, dict):
        _fail(f"{url}: missing or non-object 'seriesDetails' in the Valet response")
    if detail.get("name") != series:
        _fail(
            f"{url}: seriesDetails name mismatch: requested {series!r}, "
            f"response has {detail.get('name')!r}"
        )
    label = detail.get("label")
    if not isinstance(label, str) or not label:
        _fail(f"{url}: seriesDetails missing a non-empty string 'label'")
    return label


def _observation_value(item: dict, series: str, url: str) -> str | None:
    """Return the observation value for series, or None when it is null.

    The live Valet shape nests the value under the series code:
    {"d": "2026-11-10", "TB.CDN.90D.MID": {"v": "2.61"}}. The flat
    {"d": ..., "v": ...} shape is accepted too, so both stay readable.
    """
    if "v" in item:
        value = item["v"]
    else:
        entry = item.get(series)
        value = entry.get("v") if isinstance(entry, dict) else None
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str):
        _fail(f"{url}: observation value must be a string, got {type(value).__name__}")
    return value


def _base_observation(document: dict, series: str, url: str) -> tuple[str, str]:
    """Return the (date, value) of the latest observation carrying a value.

    Dates are ISO YYYY-MM-DD, so the lexicographic maximum is the latest
    date; picking the maximum keeps the result correct no matter whether the
    response arrives ascending or descending.
    """
    observations = document.get("observations")
    if not isinstance(observations, list) or not observations:
        _fail(f"{url}: missing or empty 'observations' list in the Valet response")
    candidates: dict[str, str] = {}
    for item in observations:
        if not isinstance(item, dict):
            _fail(f"{url}: every observation must be a JSON object, got {type(item).__name__}")
        raw_date = item.get("d")
        if not isinstance(raw_date, str) or not raw_date:
            _fail(f"{url}: observation date must be a non-empty string, got {raw_date!r}")
        value = _observation_value(item, series, url)
        if value is not None:
            candidates[raw_date] = value
    if not candidates:
        _fail(f"{url}: no observation carries a non-null value")
    latest = max(candidates)
    return latest, candidates[latest]


def _previous_digest(rates_dir: Path, name: str) -> str:
    """Return the digest SHA256SUMS currently lists for name, or a short note."""
    manifest = rates_dir / MANIFEST_NAME
    if not manifest.is_file():
        return "(no manifest yet)"
    try:
        text = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "(manifest unreadable)"
    for line in text.splitlines():
        parts = line.split("  ", 1)
        if len(parts) == 2 and parts[1] == name:
            return parts[0]
    return "(not listed yet)"


def _write_manifest(rates_dir: Path) -> None:
    """Rewrite SHA256SUMS over every *.json file: sorted, two spaces, LF."""
    lines: list[str] = []
    for path in sorted(rates_dir.glob("*.json"), key=lambda item: item.name):
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    payload = "".join(f"{line}\n" for line in lines)
    (rates_dir / MANIFEST_NAME).write_bytes(payload.encode("utf-8"))


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser: series override and check-only mode."""
    parser = argparse.ArgumentParser(
        prog="refresh_rates.py",
        description="Fetch the Bank of Canada Valet snapshot and regenerate SHA256SUMS.",
    )
    parser.add_argument(
        "--series",
        default=None,
        help="series code override; default is rates.series from scenario.toml",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fetch, assert the label, and print the observation without writing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the refresh workflow; return 0 on success, exit non-zero on failure."""
    args = build_parser().parse_args(argv)

    try:
        scenario = load_scenario(SCENARIO_PATH)
    except (OSError, ValueError) as exc:
        _fail(f"cannot load scenario config {SCENARIO_PATH}: {exc}")
    rates_cfg = scenario.rates
    series = args.series if args.series is not None else rates_cfg.series
    if not series or "/" in series or "\\" in series or series in (".", ".."):
        _fail(f"refusing unsafe series code: {series!r}")
    pattern = (
        rates_cfg.fallback_label_pattern
        if series == rates_cfg.fallback_series
        else rates_cfg.label_pattern
    )
    check_cfg = RatesConfig(
        series=series,
        fallback_series=rates_cfg.fallback_series,
        label_pattern=pattern,
        fallback_label_pattern=rates_cfg.fallback_label_pattern,
    )

    observations_url = f"{VALET_OBSERVATIONS_URL}/{series}/json?recent={RECENT_WINDOW_DAYS}"
    detail_url = f"{VALET_SERIES_URL}/{series}/json"
    target = RATES_DIR / f"{series}.json"
    print(f"refresh: series {series}")
    print(f"target: {target}")

    # Step 1: fetch. One GET of the observations endpoint is enough when the
    # document carries seriesDetail; otherwise a second GET fetches detail.
    raw = _get(observations_url)
    document = _parse_json_document(raw, observations_url)
    if isinstance(document.get("seriesDetail"), dict):
        label = _series_label(document, series, observations_url)
        label_source = "one GET: seriesDetail from the observations document"
    else:
        detail_raw = _get(detail_url)
        detail_document = _parse_json_document(detail_raw, detail_url)
        label = _detail_series_label(detail_document, series, detail_url)
        label_source = "two GETs: label from the series-detail document"

    # Step 2: assert the label before anything is written; a mismatch prints
    # the actual label inside the assertion message and exits non-zero.
    try:
        assert_series_label(series, label, check_cfg)
    except SeriesLabelError as exc:
        _fail(str(exc))
    print(f"label: {label}")
    print(f"label check passed ({label_source})")

    # Step 3: parse the observations from the fetched document.
    observation_date, observation_value = _base_observation(document, series, observations_url)
    print(f"observation date: {observation_date}")
    print(f"observation value: {observation_value}")

    if args.check:
        print("check only: nothing written")
        return 0

    # Step 4: write the raw response bytes, then regenerate the manifest.
    old_digest = _previous_digest(RATES_DIR, target.name)
    RATES_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    new_digest = hashlib.sha256(raw).hexdigest()
    _write_manifest(RATES_DIR)

    # Step 5: report the digests and the observation date.
    print(f"old digest: {old_digest}")
    print(f"new digest: {new_digest}")
    print(f"observation date: {observation_date}")
    print(f"wrote {target}")
    print(f"wrote {RATES_DIR / MANIFEST_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
