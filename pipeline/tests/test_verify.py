"""Tests for vdebt_verify: dash scan rules, import isolation, fixture runner.

Covers architecture sections 9 and 10 pins: Unicode Pd dashes fail every file
class, thematic breaks and table separators pass prose, flag tokens pass source
files, plain lock files pass, the isolation pattern flags engine imports while
vdebt_verify imports pass, and the fixture runner validates the real golden
directory with no errors. Planted dash content is written with unicode escapes
so this test file passes the repository dash scan itself.
"""

from pathlib import Path

from vdebt_verify import dash_scan, fixture_runner, isolation

EM_DASH = "\u2014"
THREE_HYPHENS = "-" * 3
DOUBLE_HYPHEN = "-" * 2


def test_dash_scan_fails_on_planted_unicode_dash(tmp_path: Path):
    (tmp_path / "note.md").write_text(f"alpha {EM_DASH} beta\n", encoding="utf-8")
    report = dash_scan.run_dash_scan(tmp_path)
    assert not report.passed
    assert [v.rule_id for v in report.violations] == ["unicode_dash"]
    assert report.violations[0].codepoint == "U+2014"
    assert report.violations[0].path.endswith("note.md")


def test_dash_scan_passes_clean_prose_tree(tmp_path: Path):
    table_row = f"|{THREE_HYPHENS}|{THREE_HYPHENS}|"
    content = (
        "Title\n"
        "\n"
        f"{THREE_HYPHENS}\n"
        "\n"
        "Prose with a single-hyphen word.\n"
        "\n"
        f"{table_row}\n"
    )
    (tmp_path / "readme.md").write_text(content, encoding="utf-8")
    report = dash_scan.run_dash_scan(tmp_path)
    assert report.passed, report.detail_lines()
    assert report.files_scanned == 1


def test_dash_scan_allows_flag_tokens_in_source(tmp_path: Path):
    content = f'flag = "{DOUBLE_HYPHEN}verbose"\nprint(flag)\n'
    (tmp_path / "main.py").write_text(content, encoding="utf-8")
    report = dash_scan.run_dash_scan(tmp_path)
    assert report.passed, report.detail_lines()


def test_dash_scan_rejects_double_hyphen_outside_flag_tokens(tmp_path: Path):
    content = f"value = a{DOUBLE_HYPHEN}b\n"
    (tmp_path / "main.py").write_text(content, encoding="utf-8")
    report = dash_scan.run_dash_scan(tmp_path)
    assert not report.passed
    assert [v.rule_id for v in report.violations] == ["double_hyphen_source"]


def test_dash_scan_accepts_plain_lock_file(tmp_path: Path):
    (tmp_path / "uv.lock").write_text(
        'name = "pytest"\nversion = "8.0.0"\n', encoding="utf-8"
    )
    report = dash_scan.run_dash_scan(tmp_path)
    assert report.passed, report.detail_lines()
    assert report.files_scanned == 1


def test_dash_scan_flags_unclassified_extension(tmp_path: Path):
    (tmp_path / "mystery.xyz").write_text("plain content\n", encoding="utf-8")
    report = dash_scan.run_dash_scan(tmp_path)
    assert not report.passed
    assert report.unclassified


def _static_findings(path: Path) -> list[str]:
    """Apply the isolation module's engine-import pattern to one explicit file."""
    findings = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if isolation._STATIC_PATTERN.match(line):
            findings.append(f"{path.name}:{lineno}")
    return findings


def test_isolation_static_pattern_flags_engine_imports(tmp_path: Path):
    # The public scan hard-codes the vdebt_verify package directory, so the
    # module's own pattern is exercised here with explicit file paths.
    bad = tmp_path / "bad.py"
    bad.write_text("import vdebt.money\nfrom vdebt.money import to_cents\n", encoding="utf-8")
    good = tmp_path / "good.py"
    good.write_text(
        "import json\nfrom vdebt_verify import gate\n",
        encoding="utf-8",
    )
    assert _static_findings(bad) == ["bad.py:1", "bad.py:2"]
    assert _static_findings(good) == []


def test_isolation_static_pattern_respects_package_boundary(tmp_path: Path):
    edge = tmp_path / "edge.py"
    edge.write_text("import vdebt\nimport vdebt_verify\n", encoding="utf-8")
    assert _static_findings(edge) == ["edge.py:1"]


def test_isolation_check_passes_on_real_tree():
    # run_isolation_check scans the real vdebt_verify package directory and the
    # live sys.modules delta, so the public function runs against the real tree.
    passed, messages = isolation.run_isolation_check()
    assert passed, messages
    assert messages == []
    # A second run in the same process must still pass: no new engine keys
    # appear after the launcher baseline is recorded.
    passed_again, messages_again = isolation.run_isolation_check()
    assert passed_again, messages_again


def test_fixture_runner_validates_real_golden_dir():
    report = fixture_runner.run_fixture_validation(fixture_runner.default_fixture_dir())
    assert report.errors == [], report.errors
    assert report.validated == ["F1", "F2", "F3", "F4", "F5", "F6"]
    assert report.skipped == []
    assert report.status() == "PASS"
    assert len(report.read_files) == 6
    for _, digest in report.read_files:
        assert len(digest) == 64


def test_fixture_runner_fails_on_missing_files(tmp_path: Path):
    # All six fixtures are committed in phase 1, so an absent file is an error
    # ("missing fixture: <path>") that fails the report and the gate.
    report = fixture_runner.run_fixture_validation(tmp_path)
    assert not report.passed
    assert report.status() == "FAIL"
    assert report.skipped == []
    assert len(report.errors) == len(fixture_runner.FIXTURE_FILES)
    for error in report.errors:
        assert error.startswith("missing fixture: "), error
    assert any(error.endswith("sizing_fixture.json") for error in report.errors)


def test_fixture_runner_rejects_fixture_missing_arithmetic(tmp_path: Path):
    (tmp_path / "sizing_fixture.json").write_text(
        '{"fixture_id": "F1"}', encoding="utf-8"
    )
    report = fixture_runner.run_fixture_validation(tmp_path)
    assert not report.passed
    assert any("arithmetic" in error for error in report.errors)
    assert any("rate_source" in error for error in report.errors)
