"""Tests for vdebt.rates, the committed Bank of Canada Valet rate reader.

Covers architecture P2.1 and the binding test_rates list in the P2.7 test
plan: happy-path snapshot parse with a scale 4 base rate and full
provenance, manifest verification in both directions (tampered digest,
unlisted file, listed file missing, unsorted and malformed lines), the
series label assertion message (actual label, series code, and expected
pattern printed), seriesDetail key equality, one ROUND_HALF_UP quantize of
the observation value to percent_annual scale 4 with JSON floats rejected,
business-day observation dates, loud failure on a missing manifest or
series file, and the no-network confinement of the engine package.

Every rate fixture is built under tmp_path in the documented Valet response
shape (seriesDetail keyed by the requested series code plus an observations
list whose entries carry a date and either a flat or a nested value) with a
freshly hashed SHA256SUMS manifest, because the real pipeline/data/rates
directory does not exist yet. No test reads that directory and no test
performs any network call. The forbidden module name used by the confinement
tests is assembled from two literals so this file, which itself lives under
pipeline/, never references it directly.
"""

import ast
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from vdebt.config import RatesConfig, load_scenario
from vdebt.rates import (
    MANIFEST_NAME,
    RateError,
    RateSnapshot,
    SeriesLabelError,
    assert_series_label,
    load_rate_snapshot,
    read_manifest,
    verify_manifest,
)

PIPELINE_DIR = Path(__file__).resolve().parent.parent
SCENARIO_PATH = PIPELINE_DIR / "config" / "scenario.toml"
ENGINE_DIR = PIPELINE_DIR / "src" / "vdebt"
SCRIPTS_DIR = PIPELINE_DIR / "scripts"

# Loaded from the real committed scenario.toml, so the series code and the
# label pattern exercised below are exactly the pins the engine runs.
CFG = load_scenario(SCENARIO_PATH)
RATES_CFG: RatesConfig = CFG.rates

SERIES = RATES_CFG.series
FALLBACK_SERIES = RATES_CFG.fallback_series

# Weekday pins: 2026-11-06 is a Friday, 2026-11-07 is a Saturday.
FRIDAY = "2026-11-06"
SATURDAY = "2026-11-07"

# MATCHING_LABEL satisfies the second alternative of the pinned pattern
# (a 3, then month, then bill within the configured gaps). NON_MATCHING_LABEL
# contains neither bill nor month nor any digit, so it matches nothing.
MATCHING_LABEL = "Bank of Canada 3 month treasury bill"
NON_MATCHING_LABEL = "Bank of Canada prime rate"

# The forbidden module name is assembled from two string literals so that
# this file, which lives under pipeline/, does not itself carry the token
# that the confinement test scans raw file text for.
NET_LIB = "url" + "lib"


def _valet_document(
    series: str = SERIES,
    label: str = MATCHING_LABEL,
    observations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build one Valet response body in the documented shape.

    The default observation list ends with a null entry on a Saturday, so a
    successful load also proves the reader takes the latest non-null value
    rather than the last list entry.
    """
    if observations is None:
        observations = [
            {"d": "2026-11-04", "v": "2.70"},
            {"d": "2026-11-05", "v": "2.72"},
            {"d": FRIDAY, "v": "2.74"},
            {"d": SATURDAY, "v": None},
        ]
    return {
        "seriesDetail": {series: {"label": label}},
        "observations": observations,
    }


def _write_manifest(rates_dir: Path, lines: list[str] | None = None) -> None:
    """Write SHA256SUMS for the *.json files in rates_dir.

    The generated format is the pinned one: 64 lowercase hex characters,
    two ASCII spaces, file name, sorted by file name, LF line endings with
    a trailing newline. Pass explicit lines to plant an unsorted or
    malformed manifest instead.
    """
    if lines is None:
        paths = sorted(rates_dir.glob("*.json"), key=lambda path: path.name)
        lines = [
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
            for path in paths
        ]
    text = "".join(f"{line}\n" for line in lines)
    (rates_dir / MANIFEST_NAME).write_text(text, encoding="utf-8")


def _write_json_rates(tmp_path: Path, documents: dict[str, dict[str, object]]) -> Path:
    """Write Valet JSON documents plus a regenerated manifest; return the dir."""
    rates_dir = tmp_path / "rates"
    rates_dir.mkdir(parents=True, exist_ok=True)
    for name, body in documents.items():
        (rates_dir / name).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    _write_manifest(rates_dir)
    return rates_dir


def test_load_rate_snapshot_happy_path(tmp_path: Path) -> None:
    rates_dir = _write_json_rates(tmp_path, {f"{SERIES}.json": _valet_document()})

    snapshot = load_rate_snapshot(rates_dir, RATES_CFG)

    assert isinstance(snapshot, RateSnapshot)
    assert snapshot.series == SERIES
    assert snapshot.label == MATCHING_LABEL
    assert isinstance(snapshot.observation_date, date)
    assert not isinstance(snapshot.observation_date, datetime)
    assert snapshot.observation_date == date(2026, 11, 6)
    assert snapshot.base_rate == Decimal("2.7400")
    assert str(snapshot.base_rate) == "2.7400"
    assert snapshot.base_rate.as_tuple().exponent == -4
    assert snapshot.source == "Bank of Canada Valet"
    assert snapshot.source_timestamp == "2026-11-06"
    assert snapshot.source_timestamp == snapshot.observation_date.isoformat()
    digest = hashlib.sha256((rates_dir / f"{SERIES}.json").read_bytes()).hexdigest()
    assert snapshot.manifest_hashes == {f"{SERIES}.json": digest}


def test_label_pattern_from_real_scenario_accepts_matching_label() -> None:
    cfg = load_scenario(SCENARIO_PATH).rates
    assert cfg.label_pattern == RATES_CFG.label_pattern
    assert_series_label(cfg.series, MATCHING_LABEL, cfg)


def test_manifest_tampered_byte_fails_with_digest_wording(tmp_path: Path) -> None:
    rates_dir = _write_json_rates(tmp_path, {f"{SERIES}.json": _valet_document()})
    path = rates_dir / f"{SERIES}.json"
    original = path.read_bytes()
    # Same length rewrite after hashing: one character of the committed
    # observation value changes, so only the digest can catch it.
    tampered = original.replace(b"2.74", b"2.75", 1)
    assert tampered != original
    assert len(tampered) == len(original)
    path.write_bytes(tampered)

    with pytest.raises(RateError) as excinfo:
        verify_manifest(rates_dir)

    message = str(excinfo.value)
    assert "SHA256 mismatch" in message
    assert f"{SERIES}.json" in message
    assert hashlib.sha256(original).hexdigest() in message
    assert hashlib.sha256(tampered).hexdigest() in message


def test_manifest_unlisted_json_file_fails(tmp_path: Path) -> None:
    rates_dir = _write_json_rates(tmp_path, {f"{SERIES}.json": _valet_document()})
    # A committed JSON file that appeared after the manifest was written.
    (rates_dir / f"{FALLBACK_SERIES}.json").write_text(
        json.dumps(_valet_document(series=FALLBACK_SERIES)), encoding="utf-8"
    )

    with pytest.raises(RateError) as excinfo:
        verify_manifest(rates_dir)

    message = str(excinfo.value)
    assert f"{FALLBACK_SERIES}.json" in message
    assert f"not listed in {MANIFEST_NAME}" in message


def test_manifest_listed_missing_file_fails(tmp_path: Path) -> None:
    rates_dir = _write_json_rates(tmp_path, {f"{SERIES}.json": _valet_document()})
    (rates_dir / f"{SERIES}.json").unlink()

    with pytest.raises(RateError) as excinfo:
        verify_manifest(rates_dir)

    message = str(excinfo.value)
    assert f"{SERIES}.json" in message
    assert "but the file is missing" in message


def test_manifest_unsorted_lines_fail(tmp_path: Path) -> None:
    rates_dir = _write_json_rates(tmp_path, {f"{SERIES}.json": _valet_document()})
    _write_manifest(
        rates_dir,
        [f"{'b' * 64}  Zebra.json", f"{'a' * 64}  Alpha.json"],
    )

    with pytest.raises(RateError) as excinfo:
        read_manifest(rates_dir)

    message = str(excinfo.value)
    assert "sorted by file name" in message
    assert "Alpha.json" in message
    assert "Zebra.json" in message


@pytest.mark.parametrize(
    ("manifest_text", "fragment"),
    [
        pytest.param(
            f"{'0' * 64} TB.CDN.90D.MID.json\n", "two ASCII spaces", id="single-space"
        ),
        pytest.param(
            f"{'z' * 64}  TB.CDN.90D.MID.json\n", "64 lowercase hex", id="non-hex-digest"
        ),
        pytest.param(
            f"{'0' * 64}  TB.CDN 90D.MID.json\n", "file name", id="file-name-with-space"
        ),
        pytest.param(
            f"{'0' * 64}  TB.CDN.90D.MID.json",
            "trailing LF newline",
            id="no-trailing-newline",
        ),
        pytest.param(
            f"{'0' * 64}  TB.CDN.90D.MID.json\r\n",
            "LF line endings",
            id="crlf-line-endings",
        ),
    ],
)
def test_manifest_malformed_line_fails(
    tmp_path: Path, manifest_text: str, fragment: str
) -> None:
    rates_dir = tmp_path / "rates"
    rates_dir.mkdir()
    (rates_dir / MANIFEST_NAME).write_bytes(manifest_text.encode("utf-8"))

    with pytest.raises(RateError) as excinfo:
        read_manifest(rates_dir)

    assert fragment in str(excinfo.value)


def test_snapshot_label_mismatch_prints_label_series_and_pattern(
    tmp_path: Path,
) -> None:
    rates_dir = _write_json_rates(
        tmp_path, {f"{SERIES}.json": _valet_document(label=NON_MATCHING_LABEL)}
    )

    with pytest.raises(SeriesLabelError) as excinfo:
        load_rate_snapshot(rates_dir, RATES_CFG)

    assert isinstance(excinfo.value, RateError)
    message = str(excinfo.value)
    assert NON_MATCHING_LABEL in message
    assert SERIES in message
    assert RATES_CFG.label_pattern in message


def test_assert_series_label_mismatch_message() -> None:
    with pytest.raises(SeriesLabelError) as excinfo:
        assert_series_label(SERIES, NON_MATCHING_LABEL, RATES_CFG)

    message = str(excinfo.value)
    assert NON_MATCHING_LABEL in message
    assert SERIES in message
    assert RATES_CFG.label_pattern in message


def test_series_detail_key_mismatch_fails(tmp_path: Path) -> None:
    rates_dir = _write_json_rates(
        tmp_path, {f"{SERIES}.json": _valet_document(series=FALLBACK_SERIES)}
    )

    with pytest.raises(RateError) as excinfo:
        load_rate_snapshot(rates_dir, RATES_CFG)

    message = str(excinfo.value)
    assert "seriesDetail key mismatch" in message
    assert SERIES in message
    assert FALLBACK_SERIES in message


def test_missing_series_detail_fails(tmp_path: Path) -> None:
    document = _valet_document()
    del document["seriesDetail"]
    rates_dir = _write_json_rates(tmp_path, {f"{SERIES}.json": document})

    with pytest.raises(RateError) as excinfo:
        load_rate_snapshot(rates_dir, RATES_CFG)

    assert "seriesDetail" in str(excinfo.value)


def test_all_null_observations_fail_loudly(tmp_path: Path) -> None:
    document = _valet_document(observations=[{"d": FRIDAY, "v": None}])
    rates_dir = _write_json_rates(tmp_path, {f"{SERIES}.json": document})

    with pytest.raises(RateError) as excinfo:
        load_rate_snapshot(rates_dir, RATES_CFG)

    assert "non-null value" in str(excinfo.value)


def test_observation_value_quantized_half_up_at_scale_4(tmp_path: Path) -> None:
    document = _valet_document(observations=[{"d": FRIDAY, "v": "2.74565"}])
    rates_dir = _write_json_rates(tmp_path, {f"{SERIES}.json": document})

    snapshot = load_rate_snapshot(rates_dir, RATES_CFG)

    assert snapshot.base_rate == Decimal("2.7457")
    assert str(snapshot.base_rate) == "2.7457"
    assert snapshot.base_rate.as_tuple().exponent == -4


def test_json_float_observation_value_rejected(tmp_path: Path) -> None:
    document = _valet_document(observations=[{"d": FRIDAY, "v": 2.74}])
    rates_dir = _write_json_rates(tmp_path, {f"{SERIES}.json": document})

    with pytest.raises(RateError) as excinfo:
        load_rate_snapshot(rates_dir, RATES_CFG)

    message = str(excinfo.value)
    assert "decimal string" in message
    assert "float" in message


def test_saturday_observation_date_fails(tmp_path: Path) -> None:
    document = _valet_document(observations=[{"d": SATURDAY, "v": "2.74"}])
    rates_dir = _write_json_rates(tmp_path, {f"{SERIES}.json": document})

    with pytest.raises(RateError) as excinfo:
        load_rate_snapshot(rates_dir, RATES_CFG)

    message = str(excinfo.value)
    assert "not a business day" in message
    assert SATURDAY in message


def test_friday_observation_date_accepted(tmp_path: Path) -> None:
    document = _valet_document(observations=[{"d": FRIDAY, "v": "2.74"}])
    rates_dir = _write_json_rates(tmp_path, {f"{SERIES}.json": document})

    snapshot = load_rate_snapshot(rates_dir, RATES_CFG)

    assert snapshot.observation_date == date(2026, 11, 6)
    assert snapshot.source_timestamp == FRIDAY


def test_nested_live_shape_picks_latest_date_not_list_position(tmp_path: Path) -> None:
    """The live Valet document: value nested under the series code, descending.

    The newest non-null entry sits at the front (2026-11-06) and a planted
    older non-null entry sits at the end (2026-11-03), so a reader that trusts
    list position in either direction picks the wrong date. The latest-date
    rule must return 2026-11-06 with 2.7400.
    """
    observations: list[dict[str, object]] = [
        {"d": FRIDAY, SERIES: {"v": "2.74"}},
        {"d": "2026-11-05", SERIES: {"v": None}},
        {"d": "2026-11-04", SERIES: {"v": "2.72"}},
        {"d": "2026-11-03", SERIES: {"v": "2.68"}},
    ]
    rates_dir = _write_json_rates(
        tmp_path, {f"{SERIES}.json": _valet_document(observations=observations)}
    )

    snapshot = load_rate_snapshot(rates_dir, RATES_CFG)

    assert snapshot.observation_date == date(2026, 11, 6)
    assert snapshot.base_rate == Decimal("2.7400")
    assert snapshot.label == MATCHING_LABEL


def test_flat_shape_still_accepted(tmp_path: Path) -> None:
    """The flat {"d", "v"} entries stay readable alongside the nested shape."""
    observations: list[dict[str, object]] = [
        {"d": "2026-11-05", "v": "2.72"},
        {"d": FRIDAY, "v": "2.74"},
        {"d": "2026-11-07", "v": None},
    ]
    rates_dir = _write_json_rates(
        tmp_path, {f"{SERIES}.json": _valet_document(observations=observations)}
    )

    snapshot = load_rate_snapshot(rates_dir, RATES_CFG)

    assert snapshot.observation_date == date(2026, 11, 6)
    assert snapshot.base_rate == Decimal("2.7400")


def test_missing_manifest_fails_loudly(tmp_path: Path) -> None:
    rates_dir = tmp_path / "rates"
    rates_dir.mkdir()
    (rates_dir / f"{SERIES}.json").write_text(
        json.dumps(_valet_document()), encoding="utf-8"
    )

    with pytest.raises(RateError) as excinfo:
        load_rate_snapshot(rates_dir, RATES_CFG)

    message = str(excinfo.value)
    assert "rate manifest missing or unreadable" in message
    assert MANIFEST_NAME in message


def test_missing_series_file_listed_in_manifest_fails(tmp_path: Path) -> None:
    rates_dir = _write_json_rates(tmp_path, {f"{SERIES}.json": _valet_document()})
    (rates_dir / f"{SERIES}.json").unlink()

    with pytest.raises(RateError) as excinfo:
        load_rate_snapshot(rates_dir, RATES_CFG)

    message = str(excinfo.value)
    assert f"{SERIES}.json" in message
    assert "but the file is missing" in message


def test_missing_series_file_never_fabricated(tmp_path: Path) -> None:
    rates_dir = tmp_path / "rates"
    rates_dir.mkdir()
    _write_manifest(rates_dir, [])  # an empty manifest: no JSON committed yet

    with pytest.raises(RateError) as excinfo:
        load_rate_snapshot(rates_dir, RATES_CFG)

    message = str(excinfo.value)
    assert "committed rate file missing" in message
    assert f"{SERIES}.json" in message
    assert MANIFEST_NAME in message


def test_engine_package_has_no_network_imports() -> None:
    forbidden_roots = {NET_LIB, "http", "socket", "requests"}
    engine_files = sorted(ENGINE_DIR.rglob("*.py"))
    assert engine_files, f"expected engine sources under {ENGINE_DIR}"

    offenders: list[str] = []
    for path in engine_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                root = name.split(".")[0]
                if root in forbidden_roots:
                    offenders.append(f"{path.name} imports {name}")

    assert not offenders, f"network imports in engine package: {offenders}"


def _scanned_pipeline_files() -> list[Path]:
    """Every text file under pipeline/ outside caches, venvs, and dot dirs."""
    excluded_dirs = {"__pycache__", "node_modules", "dist"}
    excluded_extensions = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".woff",
        ".woff2",
        ".pdf",
        ".zip",
    }
    files: list[Path] = []
    for path in sorted(PIPELINE_DIR.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(PIPELINE_DIR)
        if any(part in excluded_dirs or part.startswith(".") for part in relative.parts):
            continue
        if path.suffix.lower() in excluded_extensions:
            continue
        files.append(path)
    return files


@pytest.mark.skipif(
    not SCRIPTS_DIR.is_dir(),
    reason="pipeline/scripts/ does not exist yet; nothing to confine yet",
)
def test_network_token_reference_confined_to_scripts() -> None:
    holders: list[str] = []
    for path in _scanned_pipeline_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if NET_LIB in text:
            holders.append(path.relative_to(PIPELINE_DIR).as_posix())

    offenders = [name for name in holders if not name.startswith("scripts/")]
    assert not offenders, f"{NET_LIB} appears outside pipeline/scripts/: {offenders}"
