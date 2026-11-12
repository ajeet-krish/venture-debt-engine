# Venture Debt Engine Reviews

Last updated: 2026-11-12

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
   reports a clean tree.

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

## 2026-11-11 - Phase 2: Code review

Verdict: PASS

Scope: commits 358e5be through 3a7bb4f plus the uncommitted test_build.py
(rates, generator, pricing, schedule, sizing, build, CLI build subcommand,
config extensions, refresh_rates.py, scenario.toml phase 2 additions,
committed Valet data, six phase 2 test suites, payloads, docs updates).
Commands run: 156 pytest green (exit 0), ruff clean (exit 0), verify gate PASS
(59 files, 0 violations, 6 fixtures validated), build exit 0 with the written
book.json byte identical to the committed payloads/2026-11-10/book.json
(sha256 0b72e75196614322b2c36e22698fc7a0300b6ee249f54f1832d5bb9b95af56fb).

| Severity | File:Line | Issue | Fix |
|----------|-----------|-------|-----|
| Low | pipeline/tests/test_rates.py:16 | Docstring claims the real pipeline/data/rates directory does not exist yet; it is now committed and no test reads it directly (P2.7 asked for a manifest happy path on the committed data; covered only indirectly through the subprocess builds in test_build.py) | Add one test running load_rate_snapshot on the committed data/rates directory; update the docstring |
| Low | pipeline/tests/test_sizing.py | P2.7 item "pre_revenue predicate reduces to runway only" has no direct unit test; behavior is exercised only indirectly through the four pre-revenue borrowers in the build tests | Add a size_facility case with an all-zero CFADS path asserting runway-only binding and min_dscr None |
| Low | pipeline/tests/test_generator.py:125 | Draw spy counts 336 uniforms but does not assert the per-position draw mapping (P2.7 wording: order recorded via a spy Random); order is enforced structurally by code position | Optional: record per-borrower draw slots and assert the frozen order mapping |
| Low | pipeline/src/vdebt/snapshot.py:304 | validate_payload (phase 1 code) does not flag bare numeric leaves outside envelopes; the every numeric leaf has a unit rule holds by construction in build.py and an independent scan of the committed book.json found zero bare numeric leaves | Consider a hard leaf check in the phase 5 verify gate |
| Trivial | pipeline/tests/test_pricing.py:56 | In-memory test snapshot still uses the retired series code BTB3M | Rename the synthetic series to TB.CDN.90D.MID for consistency with the decisions record |

Recurring notes: none new. Determinism pins (336 uniforms, frozen draw order,
single quantize per boundary, explicit MONEY_CONTEXT, no wall clock, no hash
order), the envelope matrix, and the fixture rules all held under review. F1 to
F6 are untouched since commit 3aca8cd and the book hash fixture matches live
generator output (317215cd56585bb5f04d79704cc3338c4412bddac8d2b85e2f80be54a5c0cbf9).

## 2026-11-12 - Phase 3: Code review

Verdict: FAIL (one blocking gap: comparison surface test coverage; every substantive check on the phase 3 code passes)

Evidence run 2026-11-12: uv run pytest -q, 176 passed, exit 0; uv run ruff check ., All checks passed, exit 0; uv run python -m vdebt verify, GATE RESULT: PASS, exit 0 (dash scan 103 files 0 violations 0 unclassified, import isolation PASS, fixture validation 6 of 6, SHA256 prints include rate_shock_fixture.json f33dd930babc73d373b2fc5778140e33bb425c00998bb0f82e1a88f70a012e37); web npm run build exit 0 (sync-payloads byte copies, charts generated in Node, tsc (noEmit) clean, vite build clean apart from a chunk size warning); framing line grep over dist: exactly 3 occurrences in each of the six HTML pages; git diff b197140^..cf205fd on pipeline/fixtures/golden/rate_shock_fixture.json touches only the base block (arithmetic steps 1, 10 to 14, 21, 25, 27, 28, 31, 32, 33; all_in_rate_base 8.0000 to 7.0000; schedule_base interests; lender_cash_flows_base; total_interest_base; moic; base_irr_percent; irr_delta_bp; sensitivity_per_100bp_irr_bp) with the shocked block and the tolerances block byte unchanged. Hand recomputation confirms every rewritten arithmetic string is true: 4.0000 plus 3.0000 is 7.0000; base rows 594,521 / 536,986 / 613,699 / 556,164 / 594,521 sum to 2,895,891; shocked rows sum to 3,723,288; final base flow 594,521 plus 100,000,000 plus 300,000 is 100,894,521; (12.9080 minus 10.6819) x 100 is 222.61 quantized to 222.6; 222.6 / 2 is 111.3; 103,195,891 / 100,000,000 is 1.03195891 quantized to 1.03.

Findings:

| Severity | File:Line | Issue | Fix |
|----------|-----------|-------|-----|
| Medium | pipeline/tests/ (test_compare.py absent) | Architecture P3.8 pins test_compare.py (new) with ten assertion groups, and spec section 10.6 acceptance requires the byte-identical borrower inputs and base rate source to be asserted by test. No test in the suite references compare.py, build_comparison, or comparison.json (grep over pipeline/tests finds zero matches), so the comparison surface has zero direct coverage and that acceptance assertion is unmet | Tester adds test_compare.py per P3.8: identical inputs, citation presence with access date, binding constraint on all three rows, facility repetition, IO not_applicable reasons on bridge and rbf rows, first-breach placeholder with reason, drawn-row consistency against book schedule totals, featured borrower echo, 24 tables of three rows, comparison meta book_sha256 equals book meta |
| Medium | pipeline/tests/test_build.py:232 | test_committed_payload_freshness compares only book.json against a fresh build; the committed comparison.json can go stale without any test failing | Extend the freshness check to payloads/2026-11-10/comparison.json |
| Low | pipeline/src/vdebt/build.py:1063-1067 | Infeasible comparison rows attach the sizing provenance (derived: bisection, runway floor 18.0 months, DSCR floor 1.1500) to the moic, total_interest, and total_receipts not_applicable cells; the string describes sizing, not the metric the cell would have published | Reuse the per-metric provenance strings (_DERIVED_MOIC, _DERIVED_TOTAL_INTEREST, _DERIVED_RECEIPTS) for those na cells |
| Low | web/src/content/bands.ts:7 | Comment claims the band cap value is not published in any payload, but build.py publishes sizing.band_cap and web/src/routes/sizing.ts:75 renders it; behavior is correct and the comment is stale | Reword the comment to describe the published envelope with its citation and the honest fallback |
| Low | docs/tests.md:59 | The failure analysis lists stale references (returns.py docstrings and architecture P3.3 and P3.8 quotes) that were corrected in cf205fd; readers may chase non-issues | Record the corrections as landed |
| Low | web/ (vite build output) | npm run build emits a chunk size warning: dist/assets/page bundle is 1,356.72 kB because the committed payloads are inlined by static import (the pinned zero-runtime-fetch design) | Accept as by design or raise build.chunkSizeWarningLimit in vite.config.ts with a comment |

Recurring patterns: a new canonical payload file must arrive in the same phase with its freshness assertion and its spec acceptance assertions (test_compare is the first surface where both were skipped); module doc comments that describe what a payload publishes must track build.py changes (bands.ts and the tests.md failure analysis drifted the same way).

Hand recomputation notes for the record: 2026-01-05 is a Monday; F6 period spans 31, 28, 32, 29, 31 days sum to 151; all row interest values are consistent with 0.07 and 0.09 times 100,000,000 times the span over 365, ROUND_HALF_UP once. comparison.json was inspected directly: 24 tables of three rows, first-breach placeholder with the phase 4 reason on every row, RBF total_interest contractual 0 with provenance derived: RBF carries no interest term, pre-revenue rows aligned across all three structures as not_applicable with row-specific reasons (comparison.json:75, 178, 282), infeasible rows publish only status leaves (comparison.json:724-1117), and the single no_solution IRR cell (comparison.json:1368-1375) is honest: SYN-0004 RBF receipts front-load past the pinned solver bracket of -0.99 to 10.00 and the reason string discloses exactly that. book.json meta book_sha256 equals comparison.json meta book_sha256 (317215cd56585bb5f04d79704cc3338c4412bddac8d2b85e2f80be54a5c0cbf9).

## 2026-11-12 - Phase 3: Re-review

Verdict: PASS (all six findings from the 2026-11-12 Phase 3 review are resolved; standing green state confirmed at commit 0f6a4de)

Evidence run 2026-11-12 at commit 0f6a4de (working tree clean): uv run pytest -q, 187 passed, exit 0 (the prior 176 plus the 11 new comparison tests); uv run pytest tests/test_compare.py -v, 11 collected and 11 passed, exit 0; uv run ruff check ., All checks passed, exit 0; uv run python -m vdebt verify, GATE RESULT: PASS, exit 0 (dash scan 104 files, 0 violations, 0 unclassified, 4 skipped .DS_Store not valid UTF-8, import isolation PASS, fixture validation 6 of 6, rate_shock_fixture.json SHA256 f33dd930babc73d373b2fc5778140e33bb425c00998bb0f82e1a88f70a012e37); web npm run build exit 0 with no chunk size warning (page bundle 1,356.72 kB under the raised limit).

Per-item results:

1. test_compare.py (was BLOCKING): PASS. pipeline/tests/test_compare.py carries 11 tests (the ten P3.8 assertion groups plus an infeasible-shape group) and all 11 pass. The spec 10.6 acceptance is genuinely covered: test_identical_inputs_repeat_across_three_rows (test_compare.py:141-192) asserts that every table's three rows repeat the shared block cell for cell, that the published value fields are identical across rows, and that the base rate source and source_timestamp match across rows and bind to the committed book rates snapshot (test_compare.py:154-159), with the all_in_rate and lender_irr source bindings asserted on every row. The remaining tests map to the P3.8 groups: citation presence with access dates (test_compare.py:198), binding constraint equal to the book sizing answer on all three rows including infeasible tables (test_compare.py:261), facility repetition or the uniform infeasible shape (test_compare.py:282), IO not_applicable reasons on bridge and RBF rows with the pinned reason constants (test_compare.py:316), first-breach placeholder with the phase 4 reason (test_compare.py:351), drawn IO row total interest equal to the book schedule sum (test_compare.py:375), featured borrower echo from scenario.toml [site] (test_compare.py:400), 24 sorted tables of three rows in pinned structure order (test_compare.py:416), comparison meta book_sha256 equal to book meta (test_compare.py:439), and infeasible rows publishing status leaves with no numeric zeros (test_compare.py:455). All read the committed payloads and never rebuild them.

2. Freshness covers comparison.json (was Medium): PASS. test_committed_payload_freshness (test_build.py:234-285) now digests the fresh build comparison.json against the committed payloads/2026-11-10/comparison.json with a dedicated stale message and a pinned-path guard (test_build.py:262-285).

3. Infeasible na cell provenance (was Low): PASS. build.py:1065-1069 attach _DERIVED_RECEIPTS, _DERIVED_MOIC, and _DERIVED_TOTAL_INTEREST to the total_receipts, moic, and total_interest not_applicable cells, and the committed comparison.json confirms it (comparison.json:795-827, the SYN-0003 bridge row: moic carries "derived: (total receipts plus warrant value) / gross facility", total_interest carries "derived: sum of schedule period interest", total_receipts carries "derived: schedule receipts, interest plus principal or remittances"). The sizing provenance correctly remains on the facility na cell, since facility is a sizing product.

4. bands.ts comment (was Low): PASS. web/src/content/bands.ts:7-9 now states that the cap value is never derived in the browser and is read from the published sizing.band_cap envelope in the committed payloads, with the module supplying only the rule text and citation; that matches web/src/routes/sizing.ts:76 rendering the published envelope.

5. docs/tests.md stale-reference note (was Low): PASS. The failure-analysis list at docs/tests.md:59 is historical: the flagged F6 stale references were corrected in cf205fd (the cf205fd commit touches pipeline/src/vdebt/returns.py and docs/architecture.md), and the current files carry the corrected values (returns.py quotes 10.6819, 222.6, and 111.3; docs/architecture.md:1385 quotes base "7.0000" and steps 222.6 and 111.3; docs/architecture.md:1499 quotes 222.6 and 111.3; no 111.8, 55.9, 11.7898, or 1.03609589 remains in either file). A single follow-up line was appended under the phase 3 area of docs/tests.md recording the corrections as landed.

6. Vite chunk warning (was Low): PASS. web/vite.config.ts:34-36 raises build.chunkSizeWarningLimit to 2000 with the justification comment that the committed payloads are inlined intentionally under the zero runtime fetch rule, so the page bundle is large by design; npm run build emits no chunk warning (page bundle 1,356.72 kB).

Findings (Severity | File:Line | Issue | Fix):

| Severity | File:Line | Issue | Fix |
|----------|-----------|-------|-----|
| INFO | pipeline/src/vdebt/build.py:1064 and :999 | The closing_fee not_applicable cell still attaches the sizing provenance "derived: bisection, runway floor 18.0 months, DSCR floor 1.1500" instead of the per-metric _DERIVED_CLOSING_FEE, the same pattern that was corrected for moic, total_interest, and total_receipts; the facility na cell keeping the sizing provenance is correct | Optional: pass _DERIVED_CLOSING_FEE to the closing_fee _na_cell in both the shared block and row builders |

Recurring patterns: none new. The phase 3 recurring note stands: a new canonical payload must arrive in the same phase with its freshness assertion and its spec acceptance assertions; test_compare.py and the freshness extension close that gap.

Verification checklist:
- [x] All checks passed
- [x] No critical issues found
- [x] Code follows project conventions
- [x] No em dashes, double hyphens, or dash equivalents; use proper punctuation
- [x] Phase code integrates cleanly with prior phases
