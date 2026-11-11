# Venture Debt Engine Decisions

Last updated: 2026-11-10

Append-only decision history. New entries are appended at the bottom.

## 2026-11-10 - Phase 1: Money representation and Decimal policy
**Context**: Spec bans float money and mandates integer cents with single rounding.
**Decision**: Money is plain int cents (USD minor units, table {"USD": 2}); all arithmetic in Decimal with an explicit context prec=28, ROUND_HALF_UP passed explicitly; quantize exactly once at each storage or metric boundary; allocate_cents splits with earliest-first remainder.
**Rationale**: one rounding point, byte-stable, auditable; global decimal context mutation would make results host dependent.
**Trade-offs**: verbose envelopes and explicit context plumbing; accepted for auditability.

## 2026-11-10 - Phase 1: Metric envelope and status enum
**Context**: Spec section 8 requires units, provenance, timestamps, citations on every numeric.
**Decision**: Envelope keys value, unit, scale (iff string value), and exactly one provenance class (external: source + source_timestamp; derived: provenance + asof; generated: provenance with seed and generator version); band values add citation + band with access dates. Status enum: feasible, infeasible, not_applicable, not_granted, shortfall, no_solution, each with required companion fields.
**Rationale**: schema validation can be mechanical; not_applicable prevents fabricated zeros.
**Trade-offs**: more required fields per value; accepted since payloads are machine written.

## 2026-11-10 - Phase 1: config.py module for scenario.toml
**Context**: Spec pins scenario.toml contents but lists no loader module.
**Decision**: New module pipeline/src/vdebt/config.py with frozen dataclasses Band and ScenarioConfig and load_scenario(); AGENTS.md project map read as representative.
**Rationale**: a typed loader keeps band citations enforced at load time.
**Trade-offs**: deviates from the spec section 7 file list by one file; flagged to the user.

## 2026-11-10 - Phase 1: scenario.toml decimal policy
**Context**: TOML floats are IEEE binary64 and would poison Decimal exactness.
**Decision**: every non-integer numeric in scenario.toml is a quoted decimal string; the loader rejects float-typed values; ints allowed for seeds, counts, bp deltas.
**Rationale**: exact Decimal parsing from strings is deterministic.
**Trade-offs**: config reads slightly noisier; accepted.

## 2026-11-10 - Phase 1: Fixture format and fixture-local rates
**Context**: Spec requires hand-computed arithmetic stored with fixture numbers.
**Decision**: one JSON file per fixture with `_comment` strings and an `arithmetic` string array; inputs and expected values use envelope-lite numerics; `rate_source` is always "fixture_pinned" and fixtures never read the committed Valet snapshot; expected values are tester-authored and pre-quantized.
**Rationale**: single parser, arithmetic cannot drift from numbers, rate refreshes never break fixtures.
**Trade-offs**: JSON has no real comments and fixtures are verbose; accepted.

## 2026-11-10 - Phase 1: Fixture inputs pinned by tech lead
**Context**: Tester and backend-dev must work in parallel without agreeing on test vectors.
**Decision**: tech lead pins every fixture input (amounts, dates, rates, structures, case identities) in docs/architecture.md section 8; the tester computes expected values and arithmetic; fixture sizes capped at 6 periods.
**Rationale**: closes ambiguity; computed ground truth stays human-authored per spec section 11.
**Trade-offs**: tech lead chooses test vectors rather than neutral parties; mitigated by fixed small closed-form cases.

## 2026-11-10 - Phase 1: Dash-scan exemption table
**Context**: Spec section 13 requires a pinned exemption table.
**Decision**: Unicode category Pd plus U+2212 banned in every file with no exemptions; ASCII double hyphen banned in prose except thematic breaks and full-pipe table separator rows, and banned in source and machine-config files except inside flag tokens matching the pinned regex; unclassified extensions fail the scan.
**Rationale**: matches spec exactly and fails closed on scope growth.
**Trade-offs**: documentation cannot show long CLI flags; confirmed by the owner on 2026-11-10.

## 2026-11-10 - Phase 1: Verify package layout and isolation mechanics
**Context**: Verify gate must share no code with the engine and prove it structurally.
**Decision**: vdebt_verify ships __init__, __main__, dash_scan, isolation, fixture_runner, gate; it imports stdlib only; static grep scan with an assembled regex plus runtime sys.modules assertions at gate start and end; only vdebt.cli may import vdebt_verify; python -m vdebt verify is the canonical command.
**Rationale**: independence is enforced twice (static and runtime) and the CLI shape matches AGENTS.md.
**Trade-offs**: some naive logic duplicated later; that duplication is the point.

## 2026-11-10 - Phase 1: Determinism pins
**Context**: Byte-identical rebuilds are a blocking CI gate.
**Decision**: Python 3.12.11 in .python-version (fallback: newest 3.12.x, recorded here); canonical JSON per architecture section 6.4; asof derived from the committed rate snapshot date; RNG is one random.Random(seed) using only .random() with linear inverse-CDF draws via Decimal(repr(u)); frozen 14-draw per-borrower order over sorted borrower ids, with strata overrides consuming draws.
**Rationale**: every source of nondeterminism is pinned at one place.
**Trade-offs**: float uniform enters the pipeline but is converted deterministically via repr; accepted.

## 2026-11-10 - Phase 1: Date conventions
**Context**: Schedule dates must be exact and testable against closed forms.
**Decision**: monthly dates preserve day-of-month with end-of-month clamping; first payment one calendar month after close; business days Monday to Friday with no holiday table; roll forward only; strictly increasing asserted; close must be a business day; ACT/365 accrual on actual spans.
**Rationale**: standard credit convention, hand-checkable, matches spec sections 5 and 10.3.
**Trade-offs**: no holiday calendar means some payment dates land on holidays in reality; disclosed on the methods page.

## 2026-11-10 - Phase 1: IRR solver pins
**Context**: IRR bisection needs a bracket, tolerance, and failure semantics.
**Decision**: Decimal bisection on r in [-0.99, 10.00], terminate below 0.000001 (0.0001 percent), max 200 iterations as hard failure; no sign change yields status no_solution with reason; published IRR quantized once to percent scale 4; verify tolerance 0.0001 percent absolute.
**Rationale**: monotone NPV gives provable convergence; explicit statuses prevent fabricated returns.
**Trade-offs**: bisection is slower than Newton; negligible at this scale.

## 2026-11-10 - Phase 1: Sizing bisection in integer cents
**Context**: Spec states bracket width below 1 cent and fixtures pin facility to the cent.
**Decision**: the sizing search runs on the integer-cent grid (lo feasible, hi infeasible, loop while hi minus lo > 1, answer lo, 60-iteration hard failure).
**Rationale**: exact largest feasible cent with no off-by-one from Decimal flooring.
**Trade-offs**: reinterprets "below 1 cent" as "two adjacent cents"; recorded here so the deviation is explicit.

## 2026-11-10 - Phase 1: MOIC numerator excludes closing fee
**Context**: Spec section 5 defines MOIC as (interest + principal + warrant value) / funded while the fee sits in IRR cash flows.
**Decision**: MOIC follows the spec formula literally; the closing fee affects IRR only; the asymmetry is stated on the methods page.
**Rationale**: all money conventions must match the spec exactly.
**Trade-offs**: MOIC understates total lender proceeds by the fee; disclosed, confirmed by the owner on 2026-11-10.

## 2026-11-10 - Phase 1: F1 fixture carries three sizing cases
**Context**: Spec 10.2 acceptance needs a pinned facility and an infeasible-borrower assertion.
**Decision**: sizing_fixture.json holds cases F1a runway-bound, F1b DSCR-bound, F1c infeasible at zero draw, with tech-lead-pinned inputs and tester-computed expected values.
**Rationale**: one fixture file covers the full solver acceptance surface.
**Trade-offs**: none material; case count is small and closed-form.

## 2026-11-10 - Phase 1: Python patch pin fallback
**Context**: Architecture section 11 fallback rule: if 3.12.11 is unavailable locally, pin the newest installed 3.12.x and record the chosen patch here.
**Decision**: .python-version pins 3.12.13 because uv resolved CPython 3.12.13 on this machine and 3.12.11 is not installed.
**Rationale**: patch-level drift within the 3.12 minor is tolerated by the byte-identity gate as long as it is recorded and CI pins the same patch.
**Trade-offs**: local and CI must both run 3.12.13 for byte-identical rebuilds; enforced later by the CI Python version pin.

## 2026-11-10 - Phase 1: Import isolation runtime rule refined
**Context**: The literal rule (no sys.modules key named vdebt) cannot hold when the gate runs through the canonical command python -m vdebt verify, because the launcher loads vdebt and vdebt.cli before the gate starts.
**Decision**: The runtime check becomes a snapshot delta check: the gate records engine-package keys present at entry (the launcher shell) and fails if any NEW engine key appears by gate end. The static source scan still proves vdebt_verify files import nothing from vdebt. Owner decision 2026-11-10.
**Rationale**: proves the gate itself never imports engine code while keeping the canonical command green.
**Trade-offs**: launcher keys are trusted as pre-existing; mitigated by the static scan covering the verify package source.

## 2026-11-10 - Phase 1: Dash scan classifies .lock as machine config
**Context**: pipeline/uv.lock was an unclassified extension, which fails the scan by design.
**Decision**: the source/machine-config row of the dash-scan table in docs/architecture.md section 9 gains *.lock, so lock files get the flag-token exemption like other machine config. Owner decision 2026-11-10.
**Rationale**: explicit scope beats a silent exclusion; uv.lock is committed machine-generated config.
**Trade-offs**: scan covers one more large generated file; negligible cost.

## 2026-11-11 - Phase 1: Fixture-first commit ordering reconstructed
**Context**: Spec section 11 item 7 requires the six golden fixtures to be committed before any engine module exists. Authoring overlapped (fixtures and engine were built in parallel for speed), so the literal wall-clock ordering was not preserved in the working tree.
**Decision**: Phase 1 lands as two commits: commit 1 contains only pipeline/fixtures/golden/ (F1 to F6), commit 2 contains the engine, verify skeleton, tests, config, and docs. Owner decision 2026-11-11.
**Rationale**: fixture integrity does not depend on commit timing alone. Every expected value was hand-computed from the spec formulas with standalone scratch scripts that were deleted after use and never imported engine code for derivation (F1b cross-checked its hand-derived exact fractions against engine functions only as a validation step after the values were fixed). The gate now fails on any missing fixture, and the fixture envelope carries arithmetic for every expected value.
**Trade-offs**: git history shows the fixtures one commit ahead of the engine rather than authored days earlier; a reviewer reading history sees ordering, not authoring time. Accepted as the honest reconstruction.
