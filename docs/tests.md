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
