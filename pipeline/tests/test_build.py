"""Tests for vdebt.build, the canonical payload build (architecture P2.6).

Covers the binding test_build entry in the P2.7 test plan plus this slice's
envelope contract: the byte-identity kill gate over two subprocess builds
(tree diff excluding build_meta.json, identical file lists, identical
digests, same asof directory), committed payload freshness (a fresh build
from unchanged inputs must equal payloads/<asof>/book.json byte for byte),
the planted zero-count stratum failure through build._assert_strata_coverage,
the build log content contract (base rate line with asof and series code,
per-borrower sizing lines in the pinned P2.5 format, strata manifest counts,
band excursion report), book.json envelope spot checks read straight from the
committed payload (book hash fixture value, six strata counts, rate
provenance, borrower block keys, rbf_capped not_applicable schedule, zero
floats, no wall-clock values), and build_meta.json carrying git_sha plus the
wall clock while book.json carries neither key name.

Four subprocess builds run in this file, numbered in comments at each call
site: two in the kill gate (heaviest test), one for freshness, one for the
log. Everything else reads committed files or calls engine code in process.
No test writes inside the repository: every build lands under pytest's
tmp_path.
"""

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from vdebt import build as build_module
from vdebt.generator import STRATA_NAMES

PIPELINE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PIPELINE_DIR.parent
COMMITTED_PAYLOADS = REPO_ROOT / "payloads"

# Pinned asof (the committed rate observation date) and the committed payload
# path for it, per architecture P2.6 layout.
ASOF = "2026-11-10"
SERIES = "TB.CDN.90D.MID"
COMMITTED_BOOK_PATH = COMMITTED_PAYLOADS / ASOF / "book.json"
COMMITTED_META_PATH = COMMITTED_PAYLOADS / "build_meta.json"

# Engine-derived RNG drift fingerprint, committed in
# pipeline/fixtures/generator/book_sha256.json (P2.2 book hash pin).
PINNED_BOOK_SHA256 = "317215cd56585bb5f04d79704cc3338c4412bddac8d2b85e2f80be54a5c0cbf9"

# The 24 borrower ids in the pinned ascending draw order (P2.2).
EXPECTED_BORROWER_IDS = [f"SYN-{index:04d}" for index in range(1, 25)]

# Committed payload text and document, loaded once: tests below read the
# committed artifact directly and never rebuild it to answer spot checks.
COMMITTED_BOOK_TEXT = COMMITTED_BOOK_PATH.read_text(encoding="utf-8")
COMMITTED_BOOK: dict[str, Any] = json.loads(COMMITTED_BOOK_TEXT)

# Date substring pattern with digit guards so a longer number cannot smuggle
# a date-shaped fragment past the wall-clock scan.
DATE_PATTERN = r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)"
# ISO timestamp shape: a date joined to a clock time with a T separator.
TIMESTAMP_PATTERN = r"(?<!\d)\d{4}-\d{2}-\d{2}T"
# Clock time shape anywhere inside a value.
CLOCK_PATTERN = r"\d{2}:\d{2}:\d{2}"


def _run_build(output_root: Path) -> subprocess.CompletedProcess[str]:
    """Run the CLI build into output_root from the pipeline directory."""
    return subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "vdebt",
            "build",
            "--output-root",
            str(output_root),
        ],
        cwd=PIPELINE_DIR,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _process_context(process: subprocess.CompletedProcess[str]) -> str:
    """Render a failed subprocess for an assertion message."""
    return (
        f"returncode={process.returncode}\n"
        f"stdout:\n{process.stdout}\n"
        f"stderr:\n{process.stderr}"
    )


def _sha256(path: Path) -> str:
    """Return the hex sha256 digest of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _asof_dirs(root: Path) -> list[str]:
    """Return the sorted asof directory names directly under root."""
    return sorted(entry.name for entry in root.iterdir() if entry.is_dir())


def _tree_digests(root: Path) -> dict[str, str]:
    """Map every file under root (build_meta.json excluded) to its sha256.

    build_meta.json is the only build output allowed to carry the wall clock
    (architecture section 11), so it sits outside byte identity by definition
    and is dropped here by file name.
    """
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "build_meta.json":
            continue
        relative = path.relative_to(root).as_posix()
        digests[relative] = _sha256(path)
    return digests


def _first_difference(
    digests_a: dict[str, str], digests_b: dict[str, str]
) -> str:
    """Return the first relative path whose presence or digest differs."""
    for key in sorted(set(digests_a) | set(digests_b)):
        if digests_a.get(key) != digests_b.get(key):
            return key
    return "<no difference>"


def _collect_strings(value: Any, out: list[str]) -> list[str]:
    """Append every string leaf of a parsed JSON document to out."""
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_strings(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect_strings(item, out)
    return out


def _collect_floats(value: Any, path: str, out: list[str]) -> list[str]:
    """Append the document path of every float leaf to out (floats are banned)."""
    if isinstance(value, float):
        out.append(path)
    elif isinstance(value, dict):
        for key, item in value.items():
            _collect_floats(item, f"{path}/{key}", out)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_floats(item, f"{path}/{index}", out)
    return out


def _payment_dates(document: dict[str, Any]) -> set[str]:
    """Return every schedule payment date published in the payload."""
    dates: set[str] = set()
    for borrower in document["borrowers"]:
        rows = borrower["schedule"].get("rows") or []
        for row in rows:
            dates.add(row["payment_date"]["value"])
    return dates


def _allowed_dates(document: dict[str, Any]) -> set[str]:
    """Return the pinned date vocabulary of the payload.

    Allowed: the rate observation date (the asof), the external rate
    source_timestamp, the citation access dates inside band strings, and the
    schedule payment dates derived from the close. Anything else in the text
    would be a wall-clock or environment leak.
    """
    rates = document["rates"]
    allowed = {rates["observation_date"], rates["source_timestamp"]}
    for text in _collect_strings(document, []):
        allowed.update(re.findall(r"accessed (" + DATE_PATTERN + ")", text))
    allowed.update(_payment_dates(document))
    return allowed


# 1. Byte identity kill gate (builds 1 and 2 of 4: the heaviest test here).


def test_byte_identity_kill_gate(tmp_path: Path) -> None:
    # Heaviest test in this file: two full subprocess builds (builds 1 and 2
    # of the 4 subprocess builds allowed in this file).
    root_a = tmp_path / "run_a"
    root_b = tmp_path / "run_b"
    process_a = _run_build(root_a)
    process_b = _run_build(root_b)
    assert process_a.returncode == 0, f"build A failed\n{_process_context(process_a)}"
    assert process_b.returncode == 0, f"build B failed\n{_process_context(process_b)}"

    dirs_a = _asof_dirs(root_a)
    dirs_b = _asof_dirs(root_b)
    assert dirs_a == dirs_b, f"asof directory mismatch: {dirs_a} vs {dirs_b}"
    assert len(dirs_a) == 1, f"expected exactly one asof directory, got {dirs_a}"

    digests_a = _tree_digests(root_a)
    digests_b = _tree_digests(root_b)
    assert digests_a, f"build A produced no payload files under {root_a}"

    files_a = sorted(digests_a)
    files_b = sorted(digests_b)
    assert files_a == files_b, f"file list mismatch: {files_a} vs {files_b}"

    if digests_a != digests_b:
        first = _first_difference(digests_a, digests_b)
        pytest.fail(
            "byte-identity kill gate failed at first differing path "
            f"{first}: run A sha256={digests_a.get(first)} "
            f"run B sha256={digests_b.get(first)}"
        )

    # P2.6: the book hash must be identical across runs as well.
    book_a = json.loads((root_a / dirs_a[0] / "book.json").read_text(encoding="utf-8"))
    book_b = json.loads((root_b / dirs_b[0] / "book.json").read_text(encoding="utf-8"))
    assert (
        book_a["meta"]["book_sha256"]["value"]
        == book_b["meta"]["book_sha256"]["value"]
    ), "book_sha256 differs between two runs"


# 2. Committed payload freshness (build 3 of 4).


def test_committed_payload_freshness(tmp_path: Path) -> None:
    # Build 3 of the 4 subprocess builds allowed in this file.
    root = tmp_path / "fresh"
    process = _run_build(root)
    assert process.returncode == 0, f"fresh build failed\n{_process_context(process)}"

    dirs = _asof_dirs(root)
    assert len(dirs) == 1, f"expected exactly one asof directory, got {dirs}"
    fresh_book = root / dirs[0] / "book.json"
    assert fresh_book.is_file(), f"fresh build wrote no book.json under {root}"

    committed_book = COMMITTED_PAYLOADS / dirs[0] / "book.json"
    assert committed_book.is_file(), (
        f"committed payload missing for asof {dirs[0]}: {committed_book}"
    )

    fresh_digest = _sha256(fresh_book)
    committed_digest = _sha256(committed_book)
    if fresh_digest != committed_digest:
        pytest.fail(
            "committed payload is stale: committed "
            f"{committed_book} sha256={committed_digest} differs from the "
            f"fresh build {fresh_book} sha256={fresh_digest} (asof {dirs[0]})"
        )
    assert committed_digest == _sha256(COMMITTED_BOOK_PATH), (
        "freshness check compared a different committed file than pinned"
    )


# 3. Strata gate: a planted zero-count stratum fails naming the stratum.


@pytest.mark.parametrize("stratum", STRATA_NAMES)
def test_strata_gate_rejects_zero_count_stratum(stratum: str) -> None:
    manifest: dict[str, dict[str, object]] = {
        name: {"count": 1, "members": ["SYN-0001"]} for name in STRATA_NAMES
    }
    manifest[stratum] = {"count": 0, "members": []}
    with pytest.raises(build_module.BuildError) as excinfo:
        build_module._assert_strata_coverage(manifest)
    message = str(excinfo.value)
    assert stratum in message, f"failure message must name {stratum}: {message}"
    assert "count 0" in message, f"failure message must report count 0: {message}"


# 4. Build log content (build 4 of 4: one dedicated subprocess build).


def test_build_log_content(tmp_path: Path) -> None:
    # Build 4 of the 4 subprocess builds allowed in this file (heaviest
    # single-build test: it re-derives the whole log contract from scratch).
    root = tmp_path / "log"
    process = _run_build(root)
    assert process.returncode == 0, f"log build failed\n{_process_context(process)}"
    stdout = process.stdout

    # Base rate line carries the value, the unit, the pinned asof, and the
    # pinned series code (P2.6 CLI contract).
    base_line = re.search(
        r"base rate \d+\.\d{4} percent_annual asof (\d{4}-\d{2}-\d{2}) series (\S+)",
        stdout,
    )
    assert base_line is not None, f"base rate line missing from build log:\n{stdout}"
    assert base_line.group(1) == ASOF, (
        f"base rate line asof {base_line.group(1)} != pinned {ASOF}"
    )
    assert base_line.group(2) == SERIES, (
        f"base rate line series {base_line.group(2)} != pinned {SERIES}"
    )

    # Per-borrower sizing lines in the pinned P2.5 format:
    # "sizing SYN-000K iterations=N bracket=[lo hi] answer=A binding=B".
    sizing_ids = re.findall(
        r"^sizing (SYN-\d{4}) iterations=\d+ bracket=\[\d+ \d+\] "
        r"answer=\S+ binding=\S+$",
        stdout,
        flags=re.MULTILINE,
    )
    assert sizing_ids, f"no sizing lines in the pinned format:\n{stdout}"
    assert sizing_ids == EXPECTED_BORROWER_IDS, (
        f"sizing lines cover {sizing_ids}, expected {EXPECTED_BORROWER_IDS}"
    )

    # Strata manifest counts, one entry per pinned stratum in pinned order.
    manifest_lines = [
        line for line in stdout.splitlines() if line.startswith("strata manifest: ")
    ]
    assert len(manifest_lines) == 1, (
        f"expected exactly one strata manifest line, got {manifest_lines}"
    )
    manifest_line = manifest_lines[0]
    for name in STRATA_NAMES:
        assert re.search(rf"(?:^| ){name}=\d+(?: |$)", manifest_line), (
            f"strata manifest line missing {name}=<count>: {manifest_line}"
        )
    assert manifest_line.count("=") == len(STRATA_NAMES), (
        f"strata manifest line carries extra or missing entries: {manifest_line}"
    )

    # Band excursion report (report-only, P2.3).
    excursion_line = re.search(
        r"band excursion: \d+ within_band, \d+ excursion; excursion ids: \S+",
        stdout,
    )
    assert excursion_line is not None, (
        f"band excursion report missing from build log:\n{stdout}"
    )


# 5. book.json envelope spot checks against the committed payload (no build).


def test_committed_book_envelope_spot_checks() -> None:
    document = COMMITTED_BOOK
    assert sorted(document) == ["borrowers", "meta", "rates"]

    # Book hash equals the committed fixture value (P2.2 fingerprint pin).
    assert document["meta"]["book_sha256"]["value"] == PINNED_BOOK_SHA256, (
        "meta.book_sha256 drifted from the committed fixture value"
    )

    # All six strata present with count >= 1 (the build asserts this too).
    manifest = document["meta"]["strata_manifest"]
    assert sorted(manifest) == sorted(STRATA_NAMES)
    for name in STRATA_NAMES:
        count = manifest[name]["count"]["value"]
        assert isinstance(count, int) and count >= 1, (
            f"stratum {name} has count {count}, expected at least 1"
        )

    # Base rate envelope carries the external provenance keys (P2.3).
    base_rate = document["rates"]["base_rate"]
    assert base_rate["source"], "rates.base_rate is missing source"
    assert base_rate["source_timestamp"], (
        "rates.base_rate is missing source_timestamp"
    )
    assert base_rate["source"] == document["rates"]["source"]
    assert base_rate["source_timestamp"] == document["rates"]["source_timestamp"]
    assert document["rates"]["observation_date"] == ASOF
    assert document["rates"]["source_timestamp"] == ASOF
    assert document["rates"]["series"] == SERIES

    # Every borrower publishes the full P2.6 block.
    borrowers = document["borrowers"]
    assert [item["borrower_id"] for item in borrowers] == EXPECTED_BORROWER_IDS
    required = {"inputs", "paths", "pricing", "sizing", "schedule"}
    for borrower in borrowers:
        missing = required - set(borrower)
        assert not missing, (
            f"{borrower['borrower_id']} is missing block keys {sorted(missing)}"
        )

    # At least one rbf_capped borrower publishes the pinned phase 2 schedule
    # status not_applicable instead of rows.
    rbf_borrowers = [b for b in borrowers if b["structure"] == "rbf_capped"]
    assert rbf_borrowers, "no rbf_capped borrower in the committed book"
    not_applicable = [
        b
        for b in rbf_borrowers
        if b["schedule"].get("status", {}).get("value") == "not_applicable"
    ]
    assert not_applicable, (
        "no rbf_capped borrower publishes schedule status not_applicable"
    )

    # Floats are banned anywhere in payloads (canonical writer rule).
    floats = _collect_floats(document, "", [])
    assert floats == [], f"float leaves found in book.json: {floats[:10]}"

    # No wall-clock-looking value outside the pinned asof and schedule dates.
    strings = _collect_strings(document, [])
    clock_hits = [value for value in strings if re.search(CLOCK_PATTERN, value)]
    assert clock_hits == [], f"clock time values inside book.json: {clock_hits[:5]}"
    assert not re.search(TIMESTAMP_PATTERN, COMMITTED_BOOK_TEXT), (
        "ISO timestamp with time component found in book.json"
    )
    found_dates = set(re.findall(DATE_PATTERN, COMMITTED_BOOK_TEXT))
    allowed_dates = _allowed_dates(document)
    stray_dates = found_dates - allowed_dates
    assert not stray_dates, (
        "dates outside the pinned asof, citation access, and payment date "
        f"vocabulary: {sorted(stray_dates)}"
    )


# 6. build_meta.json is excluded from book.json by construction.


def test_build_meta_excluded_from_book() -> None:
    # The committed build_meta.json exists and carries the git SHA plus a
    # wall-clock field (the only clock in the build).
    meta = json.loads(COMMITTED_META_PATH.read_text(encoding="utf-8"))
    assert "git_sha" in meta, f"build_meta.json has no git_sha: {sorted(meta)}"
    wall_clock_keys = [
        key
        for key, value in meta.items()
        if isinstance(value, str)
        and re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2}", value
        )
    ]
    assert wall_clock_keys, (
        f"build_meta.json has no wall-clock field: {sorted(meta)}"
    )

    # book.json carries neither key name: no git data, no clock (section 6.4).
    assert "git_sha" not in COMMITTED_BOOK_TEXT, "git_sha leaked into book.json"
    assert "built_at_utc" not in COMMITTED_BOOK_TEXT, (
        "built_at_utc leaked into book.json"
    )
