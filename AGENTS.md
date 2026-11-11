# Venture Debt Engine

## Quick Commands

- Pipeline tests: `cd pipeline && uv sync && uv run pytest`
- Lint: `cd pipeline && uv run ruff check .`
- Site build: `cd web && npm install && npm run build`
- Verify gate: `cd pipeline && uv run python -m vdebt verify`
- Commands come online per the phase plan (venture-debt-spec.md section 14): tests and lint from phase 1, verify skeleton from phase 1, site build from phase 3, full verify gate from phase 5.

## Conventions

- No em dashes, en dashes, double hyphens, or Unicode dashes anywhere, in any file. Use commas, colons, and parentheses instead.
- Python 3.12 pinned; standard library only at runtime; pytest and ruff for development.
- Integer cents for money with Decimal internally; single rounding at the metric boundary (ROUND_HALF_UP).
- Seeded determinism: no wall clock in payloads; the asof date derives from the committed rate snapshot date.
- Every numeric field carries a unit; every band value carries a citation (source name and access date).
- Every published surface carries the framing line from section 4 of the spec, unedited.
- Fixtures are committed before the matching engine module and never regenerated from engine output.

## Project Map

- `pipeline/src/vdebt/` engine: money, dates, generator, sizing, schedules, pricing, returns, covenants, snapshot, cli
- `pipeline/src/vdebt_verify/` independent gate: naive recompute, import isolation, dash scan, SHA256 print; imports nothing from vdebt
- `pipeline/tests/` pytest suites
- `pipeline/fixtures/golden/` hand-computed fixtures F1 to F6 with arithmetic in comments
- `pipeline/data/rates/` committed Valet responses plus SHA256SUMS manifest
- `pipeline/config/scenario.toml` seed, bands, covenant floors, rate-shock deltas
- `web/` Vite + TypeScript static site consuming committed payloads only
- `payloads/` committed canonical outputs, never ignored by git
- `docs/` methods, architecture, decisions, reviews, tests
- `venture-debt-spec.md` build spec with the decisions log

## Status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Repo bootstrap | Complete |
| 1 | Domain model and golden fixtures | Complete |
| 2 | Generator, rates, pricing, schedules, sizing | In Progress |
| 3 | Structures, returns, comparison, site shell | Pending |
| 4 | Covenants, scenarios, dashboard, methods | Pending |
| 5 | Full verify gate, CI, case study, README | Pending |

## Deep Docs

- [Architecture](docs/architecture.md)
- [Decisions](docs/decisions.md)
- [Research](docs/research.md)
- [Reviews](docs/reviews.md)
- [Tests](docs/tests.md)
- [Methods](docs/methods.md)
