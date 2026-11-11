# Venture Debt Engine Reviews

Last updated: 2026-11-11

## 2026-11-10 - Phase 1: Code review

Verdict: FAIL

Scope: pipeline/src/vdebt (money.py, dates.py, config.py, snapshot.py, cli.py,
__main__.py), pipeline/src/vdebt_verify (dash_scan.py, isolation.py,
fixture_runner.py, gate.py, __main__.py), pipeline/config/scenario.toml,
pipeline/tests (6 files, 61 tests), pipeline/fixtures/golden F1 to F6,
pipeline/pyproject.toml, .python-version.

Command results (2026-11-10):
- uv run pytest -q: 61 passed, exit 0.
- uv run ruff check .: All checks passed, exit 0.
- uv run python -m vdebt verify: GATE RESULT: PASS, exit 0. Dash scan 40 files
  scanned, 0 violations, 0 unclassified, 2 skipped (.DS_Store not valid UTF-8);
  import isolation PASS (static plus runtime at gate start and gate end);
  fixture validation 6 validated, 0 skipped; SHA256 printed for all 6 fixtures.
- uv run python -m vdebt_verify: identical output, exit 0.

Findings (Severity | File:Line | Issue | Fix):

| Severity | File:Line | Issue | Fix |
|----------|-----------|-------|-----|
| BLOCKER | git history (pipeline/ untracked) | Spec section 11 item 7 and the phase 1 acceptance require the six fixture files committed before any engine module exists; git ls-files shows only .gitignore, AGENTS.md, LICENSE, and venture-debt-spec.md, so fixtures, engine, verify, tests, and config are all uncommitted and the fixture-first ordering is neither demonstrated nor true in the record | Commit in two steps: first the golden fixtures and their tests alone, then the engine and verify package; or record an explicit deviation in docs/decisions.md |
| MEDIUM | pipeline/src/vdebt_verify/fixture_runner.py:199-202 | Missing fixture files are recorded as SKIP and gate.py treats SKIP as pass (gate.py:93-98), so silent fixture deletion passes the gate, which conflicts with spec section 11 item 3 (non-zero exit on fixture mismatch) | Make a missing fixture file an error now that all six are committed, or gate the SKIP allowance on an explicit phase flag |
| LOW | pipeline/src/vdebt/snapshot.py:102-141 | An external envelope (source plus source_timestamp) may carry an extra free-text provenance string that declares no provenance class and is not rejected | Reject a provenance key on external envelopes, or require it to declare the class |
| LOW | pipeline/src/vdebt_verify/fixture_runner.py:182-189 | Tolerance values are not validated (only keys must contain "irr"); the pinned abs 0.0001 is enforced only by pipeline/tests/test_fixtures.py:109-112 | Validate tolerance values in the runner, or document the test-only enforcement in the module docstring |
| LOW | .DS_Store (repo root, pipeline/) | macOS metadata files are scanned and skipped as not valid UTF-8 on every gate run and should never be committed | Add .DS_Store to .gitignore |

Verified clean:
- Money pins hold: single ROUND_HALF_UP quantize with explicit MONEY_CONTEXT
  (money.py:22, 53-93), no decimal.setcontext anywhere, allocate_cents floor
  split with earliest-first remainder (money.py:146-157).
- Dates pins hold: ACT/365 exact (dates.py:22-29), roll forward only
  (dates.py:37-45), month-end clamp (dates.py:48-62), strictly increasing
  payment dates raising ValueError (dates.py:65-93).
- Config: native TOML floats rejected recursively (config.py:84-97), citation
  and band required per band (config.py:149-224), scenario.toml matches
  architecture section 7 verbatim.
- Snapshot validation matrix holds (snapshot.py:160-221): JSON integer iff
  usd_minor_units or count, fixed-scale strings with scale elsewhere, plain
  strings for date, sha256_hex, and status, exactly one provenance class,
  status companions, canonical JSON with sorted keys, LF, trailing newline,
  floats banned (TypeError).
- Dash scan matches section 9 including the .lock classification
  (dash_scan.py:45) and unclassified-extensions failure (dash_scan.py:201-204);
  the U+002D interpretation is documented at dash_scan.py:3-7 and is the only
  coherent reading of section 9.
- Import isolation is clean: zero imports of vdebt anywhere in vdebt_verify
  (verified by grep over source plus the assembled static pattern in
  isolation.py:26-33 and the runtime snapshot delta in isolation.py:88-101).
- No float leakage in money paths, payload paths, or fixture files. No wall
  clock, os.urandom, uuid, or hash-order dependence in any output path.
- Fixture integrity: six files, envelope fields present, arithmetic arrays
  non-empty with day counts and quantize steps, F1 three cases with the
  infeasible case publishing no facility key, F4 two cases, F6 IRR tolerance
  keys only, Valet referenced only inside the three mandated source strings,
  no Unicode dashes and no double hyphens in fixtures. Arithmetic spot checks
  (F1a runway boundary, F1b DSCR boundary with the first amortizing period
  identified by date, F1c zero-draw infeasibility, F2 and F3 interest rows,
  F4 cap-hit and shortfall residuals, F5 bridge interest, F6 NPV brackets at
  the pinned IRR minus and plus tolerance) all reconcile to the published
  expected values.
- Tests: 61 tests cover money round-trip idempotence, ACT/365 closed forms,
  dash-scan planted negatives, the envelope matrix, and fixture shapes;
  ruff and pytest both green.

Recurring patterns for other agents:
- Do not leave deliverables uncommitted; the verify culture treats the git
  record as evidence.
- Missing-file SKIP semantics will be tightened; write code that fails closed.
## 2026-11-11 - Phase 1: Re-review

Verdict: PASS

Scope: verification of the five findings from the 2026-11-10 Phase 1 review
(one BLOCKER, one MEDIUM, three LOW), plus the standing green state. All five
are resolved.

Per-item results:

1. Fixture-first commit ordering (was BLOCKER): PASS. git log shows
   757fb17 (bootstrap) then 3aca8cd ("test: hand-computed golden fixtures
   F1 to F6 with arithmetic") containing exactly the six
   pipeline/fixtures/golden JSON files, then 25f9770 ("feat: phase 1 domain
   model, verify skeleton, and test suite") containing the 28 engine, verify
   skeleton, tests, config, and docs files. docs/decisions.md:109-113 carries
   the "Fixture-first commit ordering reconstructed" entry; its integrity
   rationale is accurate (expected values hand-computed from the spec formulas
   with standalone scratch scripts deleted after use and never derived from
   engine output; the F1b engine cross-check ran only after the values were
   fixed) and it honestly records the authoring overlap as an accepted
   deviation.

2. Missing fixture fails the gate (was MEDIUM): PASS. fixture_runner.py:231-232
   reports "missing fixture: <path>" as an error, FixtureReport.status() is
   FAIL on any error (fixture_runner.py:80-82), and gate.py:94-101 exits 1.
   Demonstrated live: renaming bridge_fixture.json produced "FAIL fixture
   validation (5 validated, 0 skipped)", the line "missing fixture:
   pipeline/fixtures/golden/bridge_fixture.json", GATE RESULT: FAIL, exit 1;
   the restore returned the gate to GATE RESULT: PASS, exit 0, with the file
   byte-identical and git status clean. test_verify.py:130-140
   test_fixture_runner_fails_on_missing_files asserts the "missing fixture: "
   error prefix and the failed report.

3. External envelopes reject a stray free-text provenance key (was LOW): PASS.
   snapshot.py:138-147: an external class (source plus source_timestamp) that
   carries any provenance key raises ValueError quoting "external provenance
   (source and source_timestamp) must not carry a stray free-text 'provenance'
   key"; an external envelope whose provenance string also declares derived or
   generated hits the multiple-classes error at snapshot.py:133-135.

4. Tolerance values validated (was LOW): PASS. fixture_runner.py:145-159
   requires each tolerance entry to be an object with exactly an "abs" key
   holding a decimal string (NUMERIC_STRING_RE fullmatch) whose Decimal value
   is strictly positive; fixture_runner.py:207-215 requires the tolerances
   object with IRR-only keys. The module docstring states the rule
   (fixture_runner.py:20-21).

5. .DS_Store gitignored (was LOW): PASS. git check-ignore -v reports
   .gitignore:17 matching both .DS_Store and pipeline/.DS_Store; git status
   --short is empty.

Standing green state (2026-11-11):
- uv run pytest -q: 61 passed in 0.07s, exit 0.
- uv run ruff check .: All checks passed, exit 0.
- uv run python -m vdebt verify: GATE RESULT: PASS, exit 0. Dash scan 41 files
  scanned, 0 violations, 0 unclassified, 2 skipped (.DS_Store not valid
  UTF-8); import isolation PASS (static plus runtime at gate start and gate
  end); fixture validation 6 validated, 0 skipped; SHA256 printed for all 6
  fixtures.

Findings (Severity | File:Line | Issue | Fix):

| Severity | File:Line | Issue | Fix |
|----------|-----------|-------|-----|
| INFO | pipeline/tests/test_snapshot.py:97-109 | The external stray free-text provenance branch (snapshot.py:143-147) has no dedicated negative test | Add an assertion matching "stray free-text" to test_provenance_classes_exactly_one_with_companions |
| INFO | pipeline/tests/test_verify.py:130-140 | Tolerance validation negative paths (fixture_runner.py:145-159) have no unit tests | Add negative tests for bad tolerance shape, non-decimal abs, and non-positive abs |

Recurring patterns for other agents:
- Fixtures are hand-computed and committed first; never regenerate them from
  engine output.
- The gate fails closed on missing fixture files; keep it that way.

Verification checklist:
- [x] All checks passed
- [x] No critical issues found
- [x] Code follows project conventions
- [x] No em dashes, double hyphens, or dash equivalents; use proper punctuation
- [x] Phase code integrates cleanly with prior phases

