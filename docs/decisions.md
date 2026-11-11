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

## 2026-11-11 - Phase 2: Valet series selection and label patterns
**Context**: Spec section 9 lists CORRA (AVG.INTWO) and the 3-month and 6-month bill series as candidates for the floating base rate; series labels are unverified until an assertion runs.
**Decision**: Primary series BTB3M (3-month bill), fallback BTB6M; AVG.INTWO rejected as an overnight fixing that is noisy for monthly accrual. Expected label patterns are regexes committed in scenario.toml [rates], asserted at every engine load and at refresh; a mismatch raises printing the actual label verbatim and fails the run. Patterns are best-effort until the first refresh, which may tighten them in a reviewed commit. Owner confirmed 2026-11-11.
**Rationale**: a 3-month bill is the standard short-rate proxy for floating loan pricing and matches the monthly reset convention; the assertion makes an unverified series fail loudly per spec.
**Trade-offs**: no runtime fallback (config change plus this log required); patterns may need one tightening pass after the first live refresh.

## 2026-11-11 - Phase 2: Derived term parameters instead of new draws
**Context**: The frozen 14-draw order (architecture section 11) has no term or tenor draw, but schedules need term, RBF horizon, and bridge tenor.
**Decision**: Term is derived: term_months = io_months + 36 amortizing periods for bullet and IO-then-straight-line (reproducing the research 4-year, 12-IO, 36-amortizing example and staying inside the 36 to 60 band); rbf horizon 24 periods at 5.00 percent remittance; bridge tenor 3 months. All pinned in [generator]. Owner confirmed 2026-11-11.
**Rationale**: adding draws would re-key the RNG stream and invalidate any committed book hash; derived values keep the stream stable and the parameters citable.
**Trade-offs**: less variety in term and tenor; accepted for determinism.

## 2026-11-11 - Phase 2: Canonical serviceability schedule for sizing
**Context**: Sizing needs a debt-service schedule per candidate facility, but bullet balloons make min DSCR degenerate (the balloon period repays the whole facility) and per-structure bases would make the 24 capacities incomparable.
**Decision**: Every borrower is sized on one canonical io-then-straight-line schedule (borrower io_months, term = io + 36) regardless of the structure draw; the borrower's own structure governs only its schedule payload and later returns. Pre-revenue borrowers size on runway only with DSCR not_applicable. Owner confirmed 2026-11-11.
**Rationale**: matches the F1 fixture's pinned structure, keeps capacities comparable across the book, and avoids a degenerate bullet predicate.
**Trade-offs**: capacity is a disclosed serviceability proxy, not the structure's own debt service (disclosed on the methods page in phase 4).

## 2026-11-11 - Phase 2: Strata slot mechanics and book hash fixture
**Context**: Spec 10.1 requires the strata manifest to guarantee at least one borrower in each of six strata while the draw stream stays aligned; the book hash must catch RNG drift.
**Decision**: SYN-0001 to SYN-0006 are strata slots: all 14 uniforms are consumed, then post-draw overrides replace named values (full consistent sets for lender-type touches), derived values recompute, and post-override band assertions run with one documented exception (ARR 0 legal iff pre_revenue). Manifest predicates are independent and asserted at build time with counts and sorted member ids. The expected book hash (sha256 over the generator's pre-pricing output document) is committed at pipeline/fixtures/generator/book_sha256.json and is exempt from the golden fixture-first rule; changing it requires a decision entry.
**Rationale**: post-draw overrides never skip a uniform; the manifest fails loudly on lost coverage; the hash fingerprint makes RNG drift a build failure with both digests printed.
**Trade-offs**: slot recipes are hand-picked (margins chosen across 5 to 15 percent all-in so the unknown base-rate level cannot flip a stratum); the hash is engine-derived by design.

## 2026-11-11 - Phase 2: Aggregate payload layout and asof
**Context**: payloads/ needs a canonical layout, byte identity, and per the determinism pins an asof derived from the committed rate snapshot date.
**Decision**: One aggregate file payloads/<asof>/book.json (meta plus rates plus the 24 borrowers with inputs, paths, pricing, sizing, schedules); <asof> equals the base-rate observation date from the committed snapshot; payloads/build_meta.json sits outside the asof directory and is excluded from byte identity. RBF and bridge borrowers publish their structure schedule as not_applicable with a phase 3 reason.
**Rationale**: one deterministic document, one book hash target, trivial two-build diff, and the site loads the whole book once for the selector and dashboard.
**Trade-offs**: no per-borrower caching (negligible at 24 borrowers) and one larger file.

## 2026-11-11 - Phase 2: Solver fidelity and excursion policy
**Context**: F1 steps 21 to 23 show the analytic DSCR bound is 8 cents too high once schedule rows quantize, and spec 10.4 makes the band-excursion check a report, not a failure.
**Decision**: The sizing bisection rebuilds freshly quantized schedule rows for every candidate facility and compares unquantized ratios to the floors (equality feasible); no analytic shortcut. The band-excursion check compares the base all-in against the total-rate columns (5 to 8 bank, 10 to 14 non-bank), publishes within_band or excursion per borrower, prints the report in the build log, and never fails the build; the shocked all-in is excluded from the check.
**Rationale**: exact agreement with the hand-computed fixture to the cent; the spec asks the excursion to be flagged and reported, with the short-rate restatement disclosed on the methods page.
**Trade-offs**: bisection is slower than an analytic bound (trivial at 60 iterations); excursions surface in the log and payload but not in the exit code.

## 2026-11-11 - Phase 2: Money band bounds are pinned in minor units
**Context**: The architecture P2.2 pins for bands.last_round, bands.arr, and bands.net_burn use whole-USD magnitudes (10000000 for 10 million USD) while declaring unit usd_minor_units, which under the section 8 vocabulary means cents. The draw rule requires lo and hi in the target storage unit (cents).
**Decision**: Money band bounds and strata money overrides are pinned in integer cents of their declared unit: bands.last_round 1,000,000,000 to 40,000,000,000 cents (10 to 400 million USD), bands.arr 200,000,000 to 15,000,000,000 cents (2 to 150 million USD), bands.net_burn 50,000,000 to 800,000,000 cents (0.5 to 8 million USD monthly); strata slot money overrides likewise in cents. The architecture P2.2 TOML block and slot table are corrected to match. Owner decision 2026-11-11.
**Rationale**: the unit vocabulary has no whole-dollar unit; every money value in this project is integer cents, so band bounds are no exception; the generator draw then needs no unit conversion at the storage boundary.
**Trade-offs**: the key names keep the min_usd and max_usd spelling from the pin while the values are cents; the unit field is authoritative and the test suite pins the conversion explicitly.

## 2026-11-11 - Phase 2: Pre-revenue flag forces ARR to zero
**Context**: pre_revenue and arr are drawn as independent uniforms, so a borrower could carry pre_revenue = yes with a nonzero ARR, contradicting spec 10.1 (pre-revenue borrowers are burn only, ARR 0) and the band exception "ARR 0 cents is legal exactly when pre_revenue holds". Before this change only the SYN-0001 strata override produced a consistent flagged borrower; SYN-0003, SYN-0013, and SYN-0018 were flagged with contradictory nonzero ARR.
**Decision**: After all 14 uniforms are consumed and before strata overrides and derived recompute, a documented consistency step (not a skipped draw) sets arr_cents = 0 whenever pre_revenue is yes; the arr uniform is still consumed and its drawn band value is then discarded by the rule, so the stream stays aligned at 336 uniforms. When pre_revenue is no, the drawn ARR stands (its band minimum is positive, so it can never be 0). The revenue path becomes all zeros from the formula (base = 0/12); the explicit all-zero branch for pre-revenue borrowers stays in place with identical output. The book_sha256 digest changes from e54388be6ffded4637e8d7d5e1cc33ff7e3faae63c5cda5502509aaef0317e14 to 317215cd56585bb5f04d79704cc3338c4412bddac8d2b85e2f80be54a5c0cbf9 (no book_sha256.json fixture is committed yet; the tester slice pins the new digest).
**Rationale**: spec 10.1 requires the flag and the ARR to agree; enforcing the rule as a post-draw consistency step keeps the frozen 11 draw order and uniform count untouched while removing the contradiction from every published surface.
**Trade-offs**: the drawn ARR for flagged borrowers is discarded (its uniform is still consumed, so the stream stays aligned and downstream draws are unchanged); only the arr_cents document field and pre_revenue stratum membership change, since revenue and CFADS paths were already all zeros for flagged borrowers.

## 2026-11-11 - Phase 2: Valet series codes corrected after first live probe
**Context**: The first live probe of the pinned series codes failed with HTTP 404: Bank of Canada Valet does not publish "BTB3M" or "BTB6M". The real published codes are "TB.CDN.90D.MID" (3-month) and "TB.CDN.180D.MID" (6-month), with real labels "Treasury bills, 3-month" and "Treasury bills, 6-month". The live observations document also differs from the shape assumed in the first reader draft: each entry nests the value under the requested series code, for example {"d": "2026-11-10", "TB.CDN.90D.MID": {"v": "2.61"}}, and the list arrives in descending date order, so "the last entry with a non-null value" would silently select the OLDEST observation instead of the base rate.
**Decision**: scenario.toml [rates] pins series = "TB.CDN.90D.MID" and fallback_series = "TB.CDN.180D.MID"; the label patterns stay as written because both real labels already match. The reader parses both the live nested shape and the flat {"d", "v"} shape, and selects the base observation as the LATEST date (lexicographic compare on the ISO date) among entries with a non-null value, independent of list order. Owner approval 2026-11-11.
**Rationale**: the 404 proves the old codes never existed; the real codes carry the same 3-month and 6-month tenor rationale recorded above. Latest-date selection is order-independent, so a descending response, an ascending one, or a planted stray entry cannot move the book close date off the true base rate.
**Trade-offs**: the series codes now contain dots, so the committed file name is TB.CDN.90D.MID.json (a dot in a file name is safe here; no code treats the code as a glob or a regex); the reader carries a small amount of shape tolerance (nested and flat) that it would not need if only one shape were ever served.
