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
