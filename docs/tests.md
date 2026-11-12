# Venture Debt Engine Test Results

Last updated: 2026-11-10

## Phase 1 (domain model, golden fixtures, verify skeleton)

| Check | Command | Result |
|-------|---------|--------|
| Unit and integration suite | cd pipeline && uv run pytest -q | 61 passed in 0.03s (exit 0) |
| Lint | cd pipeline && uv run ruff check . | All checks passed (exit 0) |
| Verify gate | cd pipeline && uv run python -m vdebt verify | GATE RESULT: PASS (exit 0); dash scan 39 files 0 violations, import isolation PASS, fixture validation 6 of 6 with SHA256 printed |
| Fixture self-checks (authoring time) | scratch scripts, deleted after use | F1 28 checks, F2 suite, F3 35 checks, F4 65 checks, F6 28 checks, all PASS |

Coverage notes: money round trip and HALF_UP ties, ACT/365 closed forms, month-end clamping and weekend rolls, config float ban and citation requirements, envelope validation matrix, canonical serialization float ban, planted dash and planted engine-import negatives, all six fixture envelopes with F1 three cases and F4 two cases.

Notable authoring findings: F1b published facility 418,394,934 cents with boundary verification at F and F+1 cent; interest accrues on the period opening balance (before principal settles), consistent across F1a, F1b, and F3.

## Phase 2 (rates, generator, pricing, schedules, sizing, build)

Last updated: 2026-11-11

| Check | Command | Result |
|-------|---------|--------|
| Full suite | cd pipeline && uv run pytest -q | 158 passed (exit 0) |
| Lint | cd pipeline && uv run ruff check . | All checks passed (exit 0) |
| Verify gate | cd pipeline && uv run python -m vdebt verify | GATE RESULT: PASS (exit 0); dash scan 59 files 0 violations, import isolation PASS, fixture validation 6 of 6 with SHA256 printed |
| First live rate refresh | uv run python scripts/refresh_rates.py | Series TB.CDN.90D.MID, label "Treasury bills, 3-month", observation 2026-11-10, base rate 2.6100; label assertion passed; raw bytes committed with SHA256SUMS |
| F1 sizing convergence (kill gate half one) | test_sizing.py | F1a 700,000,000 runway; F1b 418,394,934 dscr with boundary at +1 cent; F1c infeasible; all to the cent |
| F2/F3 schedule recompute | test_schedule.py | Field-by-field exact to the cent (77 checks at authoring, 10 tests committed) |
| Byte identity (kill gate half two) | test_build.py | Two subprocess builds byte-identical excluding build_meta.json; committed payload equals a fresh build |
| Strata gate | test_build.py | All six strata counts >= 1 (pre_revenue 4, zero_warrant_bank 1, infeasible 2, runway_bound 4, dscr_bound 9, rbf_shortfall 6); zero count fails the build naming the stratum |
| Draw accounting | test_generator.py | Exactly 336 uniforms, frozen order, ids SYN-0001 to SYN-0024 |
| Book hash | test_generator.py | 317215cd56585bb5f04d79704cc3338c4412bddac8d2b85e2f80be54a5c0cbf9 matches committed fixture |
| Network confinement | test_rates.py | Only pipeline/scripts/ references urllib (test unskipped after slice 3) |

Notable discoveries: pinned series codes BTB3M/BTB6M returned HTTP 404 on first live probe, corrected to the real TB.CDN.90D.MID/TB.CDN.180D.MID (decisions record 2026-11-11); live Valet observations are nested and descending, reader adapted to latest-date selection; pre-revenue flag now forces ARR to zero per spec 10.1.

## 2026-11-12 - Phase 3: F6 fixture base block corrected to the 7.0000 basis

Last updated: 2026-11-12

| Check | Command | Result |
|-------|---------|--------|
| F6 recompute pins | cd pipeline && uv run pytest tests/test_returns.py -q | 10 passed (exit 0) |
| Full suite | cd pipeline && uv run pytest -q | 176 passed (exit 0; baseline 176) |
| Lint | cd pipeline && uv run ruff check . | All checks passed (exit 0) |
| Verify gate | cd pipeline && uv run python -m vdebt verify | GATE RESULT: PASS (exit 0); dash scan 97 files 0 violations, import isolation PASS, fixture validation 6 of 6; F6 SHA256 f33dd930babc73d373b2fc5778140e33bb425c00998bb0f82e1a88f70a012e37 (before: b0ff70777460f29b0dab1f8880e954475b80bfac6b487039d1a28b6b4107db96) |
| Fixture self-checks (throwaway, deleted after use) | stdlib scratch scripts, never importing vdebt | structural diff vs git HEAD: exactly the 29 allowed paths changed, all shocked and untouched paths byte-identical; 93 arithmetic, envelope, and recomputation checks all PASS |

| Test | Verdict | Notes |
|------|---------|-------|
| test_returns.py suite (10 tests) | PASS | new pins: all_in_rate_base 7.0000, base rows 594,521 / 536,986 / 613,699 / 556,164 / 594,521, total_interest_base 2,895,891, final flow 100,894,521, base_irr 10.6819, delta 222.6, sensitivity 111.3, moic 1.03 |
| Shocked-side pins | PASS | 9.0000, 12.9080, 3,723,288, shocked NPV brackets all unchanged and still passing |
| Whole pipeline suite | PASS | 176 passed, matching the baseline count |
| Verify gate | PASS | exit 0, fixture validation 6 of 6, new F6 digest printed |

Coverage summary: the F6 base block now recomputes end to end at the 7.0000 basis from engine code inside pytest (schedule rows, aggregated lender flows, IRR with NPV bracket at published plus and minus the 0.0001 percent tolerance, delta, sensitivity, MOIC), every rewritten fixture arithmetic string was verified arithmetically true against an independent stdlib recomputation (Fraction rows plus a prec-50 Decimal bisection), and the shocked block was proven byte-identical to git HEAD.

Failure analysis: no test failures after the correction. Authoring note for future scratch work: interest must accrue on per-period span days (31, 28, 32, 29, 31) while NPV discounting uses cumulative days from close (31, 59, 91, 120, 151); conflating the two silently produces cumulative nonsense rows. Stale references outside the agreed scope, left for a follow-up decision: pipeline/src/vdebt/returns.py docstrings still quote F6 step 31 as 111.8, step 32 as 55.9, base 11.7898, and the MOIC example 103,609,589 / 100,000,000 = 1.03609589; docs/architecture.md line 1385 still quotes F6 base "8.0000" in the same P3.3 sentence whose two step value quotes were updated, and the P3.8 test plan bullet still says irr_delta_bp 111.8 and sensitivity 55.9.
F6 stale-reference follow-up: the returns.py docstring and docs/architecture.md P3.3 and P3.8 quote corrections listed above landed in commit cf205fd; the stale list is historical.

## Phase 3 (RBF and bridge schedules, returns, comparison, payloads, site shell)

Last updated: 2026-11-12

| Check | Command | Result |
|-------|---------|--------|
| Full suite | cd pipeline && uv run pytest -q | 187 passed (exit 0) |
| Lint | cd pipeline && uv run ruff check . | All checks passed (exit 0) |
| Verify gate | cd pipeline && uv run python -m vdebt verify | GATE RESULT: PASS (exit 0); dash scan 105 files 0 violations, import isolation PASS, fixture validation 6 of 6 |
| F4 RBF recompute | test_schedule.py | Both cases exact to the cent plus synthetic early-cap and pre-revenue invariant cases |
| F5 bridge recompute | test_schedule.py | Interest 1,463,014 and repayment 51,463,014 exact; month-end clamp cases pass |
| F6 returns recompute (corrected basis) | test_returns.py | Base IRR 10.6819 and shocked 12.9080 within 0.0001 percent with NPV brackets; delta 222.6 bp; sensitivity 111.3 bp; moic 1.03 |
| Comparison acceptance | test_compare.py | 11 tests: identical shared inputs across three rows, citations with access dates, facility repetition, census of 24 tables, meta book_sha256 binding, infeasible status leaves |
| Byte identity over both payloads | test_build.py plus manual | Two-build diff clean excluding build_meta.json; committed book.json and comparison.json equal a fresh build |
| Site build | cd web && npm run build | Exit 0: sync, chart generation (byte-stable across two runs), tsc, vite; six pages; no chunk warning |
| Site live (spec kill criterion) | HTTP GET on the Pages URL | 200; framing line present on the landing page; Site workflow run completed success in 31 seconds |
| Zero-arithmetic audit of shipped JS | grep audits over dist | No Math, no parseInt or parseFloat, no fetch, no reduction over payload values; charts generated in Node only; framing line present on all six pages |

Notable: fixture F6 base block corrected to the 7.0000 basis per owner decision (arithmetic strings now true, shocked block untouched, decisions record 2026-11-12); stale references corrected in returns.py docstrings and architecture quotes (this section's earlier failure-analysis list is historical).
