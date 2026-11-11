"""Gate orchestration for the verify package (architecture section 10).

run_all() executes the dash scan, the import isolation check at gate start and
again at gate end, and the fixture validation; it prints one PASS, FAIL, or
SKIP line per check plus details on failure, then the SHA256 of every artifact
file it read (recorded as printed, not reverified), and returns 0 when every
check passes and 1 otherwise. A missing fixture file is an error, not a skip:
all six fixtures F1 to F6 are committed in phase 1, so an absent file fails
the gate with "missing fixture: <path>".

No network access and no engine imports. Standard library only.
"""

from pathlib import Path

from vdebt_verify import dash_scan, fixture_runner, isolation


def _print_dash(report: dash_scan.DashReport) -> None:
    status = "PASS" if report.passed else "FAIL"
    print(f"{status} dash scan ({report.summary()})")
    for line in report.detail_lines():
        print(f"  {line}")


def _print_isolation(
    start_passed: bool,
    start_messages: list[str],
    end_passed: bool | None,
    end_messages: list[str] | None,
) -> None:
    passed = start_passed and end_passed is not False
    scope = (
        "(static scan plus runtime sys.modules check at gate start and gate end)"
        if end_passed is not None
        else "(static scan plus runtime sys.modules check)"
    )
    if passed:
        print(f"PASS import isolation {scope}")
        return
    print(f"FAIL import isolation {scope}")
    for message in start_messages:
        print(f"  [gate start] {message}")
    if end_messages:
        for message in end_messages:
            print(f"  [gate end] {message}")


def _print_fixtures(report: fixture_runner.FixtureReport) -> None:
    status = report.status()
    print(f"{status} fixture validation ({report.summary()})")
    for error in report.errors:
        print(f"  {error}")
    for display, digest in report.read_files:
        print(f"SHA256 {digest}  {display}")


def run_dash_check(root: Path | None = None) -> int:
    """Standalone dash scan entry; return its exit code."""
    report = dash_scan.run_dash_scan(root)
    _print_dash(report)
    return 0 if report.passed else 1


def run_imports_check() -> int:
    """Standalone import isolation entry; return its exit code."""
    passed, messages = isolation.run_isolation_check()
    _print_isolation(passed, messages, None, None)
    return 0 if passed else 1


def run_fixtures_check() -> int:
    """Standalone fixture validation entry; return its exit code."""
    report = fixture_runner.run_fixture_validation()
    _print_fixtures(report)
    return 1 if report.status() == "FAIL" else 0


def run_all(root: Path | None = None) -> int:
    """Run every gate check; print the report and return 0 or 1."""
    scan_root = root if root is not None else dash_scan.default_root()
    print("vdebt verify gate")
    print(f"root: {scan_root}")

    start_passed, start_messages = isolation.run_isolation_check()
    dash_report = dash_scan.run_dash_scan(scan_root)
    fixture_report = fixture_runner.run_fixture_validation()
    end_passed, end_messages = isolation.run_isolation_check()

    _print_dash(dash_report)
    _print_isolation(start_passed, start_messages, end_passed, end_messages)
    _print_fixtures(fixture_report)

    passed = (
        dash_report.passed
        and start_passed
        and end_passed
        and fixture_report.status() != "FAIL"
    )
    print(f"GATE RESULT: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1
