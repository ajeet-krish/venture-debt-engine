# Venture Debt Engine Architecture

Last updated: 2026-11-10
Phase: 1 of 6 (domain model, golden fixtures, verify skeleton)

Notation: in pattern definitions the ASCII hyphen-minus is written as \u002d so this
file passes the repository dash scan. "Double hyphen" always means two consecutive
U+002D characters.

---

## 1. Overview

A Python pipeline sizes synthetic venture debt facilities and computes lender
returns, publishes canonical JSON payloads, and is verified by an independent
gate that shares no code with the engine. This document is the single canonical
home for module interfaces, the scenario.toml schema, the fixture format, the
dash-scan exemption table, and the determinism pins.

## 2. Phase scope

Phase 1 delivers the domain model (money, dates, band config, snapshot writer),
the six golden fixtures F1 to F6 (inputs and schemas pinned here, expected values
and arithmetic authored by 06-tester, committed before any engine module), and
the verify skeleton (dash scan, import isolation, fixture runner) which runs with
no engine dependency. Phases 2 to 5 build on these pins without renegotiation;
any change to a pin requires a new entry in docs/decisions.md.

## 3. Component breakdown

| Component | Responsibility | File |
|-----------|---------------|------|
| Money domain | integer cents, minor-unit table, Decimal context, quantize | pipeline/src/vdebt/money.py |
| Dates domain | ACT/365, business-day roll forward, monthly payment dates | pipeline/src/vdebt/dates.py |
| Config loader | scenario.toml schema, bands with citations, solver params | pipeline/src/vdebt/config.py |
| Snapshot writer | metric envelope, status enum, canonical JSON, schema check | pipeline/src/vdebt/snapshot.py |
| Fixtures | hand-computed ground truth for F1 to F6 | pipeline/fixtures/golden/*.json |
| Verify package | dash scan, import isolation, fixture runner, gate | pipeline/src/vdebt_verify/*.py |
| CLI shell | python -m vdebt verify | pipeline/src/vdebt/__main__.py, cli.py |

Dependency rule: vdebt_verify imports the standard library only. vdebt.cli may
import vdebt_verify (one direction only). Nothing in vdebt_verify may import
vdebt, ever.

## 4. Data flow

See section 2 of the phase 1 architecture handoff. In short: scenario.toml and
committed rate snapshots feed the engine; the engine writes payloads through
snapshot.write_payload; the gate reads committed artifacts as plain files and
recomputes naively; the site reads payloads and formats only.

## 5. Technology decisions

| Decision | Choice | Why | Rejected | Trade-off |
|----------|--------|-----|----------|-----------|
| Money | int cents, Decimal prec 28 ROUND_HALF_UP inside | single rounding point | float | verbosity |
| Config decimals | quoted decimal strings, TOML floats banned | exact Decimal, deterministic | TOML float | config noise |
| Fixture format | JSON with arithmetic string arrays | one parser, auditable | markdown sidecar | comments are data |
| Fixture rates | fixture-local pinned rates | rate refresh never breaks fixtures | read live snapshot | rate reader tested separately |
| Verify | stdlib only, grep scan plus sys.modules assertion | independence structural | shared utils | duplicated naive code |
| Dash scan | Unicode Pd and U+2212 banned all files; ASCII double hyphen per exemption table | spec section 13 | per-file allowlists | no long flags in docs |
| IRR | Decimal bisection, bracket -0.99 to 10.00 | monotone NPV | Newton | more iterations |
| Sizing | integer-cent bisection | exact to the cent | Decimal bracket + floor | wording normalized in decisions log |
| Python | 3.12.11 (.python-version) | byte identity | floating 3.12 | patch fallback documented |

## 6. Interfaces

### 6.1 money.py

```python
MINOR_UNITS: dict[str, int] = {"USD": 2}
DEFAULT_CURRENCY = "USD"
MONEY_CONTEXT: decimal.Context  # prec=28, rounding=ROUND_HALF_UP, created once

def minor_units(currency: str = DEFAULT_CURRENCY) -> int
def to_cents(amount: Decimal | str, currency: str = DEFAULT_CURRENCY) -> int
def from_cents(cents: int, currency: str = DEFAULT_CURRENCY) -> Decimal
def quantize(value: Decimal, scale: int) -> Decimal
def format_fixed(value: Decimal, scale: int) -> str
def parse_fixed(text: str, scale: int) -> Decimal
def add_cents(values: Iterable[int]) -> int
def allocate_cents(total: int, n: int) -> list[int]
```

Pins:
- Money is a plain Python int of minor units (USD cents). A NewType alias may
  exist for static checking only; runtime type is int. Float is banned for money.
- MONEY_CONTEXT is passed explicitly to every Decimal operation. The global
  decimal context is never mutated (decimal.setcontext is banned).
- Quantize happens exactly once per value at its storage or metric boundary,
  ROUND_HALF_UP. Internal intermediates are never rounded.
- Metric scales: percent_annual 4, percent 4 for rates and 2 for band
  percentages, multiple 2, ratio 4, bp 1, months 1, usd_minor_units 0 (int).
- to_cents is the money boundary: Decimal dollars quantized to integer cents
  with ROUND_HALF_UP. from_cents is exact.
- allocate_cents splits total cents into n parts as equal as possible: each part
  gets floor(total / n) and the first (total mod n) parts get one extra cent.
  Sum is preserved exactly. Used for straight-line principal and equal splits.
- format_fixed renders a fixed-scale string ("1.2100" at scale 4). parse_fixed
  rejects wrong scale or extra digits.

### 6.2 dates.py

```python
def days_between(start: date, end: date) -> int
def year_fraction_act365(start: date, end: date) -> Decimal  # Decimal(days) / 365
def is_business_day(d: date) -> bool                         # Monday to Friday
def roll_forward(d: date) -> date                            # next business day on or after d
def add_months(d: date, months: int) -> date                 # DOM preserved, month-end clamped
def payment_dates(close: date, n_periods: int) -> list[date]
```

Pins (section 12 for full rules):
- ACT/365: year fraction is actual days divided by 365, computed in Decimal at
  MONEY_CONTEXT precision. A full non-leap year is exactly 1.
- Business days are Monday to Friday. There is no holiday table. This is
  disclosed on the methods page.
- roll_forward never moves a date backward: a date that is already a business
  day is returned unchanged.
- add_months preserves the day of month and clamps to the target month's last
  day (Jan 31 plus 1 month is Feb 28 in 2026).
- payment_dates returns roll_forward(add_months(close, k)) for k = 1..N and
  asserts strictly increasing (raises ValueError on violation, never silently).
- The close date must be a business day (asserted by callers that fund).

### 6.3 config.py (scenario.toml loader)

```python
@dataclass(frozen=True)
class Band:
    min: Decimal
    max: Decimal
    unit: str
    scale: int
    citation: str
    band: str

@dataclass(frozen=True)
class ScenarioConfig:
    seed: int
    generator_version: str
    currency: str
    rounding_mode: str
    bands: dict[str, Band]
    runway_floor_months: Decimal
    dscr_floor: Decimal
    rate_shock_bp: Decimal
    warrant_valuation_factor: Decimal
    downside_burn_uplift_pct: Decimal
    downside_revenue_downlift_pct: Decimal
    solver: SolverParams

def load_scenario(path: Path) -> ScenarioConfig
```

Pins:
- TOML decimal policy: every non-integer numeric value in scenario.toml is a
  quoted decimal string. Native TOML floats are banned; the loader raises on any
  float-typed value. Integers are allowed for seeds, counts, and bp deltas.
- The loader converts every numeric to Decimal on return (ints convert exactly).
- Every band entry carries citation and band keys; the loader raises if missing.

### 6.4 snapshot.py (canonical writer)

```python
Status = Literal[
    "feasible", "infeasible", "not_applicable",
    "not_granted", "shortfall", "no_solution",
]
STATUS_VALUES: frozenset[str]

def envelope(
    value: int | str,
    unit: str,
    *,
    scale: int | None = None,
    provenance: str | None = None,
    source: str | None = None,
    source_timestamp: str | None = None,
    asof: str | None = None,
    citation: str | None = None,
    band: str | None = None,
) -> dict[str, object]

def validate_payload(payload: Mapping[str, object]) -> list[str]  # error strings
def canonical_dumps(payload: Mapping[str, object]) -> str
def write_payload(path: Path, payload: Mapping[str, object]) -> str  # sha256 hex
```

Metric envelope shape (every numeric leaf in every payload):

```json
{
  "value": "1.2100",
  "unit": "ratio",
  "scale": 4,
  "provenance": "derived: min over periods of CFADS / debt service",
  "asof": "2026-11-10"
}
```

Envelope validation matrix:
- unit is required and must come from the vocabulary: usd_minor_units, percent,
  percent_annual, multiple, ratio, bp, months, count, date, sha256_hex, status.
- value is a JSON integer iff unit is usd_minor_units or count. Otherwise a
  fixed-scale string with scale present. date and sha256_hex and status carry
  plain strings with no scale.
- Provenance class, exactly one: external (source + source_timestamp), derived
  (provenance starting with "derived: " + asof), generated (provenance of the
  form "synthetic seed=20261110 generator v1" + asof).
- Band values (rule-of-thumb derived values) additionally require citation and
  band, and citation must contain an access date.
- Status values carry a companion: infeasible needs binding_constraint,
  not_applicable needs reason, shortfall needs residual amount, no_solution
  needs reason.

Status semantics:
- feasible: solution found and all floors satisfied.
- infeasible: no value satisfies the floors; binding constraint named.
- not_applicable: metric undefined (for example DSCR when ARR is 0); reason
  string required, never a fabricated zero.
- not_granted: instrument not granted (for example no warrant); value published
  as zero with this status, never a missing field.
- shortfall: obligation partially repaid within the horizon (RBF residual);
  residual amount published.
- no_solution: solver found no root (for example IRR cash flows with no sign
  change); reason published.

Canonical serialization (enforced by write_payload):
- UTF-8, LF line endings, json.dumps(obj, sort_keys=True, indent=2,
  ensure_ascii=False, allow_nan=False) plus a trailing newline.
- Integers (cents, counts) serialize as JSON numbers. Non-integer decimals
  serialize as fixed-scale strings. Floats are banned anywhere in payloads;
  canonical_dumps raises TypeError on a float value.
- Keys sorted recursively. No wall clock, no git data, no environment data in
  payloads. build_meta.json (wall clock, git SHA) lives outside payloads.

## 7. scenario.toml schema (phase 1 keys)

File: pipeline/config/scenario.toml. Values below are pinned. Decimal values are
quoted strings. Citations travel with every band.

```toml
[meta]
seed = 20261110
generator_version = "v1"
currency = "USD"
rounding_mode = "ROUND_HALF_UP"

[sizing]
runway_floor_months = "18.0"
dscr_floor = "1.1500"
bisection_max_iterations = 60

[solver.irr]
bracket_low = "-0.99"
bracket_high = "10.00"
tolerance_decimal = "0.000001"
max_iterations = 200

[scenario]
rate_shock_bp = 200

[downside]
burn_uplift_pct = "50.00"
revenue_downlift_pct = "30.00"

[warrant]
valuation_factor = "0.15"
placement = "final_payment_date"

[bands.facility_sizing]
min_pct_of_last_round = "20"
max_pct_of_last_round = "50"
unit = "percent"
scale = 2
band = "facility_sizing_band_20_50_pct_of_last_round"
citation = "Mercury venture debt term sheet guide (accessed 2026-11-10)"

[bands.interest_only]
min_months = 12
max_months = 18
unit = "months"
scale = 1
band = "interest_only_12_18_months"
citation = "Mercury venture debt term sheet guide (accessed 2026-11-10)"

[bands.term]
min_months = 36
max_months = 60
unit = "months"
scale = 1
band = "loan_term_3_5_years"
citation = "Mercury venture debt term sheet guide (accessed 2026-11-10)"

[bands.warrant_coverage]
bank_min_pct = "0"
bank_max_pct = "5"
nonbank_min_pct = "10"
nonbank_max_pct = "20"
unit = "percent"
scale = 2
band = "warrant_coverage_0_20_pct_banks_0_5_nonbanks_10_20"
citation = "Mercury venture debt term sheet guide (accessed 2026-11-10)"

[bands.pricing_bank]
spread_min_pct = "1"
spread_max_pct = "3"
total_rate_min_pct = "5"
total_rate_max_pct = "8"
unit = "percent_annual"
scale = 4
band = "bank_pricing_prime_plus_1_3_rebased_short_rate_plus_spread"
citation = "Mercury venture debt term sheet guide (accessed 2026-11-10)"

[bands.pricing_nonbank]
spread_min_pct = "3"
spread_max_pct = "4"
total_rate_min_pct = "10"
total_rate_max_pct = "14"
unit = "percent_annual"
scale = 4
band = "nonbank_pricing_prime_plus_3_4_rebased_short_rate_plus_spread"
citation = "Mercury venture debt term sheet guide (accessed 2026-11-10)"

[bands.closing_fee]
min_pct = "0.5"
max_pct = "2"
unit = "percent"
scale = 2
band = "closing_fee_0_5_2_pct"
citation = "Mercury venture debt term sheet guide (accessed 2026-11-10)"

[bands.rbf_cap]
min_multiple = "1.3"
max_multiple = "2.5"
unit = "multiple"
scale = 2
band = "rbf_cap_multiple_1_3_2_5x"
citation = "Mercury venture debt term sheet guide (accessed 2026-11-10)"

[bands.gross_margin]
min_pct = "50"
max_pct = "80"
unit = "percent"
scale = 2
band = "cfads_gross_margin_band_50_80_pct"
citation = "Venture Debt Engine stated proxy assumption, not a market quote (accessed 2026-11-10)"

[bands.revenue_growth_monthly]
min_pct = "0.5"
max_pct = "3.0"
unit = "percent"
scale = 4
band = "monthly_revenue_growth_band_0_5_3_0_pct"
citation = "Venture Debt Engine stated growth path assumption, not a market quote (accessed 2026-11-10)"

[bands.dscr_floor_reference]
value = "1.1500"
unit = "ratio"
scale = 4
band = "dscr_floor_1_15x_reference"
citation = "SBA DSCR reference via 2026-11-10 research (accessed 2026-11-10)"
```

Notes:
- Pricing bands are restated as short rate plus spread (spec decision log). The
  total-rate columns (5 to 8, 10 to 14) are used by the build-time band-excursion
  check only, with the restatement disclosed on the methods page.
- The gross-margin and growth bands and the warrant valuation factor are stated
  project assumptions, labeled as such, never as market quotes.
- Reserved for later phases: [generator] strata_slots, [covenants] floors,
  [rates] series codes and expected label patterns.

## 8. Golden fixture format (F1 to F6)

Decision: one JSON file per fixture with `_comment` string fields and an
`arithmetic` array of strings holding the hand-computed steps. Rationale: one
file, one parser (json.load), the arithmetic can never drift from the numbers it
explains, and the verify gate stays trivial. A markdown sidecar was rejected
because it needs a second parser and can diverge silently.

Common envelope (every fixture file):

```json
{
  "fixture_id": "F2",
  "title": "bullet schedule closed form",
  "authored_by": "06-tester",
  "authored_phase": 1,
  "rate_source": "fixture_pinned",
  "_comment": "Free-text provenance note. Expected values are ground truth.",
  "arithmetic": ["step 1: ...", "step 2: ..."],
  "inputs": { },
  "expected": { },
  "tolerances": { }
}
```

Fixture value rules:
- Inputs and expected values use envelope-lite numerics: {"value": ..., "unit":
  ..., "scale": ...} with the same serialization rules as payloads (int cents,
  fixed-scale strings). Comparisons are exact string or int equality by default.
- `arithmetic` must be non-empty and must show the day counts and the quantize
  step for every expected money value and every published ratio or rate.
- `rate_source` must equal "fixture_pinned". The runner fails any fixture that
  references Valet data, pipeline/data/rates, or the committed snapshot in any
  key or string. Fixture-local pinned base rates live in inputs.base_rate with a
  `source` of "fixture pinned rate, not a Valet observation".
- Expected values must already be quantized at their metric boundary
  (ROUND_HALF_UP) exactly as the engine will publish them.
- Tolerances are only allowed where the spec allows: IRR fields abs 0.0001
  (percent units); all money fields are exact to the cent (tolerance 0).
- Fixture inputs are engine test vectors and are exempt from rule-of-thumb band
  validation. The runner asserts fixtures are never fed through band checks.

F1 sizing_fixture.json (3 cases in a `cases` array):

| Case | Identity | Pinned inputs |
|------|----------|---------------|
| F1a | runway-bound, feasible | close 2026-04-30; last_round 20,000,000.00 USD; cash 25,000,000.00; net_burn_monthly 1,000,000.00; revenue_monthly 1,100,000.00; gross_margin_pct "50.00"; monthly_growth_pct "0.0000"; structure io_then_straight_line; term 30; io 12; all_in_rate "8.0000"; band_cap_pct "50.00" |
| F1b | DSCR-bound, feasible | same as F1a but revenue_monthly 600,000.00 |
| F1c | infeasible at zero draw | same as F1a but cash 1,500,000.00 |

Expected per case: status; for feasible cases facility_usd (integer cents),
binding_constraint ("runway" or "dscr"), post_draw_runway_months (scale 1),
min_dscr (scale 4); for F1c status "infeasible", binding_constraint "runway",
and no facility value. The arithmetic must show the analytic boundary and the
step to the largest feasible whole cent. F1a's runway boundary is defined by
cash minus runway floor times monthly burn; F1b's boundary is the CFADS divided
by the floor times the first amortizing period debt service per unit of
facility, with the first amortizing period identified by date.

F2 schedule_bullet.json: facility 100,000,000 cents (1,000,000.00 USD); close
2026-03-31; term 4; base_rate "4.0000"; spread "4.0000" (non-bank band edge);
closing_fee_pct "1.00"; warrant_coverage_pct "0.00". Expected: all_in_rate
"8.0000", payment_dates (4 ISO dates), schedule rows (opening balance,
interest, principal, closing balance), total_interest, total_principal,
closing_fee, moic (scale 2), warrant status "not_granted" with value 0.
MOIC per spec section 5: (interest + principal + warrant value) / gross facility.

F3 schedule_io_amort.json: facility 30,000,000 cents (300,000.00 USD); close
2026-07-31; term 6; io 3; all_in_rate "8.0000"; closing_fee_pct "1.00";
warrant_coverage_pct "0.00". Straight-line principal is 3 equal monthly
payments (allocate_cents over 3 periods). Expected: dates, 6 schedule rows,
totals, moic.

F4 schedule_rbf.json (2 cases): advance 60,000,000 cents (600,000.00 USD);
close 2026-05-01; remittance_rate_pct "5.00"; cap_multiple "1.50" (cap
900,000.00 USD); no interest, no fee, no warrant. Case F4a cap hit exact:
horizon 5, per-period revenue [4,000,000.00, 4,000,000.00, 4,000,000.00,
4,000,000.00, 2,000,000.00] USD. Cumulative remittance must reach the cap
exactly in period 5 (the final remittance is the exact residual). Case F4b
shortfall: horizon 3, per-period revenue [1,000,000.00, 1,000,000.00,
1,000,000.00]. Expected: per-period remittances, cumulative, final cumulative,
residual (0 in F4a), status ("feasible" in F4a, "shortfall" with residual in
F4b). RBF remittances are computed in period arrears as the rate times period
revenue, capped at the residual to the cap.

F5 bridge_fixture.json: facility 50,000,000 cents (500,000.00 USD); close
2026-02-27; tenor 3 months; all_in_rate "12.0000" (non-bank band edge);
closing_fee_pct "2.00"; warrant_coverage_pct "0.00". Bridge accrues simple
interest ACT/365 from close to maturity and repays principal plus all accrued
interest in one payment at maturity. Expected: maturity_date, interest,
total_repayment, closing_fee, moic (scale 2), warrant status "not_granted".

F6 rate_shock_fixture.json: facility 100,000,000 cents (1,000,000.00 USD);
structure bullet; close 2026-01-05; term 5; base_rate "4.0000"; spread
"3.0000" (bank band edge); rate_shock_bp 200; closing_fee_pct "1.00";
warrant_coverage_pct "2.00"; warrant_valuation_factor "0.15" (warrant value
dated at the final payment date). Expected: base schedule rows, shocked
schedule rows (base_rate "6.0000"), base_irr_percent, shocked_irr_percent,
irr_delta_bp (scale 1), sensitivity_per_100bp_irr_bp (scale 1), total_interest
base and shocked, warrant_value, moic. Tolerances: both IRR fields abs 0.0001.
The arithmetic must bracket each IRR with NPV sign evaluations at the pinned
value minus and plus the tolerance.

Fixture size guidance: F2 has 4 periods, F3 has 6, F5 has 3 months (single
payment), F6 has 5 periods, F4 has 5 plus a 3-period case, F1 has no schedule
rows (analytic cases only). No fixture exceeds 6 periods. All fixture closes
are business days. F2, F3, F6 pin closes on day-of-month 31, 31, and 5
specifically to exercise end-of-month clamping and weekend rolling; F4 and F5
exercise plain monthly arithmetic and a month with a clamped target.

## 9. Dash-scan rules (pinned exemption table)

Scope: every file under the repository root except the excluded paths below.

| Class | Files | Double hyphen rule |
|-------|-------|--------------------|
| Prose | *.md, *.rst, *.txt, LICENSE, CHANGELOG, and any extensionless file | banned except thematic break lines and table separator rows |
| Source and machine config | *.py, *.ts, *.tsx, *.js, *.json, *.toml, *.yml, *.yaml, *.sh, *.html, *.css, *.svg, *.lock, .gitignore, .python-version | banned except inside command-line flag tokens |

Banned with no exemptions anywhere (any file class, any context including code,
strings, URLs, and comments): every codepoint in Unicode category Pd (covers
U+2010, U+2011, U+2012, U+2013, U+2014, U+2015, U+2053, U+2E3A, U+2E3B, U+FE58,
U+FE63, U+FF0D) plus U+2212 MINUS SIGN. Implementation: per character,
unicodedata.category(ch) == "Pd" or ch == "\u2212".

ASCII double hyphen exemptions, exact patterns:
- Thematic break lines (prose only): `^\s*-{3,}\s*$`
- Table separator rows (prose only, full-pipe rows are the project convention):
  `^\s*\|(?:\s*:?-+:?\s*\|)+\s*$`
- Command-line flag tokens (source and machine config only):
  `(?<![A-Za-z0-9\u002d])\u002d\u002d[A-Za-z][A-Za-z0-9\u002d]*`

Any other double hyphen occurrence fails the scan. Pattern literals in scanned
source must be written with \u002d escapes (or assembled at runtime) so the
scanner module passes its own scan.

Excluded paths: .git/, node_modules/, __pycache__/, .venv/, dist/,
.pytest_cache/, .ruff_cache/, .mypy_cache/, and binary extensions (png, jpg,
jpeg, gif, ico, woff, woff2, pdf, zip). Files that fail UTF-8 decode are
counted and reported as skipped. Any other file extension is a scan failure
(unclassified), so scope changes are explicit and reviewed.

Report format: path, line, column, offending codepoint, rule id
(unicode_dash, double_hyphen_prose, double_hyphen_source). Exit non-zero on any
violation. Entry points: python -m vdebt_verify dash and python -m vdebt verify.

## 10. Verify skeleton (vdebt_verify)

Layout:

```text
pipeline/src/vdebt_verify/
  __init__.py        empty docstring only, zero imports of vdebt
  __main__.py        argparse: all (default), dash, imports, fixtures
  dash_scan.py       section 9 rules
  isolation.py       static grep scan plus runtime sys.modules assertion
  fixture_runner.py  fixture schema validation (phase 1), compare mode (phase 3+)
  gate.py            run_all orchestration, SHA256 print, exit code
```

Import isolation check (both halves run in every gate invocation):
- Static: for each *.py under pipeline/src/vdebt_verify/, fail on any line
  matching `^\s*(?:from|import)\s+vdebt(?:\s|\.|$)`. The pattern is assembled
  from fragments at runtime so the scanner cannot match its own source.
  vdebt_verify itself is allowed (the pattern does not match it).
- Runtime: at gate start and again at gate end, assert that no sys.modules key
  equals "vdebt" or starts with "vdebt.". Violation raises IsolationError and
  the gate exits non-zero.

Fixture runner:
- Phase 1 mode validate_only: load each of the six fixture files, enforce the
  envelope rules from section 8 (fixture_id in F1..F6, arithmetic non-empty,
  rate_source "fixture_pinned", no Valet references, envelope-lite numerics,
  tolerances only where allowed).
- Phase 3 mode compare: compare fixture `expected` against engine-emitted
  fixture outputs written by the engine CLI to pipeline/build/fixtures/ (git
  ignored) via the normal snapshot writer. The runner reads both sides as plain
  JSON files; it never imports the engine.

Gate CLI shape: python -m vdebt verify runs vdebt_verify.gate.run_all(). Also
runnable directly as python -m vdebt_verify. Exit 0 on pass, 1 on any failure.
Output lists each check with PASS or FAIL and prints SHA256 for every artifact
it read (recorded as printed, not claimed as reverified). The gate reads
committed artifacts only: payloads if present, fixtures always. It never calls
the network and never imports engine code.

Phase 1 acceptance: python -m vdebt verify runs green on a clean tree and exits
non-zero on a planted Unicode dash or a planted engine import.

## 11. Determinism pins

- Python: 3.12.11 in .python-version at the repo root; pipeline/pyproject.toml
  sets requires-python = ">=3.12,<3.13". Fallback rule: if 3.12.11 is
  unavailable locally, pin the newest installed 3.12.x and record the chosen
  patch in docs/decisions.md. Minor-version drift is the byte-identity risk;
  patch drift is tolerated but recorded.
- Decimal: MONEY_CONTEXT (prec=28, ROUND_HALF_UP), passed explicitly, global
  context never mutated. Rounding mode is declared on the methods page.
- Canonical JSON: section 6.4. Integers as numbers, non-integer decimals as
  fixed-scale strings, keys sorted, LF, two-space indent, trailing newline,
  allow_nan=False, floats banned. Byte identity is defined over this encoding.
- asof: derived from the committed rate snapshot date (the source_timestamp of
  the base-rate observation used at close). Never the wall clock. Fixtures pin
  their own asof. build_meta.json (wall clock, git SHA) is excluded from byte
  identity.
- RNG: one instance of random.Random(seed) with seed 20261110 from
  scenario.toml. Only .random() is called (no randrange, no choice, no
  shuffle). Continuous draws: x = lo + u * (hi - lo) computed as
  Decimal(repr(u)) in MONEY_CONTEXT, where lo and hi are the band bounds in the
  draw's target unit; the draw is quantized once at its storage boundary.
  Discrete draws: labels sorted by label text, cumulative thresholds computed
  from pinned weights, first label whose cumulative exceeds u.
- Draw order: borrowers SYN-0001 through SYN-0024 in ascending id. Per
  borrower the uniform draws are consumed in exactly this order: lender_type,
  structure, pre_revenue, last_round, arr, net_burn, cash_coverage_months,
  monthly_growth, gross_margin, warrant_coverage, spread, closing_fee, rbf_cap,
  io_months. Strata slot overrides (phase 2) never skip a draw: the uniform is
  consumed and ignored, so the stream stays aligned across the whole book.
- No wall clock, no os.urandom, no uuid, no hash-order dependence (never
  iterate a set when emitting output; sort or use insertion order).
- Byte-identity gate: two consecutive builds over payloads/ (excluding
  build_meta.json) diff clean.

## 12. Date conventions

- Monthly payment date k is add_months(close, k) then roll_forward. First
  payment is exactly one calendar month after close (rolled forward if needed).
- Day-of-month rule: preserve the close day-of-month; clamp to the target
  month's last day when the month is shorter (Jan 31 close gives Feb 28, Apr
  30, May 31, ... in 2026).
- Business days: Monday to Friday, no holiday table, disclosed on the methods
  page. Roll forward only, never backward. A target date already on a business
  day is unchanged.
- Payment dates must be strictly increasing; violation raises ValueError. Two
  consecutive rolled dates can never collide because consecutive monthly targets
  are at least 28 days apart.
- Close date must be a business day. Funding cash flow is dated at close.
- Interest accrual period t spans (payment date t-1, payment date t], with
  period 1 starting at close. Accrual uses year_fraction_act365 of the actual
  span.

## 13. IRR solver pins

- Definition: solve r in sum_i CF_i * (1 + r) ^ (-yf_i) = 0 where yf_i is the
  ACT/365 year fraction from close to cash-flow date i and CF_i are dated lender
  cash flows (close: fee minus facility; periods: interest and principal; exit:
  warrant value at the final payment date).
- Bisection on Decimal r with bracket [-0.99, 10.00], midpoint (lo + hi) / 2 in
  MONEY_CONTEXT, sign-based decisions only. Terminate when hi minus lo is below
  Decimal("0.000001") (0.0001 percent). Max 200 iterations; exceeding the limit
  is a hard failure (SolverError), never a silent fallback.
- Status no_solution: when the NPV has no sign change across the bracket (for
  example all-positive cash flows). The payload publishes status "no_solution"
  with a reason and no IRR value.
- Published IRR: r * 100 quantized once to scale 4 percent_annual, ROUND_HALF_UP.
- Verify recompute tolerance: absolute 0.0001 in percent units.
- Warrant value formula (stated assumption): warrant_value_cents =
  to_cents(coverage_fraction * facility_cents * warrant_valuation_factor),
  single ROUND_HALF_UP at the boundary, cash flow dated at the final payment
  date. Labeled a stated assumption, not a market quote, on every surface.

## 14. Sizing bisection pins

- Domain: whole integer cents, lo feasible and hi infeasible, invariant held
  every step. Start lo = 0; hi = band cap in cents (50 percent of last round).
  If hi itself is feasible, the answer is hi and the binding constraint is
  "band_cap". If lo is infeasible, status infeasible with the named constraint.
- Loop while hi minus lo > 1; midpoint (lo + hi) // 2. Answer is lo (largest
  proven-feasible cent). Max 60 iterations; exceeding is a hard failure that
  prints the bracket. The build log prints iteration count and final bracket.
- Feasibility predicate: post-draw runway (cash minus F over average monthly net
  burn) at or above the runway floor AND min DSCR across periods at or above the
  DSCR floor (equality is feasible). Monotone decreasing in F, so bisection is
  exact on the integer grid.
- Binding constraint reporting: "runway", "dscr", or "band_cap". Edge borrowers
  return status infeasible with the constraint named; a zero-sized facility is
  never presented as a solution.

## 15. Verification checklist and risks

- [x] Every component has one owner and one file.
- [x] Data flow has no dead ends (fixtures and config feed the engine; the gate
      reads artifacts only).
- [x] File structure matches the spec section 7 layout plus the flagged
      config.py addition.
- [x] All technology choices carry rationale and rejected alternatives.
- [x] Risks with mitigations:
      - Human arithmetic error in fixtures: arithmetic strings are mandatory and
        the independent gate recomputes naively in phase 5.
      - Float leakage: canonical writer raises on floats; ruff rule and tests.
      - Dash-scan self-violation: pattern literals use \u002d escapes; the
        scanner scans its own package.
      - Decimal power determinism in the IRR solver: sign-only bisection means
        precision noise near the root is absorbed by the 0.0001 percent publish
        tolerance and the 0.000001 bracket width.
      - Stream misalignment in the generator: strata overrides consume draws;
        the draw-order table is frozen and reviewed.
- [x] Phase 2 and 3 can start against this document with no open questions
      except Q1 to Q3 in the handoff notes.

## Phase 2 design

Appended 2026-11-11 (phase 2 of 6). Sections 1 to 15 above are binding and
unchanged. This section pins the committed rate snapshot, the seeded borrower
generator, pricing, bullet and IO-then-straight-line schedules, sizing, the
first canonical payload, the phase 2 test plan, and the task slices. RBF and
bridge schedules, returns analytics, and IRR are phase 3; covenants and
scenarios are phase 4; the full verify gate is phase 5.

### P2.1 Rates module

File: pipeline/src/vdebt/rates.py. It reads committed Bank of Canada Valet JSON
under pipeline/data/rates/ and never touches the network.

Committed data:
- pipeline/data/rates/BTB3M.json: the raw Valet response body, committed
  verbatim as received (UTF-8).
- pipeline/data/rates/SHA256SUMS: one line per committed JSON file, sorted by
  file name, format "64 lowercase hex characters, two ASCII spaces, file name",
  LF line endings, listing exactly the *.json files in the directory and
  nothing else.

Series selection:
- Primary: BTB3M, the 3-month Treasury bill rate. Rationale: a 3-month bill is
  the standard short-rate proxy for floating loan pricing, its tenor matches
  the monthly accrual reset, and it is a published government rate rather than
  a bank quote. This matches the spec section 9 rebasing decision (short rate
  plus spread, restatement disclosed on the methods page).
- Fallback: BTB6M, the 6-month bill. Same series family and response shape. It
  activates only by changing rates.series in scenario.toml with a
  docs/decisions.md record; there is no runtime fallback, so CI behavior stays
  deterministic.
- Rejected: AVG.INTWO (CORRA). An overnight fixing is noisy for a monthly
  accruing term facility and would move the published base rate day to day
  without adding realism to a synthetic book.

Series label assertion (spec section 9 and the section 13 CI gate):
- Expected label patterns are committed as regexes in scenario.toml [rates]
  (TOML literal strings, single quoted). Patterns are hyphen-free by
  construction: bridge word gaps with .{0,N} classes so the dash scan passes.
- assert_series_label(series, label, cfg) compiles the configured pattern and
  raises SeriesLabelError whose message prints the series code, the expected
  pattern, and the actual label verbatim. It runs in two places: at engine load
  against the label in the committed JSON (the startup assertion from the
  section 3 decisions log), and in the refresh script against the live
  series-detail response before anything is written.
- The reader also asserts the response seriesDetail key equals the requested
  series code. A mismatch is the same loud failure.
- The patterns are best-effort until the first refresh confirms the live label.
  If the live label differs, the run fails printing the actual label and the
  pattern is tightened in a reviewed commit with a decisions entry.

Reader interface and semantics:

```python
@dataclass(frozen=True)
class RateSnapshot:
    series: str
    label: str
    observation_date: date
    base_rate: Decimal          # percent_annual, scale 4
    source: str                 # "Bank of Canada Valet"
    source_timestamp: str       # ISO date of the observation
    manifest_hashes: dict[str, str]

class RateError(Exception): ...
class SeriesLabelError(RateError): ...

def read_manifest(rates_dir: Path) -> dict[str, str]
def verify_manifest(rates_dir: Path) -> dict[str, str]
def assert_series_label(series: str, label: str, cfg: RatesConfig) -> None
def load_rate_snapshot(rates_dir: Path, cfg: RatesConfig) -> RateSnapshot
```

- verify_manifest raises RateError unless the manifest lists exactly the
  committed JSON files and every digest matches (checked in both directions:
  listed but missing fails, present but unlisted fails).
- The base observation is the last observation in the response with a non-null
  value. Its value is parsed with Decimal and quantized once to percent_annual
  scale 4 (for example "2.74" becomes "2.7400").
- observation_date must be a business day; it is the book close date and the
  asof (P2.6). The published base-rate envelope uses the external provenance
  class with source "Bank of Canada Valet" and source_timestamp set to this
  date.

Manual refresh workflow (the only network code in the repository):
- pipeline/scripts/refresh_rates.py, standard library argparse plus
  urllib.request plus json plus hashlib. Steps in order: (1) GET the
  series-detail endpoint for the chosen series and assert the label via the
  same assertion as the reader; on mismatch print the actual label and exit
  non-zero with nothing written; (2) GET the observations document with a
  recent window; (3) write the raw bytes to pipeline/data/rates/<CODE>.json;
  (4) regenerate SHA256SUMS (sorted, two spaces, LF); (5) print the old and
  new digests and the observation date.
- The refresh workflow file .github/workflows/refresh-rates.yml invokes it on
  workflow_dispatch only and opens a pull request. It never runs on push and
  never runs in a blocking gate. A test asserts that no file under
  pipeline/src/ imports urllib or any network module and that the only urllib
  reference under pipeline/ is refresh_rates.py.

### P2.2 Generator module

File: pipeline/src/vdebt/generator.py.

Borrower book: SYN-0001 through SYN-0024 in ascending id order. One instance
of random.Random(20261110) from scenario.toml [meta]. Exactly 14 calls of
.random() per borrower, consumed in the frozen section 11 order:
lender_type, structure, pre_revenue, last_round, arr, net_burn,
cash_coverage_months, monthly_growth, gross_margin, warrant_coverage, spread,
closing_fee, rbf_cap, io_months. That is 336 uniforms for the book. Every
uniform is consumed even when a strata slot overrides the resulting value: the
override replaces the computed value after the draw, never the draw itself.

Draw mapping (target unit is the storage boundary; every continuous draw is
x = lo + Decimal(repr(u)) * (hi - lo) in MONEY_CONTEXT with lo and hi in the
target unit, quantized exactly once at the storage boundary):

| # | Draw | Kind | Bounds source | Stored as |
|---|------|------|---------------|-----------|
| 1 | lender_type | discrete | [generator.weights.lender_type] | label "bank" or "non_bank" |
| 2 | structure | discrete | [generator.weights.structure] | label "bridge", "bullet", "io_then_straight_line", "rbf_capped" |
| 3 | pre_revenue | discrete | [generator.weights.pre_revenue] | bool from label "no" or "yes" |
| 4 | last_round | continuous | [bands.last_round] 10 to 400 million USD | integer cents |
| 5 | arr | continuous | [bands.arr] 2 to 150 million USD | integer cents |
| 6 | net_burn | continuous | [bands.net_burn] 0.5 to 8 million USD per month | integer cents |
| 7 | cash_coverage_months | continuous | [bands.cash_coverage_months] 15 to 150 | Decimal months, scale 1 |
| 8 | monthly_growth | continuous | [bands.revenue_growth_monthly] 0.5 to 3.0 | Decimal percent, scale 4 |
| 9 | gross_margin | continuous | [bands.gross_margin] 50 to 80 | Decimal percent, scale 2 |
| 10 | warrant_coverage | continuous | [bands.warrant_coverage], bank 0 to 5, non-bank 10 to 20 | Decimal percent, scale 2 |
| 11 | spread | continuous | [bands.pricing_bank] 1 to 3 or [bands.pricing_nonbank] 3 to 4 | Decimal percent_annual, scale 4 |
| 12 | closing_fee | continuous | [bands.closing_fee] 0.5 to 2 | Decimal percent, scale 2 |
| 13 | rbf_cap | continuous | [bands.rbf_cap] 1.3 to 2.5 | Decimal multiple, scale 2 |
| 14 | io_months | continuous | [bands.interest_only] 12 to 18 | integer months (draw, quantize to scale 0, clamp into 12 to 18) |

Discrete draws: u = rng.random(); labels sorted by label text ("bank" before
"non_bank"; "bridge", "bullet", "io_then_straight_line", "rbf_capped"; "no"
before "yes"); cumulative thresholds from the pinned weights computed in
Decimal; the chosen label is the first whose cumulative exceeds
Decimal(repr(u)). The loader asserts the weights sum to exactly 1.

Derived values (never drawn):
- cash_cents = quantize(Decimal(net_burn_cents) * cash_coverage_months, 0),
  ROUND_HALF_UP once.
- term_months = io_months + 36 for bullet and io_then_straight_line;
  term_months = 24 for rbf_capped (the pinned horizon); term_months = 3 for
  bridge (the pinned tenor). The 36 amortizing periods reproduce the research
  amortization example (4-year facility, 12 months IO, 36 straight-line) and
  keep term inside the 36 to 60 band at any io_months.
- Revenue path (per-period monthly revenue) for t = 1 to term_months:
  base = arr_cents / 12 at MONEY_CONTEXT; revenue_t = quantize(base *
  (1 + monthly_growth)^(t-1), 0), ROUND_HALF_UP once per period. For
  pre_revenue borrowers the path is all zero cents.
- CFADS path: cfads_t = quantize(Decimal(revenue_t) * gross_margin / 100, 0),
  ROUND_HALF_UP once per period (F1 step 4 basis: quantized revenue in,
  cents out).

scenario.toml phase 2 additions (decimal values quoted strings; loader rejects
floats as before):

```toml
[rates]
series = "BTB3M"
fallback_series = "BTB6M"
label_pattern = '(?i)bill.{0,40}3.{0,8}month|3.{0,8}month.{0,40}bill'
fallback_label_pattern = '(?i)bill.{0,40}6.{0,8}month|6.{0,8}month.{0,40}bill'

[generator.weights.lender_type]
bank = "0.45"
non_bank = "0.55"

[generator.weights.structure]
bridge = "0.15"
bullet = "0.25"
io_then_straight_line = "0.35"
rbf_capped = "0.25"

[generator.weights.pre_revenue]
no = "0.90"
yes = "0.10"

[generator]
term_amortizing_periods = 36
rbf_horizon_periods = 24
rbf_remittance_rate_pct = "5.00"
bridge_tenor_months = 3

[bands.last_round]
min_usd = "10000000"
max_usd = "400000000"
unit = "usd_minor_units"
scale = 0
band = "generator_last_round_10_400_million_usd"
citation = "Venture Debt Engine synthetic generator band per spec section 10.1 (accessed 2026-11-11)"

[bands.arr]
min_usd = "2000000"
max_usd = "150000000"
unit = "usd_minor_units"
scale = 0
band = "generator_arr_2_150_million_usd"
citation = "Venture Debt Engine synthetic generator band per spec section 10.1 (accessed 2026-11-11)"

[bands.net_burn]
min_usd = "500000"
max_usd = "8000000"
unit = "usd_minor_units"
scale = 0
band = "generator_net_burn_0_5_8_million_usd_monthly"
citation = "Venture Debt Engine synthetic generator band per spec section 10.1 (accessed 2026-11-11)"

[bands.cash_coverage_months]
min_months = "15.0"
max_months = "150.0"
unit = "months"
scale = 1
band = "generator_cash_coverage_15_150_months_of_burn"
citation = "Venture Debt Engine synthetic generator band per spec section 10.1 (accessed 2026-11-11)"
```

Strata slot overrides. Slots are SYN-0001 to SYN-0006, pinned in
[generator.strata_slots] as (borrower_id, stratum, overrides) recipes. The
mechanics: (1) all 14 uniforms are drawn and the raw values computed; (2) the
slot's override map replaces named values; (3) derived values (cash, term,
paths) are computed from the final values; (4) every final value is asserted
inside its band, with one documented exception: arr = 0 is legal exactly when
pre_revenue is yes (the spec section 10.1 pre-revenue share). Overrides that
touch lender_type must override the full consistent set (warrant_coverage and
spread are re-pinned to band-edge values in both lender bands where needed).
The draw stream is never touched, so the book stays aligned.

| Slot | Stratum | Overrides (all band-legal) |
|------|---------|----------------------------|
| SYN-0001 | pre_revenue | pre_revenue = yes, arr = 0 |
| SYN-0002 | zero_warrant_bank | lender_type = bank, warrant_coverage_pct = "0.00", spread_pct = "3.0000" (edge value legal in both lender bands) |
| SYN-0003 | infeasible_at_zero_draw | cash_coverage_months = "15.0" (zero-draw runway 15.0 is strictly below the 18.0 floor, so infeasibility is structural) |
| SYN-0004 | runway_bound | last_round = 400,000,000.00 USD, net_burn = 500,000.00 USD, cash_coverage_months = "20.0", arr = 150,000,000.00 USD, gross_margin_pct = "80.00", monthly_growth_pct = "0.5000" (runway bound is far below the 200,000,000.00 band cap while CFADS gives DSCR huge slack) |
| SYN-0005 | dscr_bound | last_round = 10,000,000.00 USD, arr = 2,000,000.00 USD, gross_margin_pct = "50.00", monthly_growth_pct = "0.5000", cash_coverage_months = "150.0" (small CFADS puts the DSCR bound near 2,000,000.00 USD at any allowed all-in rate, below the 5,000,000.00 cap and far below the runway bound) |
| SYN-0006 | rbf_shortfall | structure = rbf_capped, rbf_cap = "2.50", arr = 2,000,000.00 USD, monthly_growth_pct = "0.5000", cash_coverage_months = "150.0" (projected remittance 5.00 percent of revenue over 24 periods is far below 2.50x any feasible advance) |

Strata manifest (emitted in the payload and asserted by the build):
- pre_revenue: arr_cents == 0.
- zero_warrant_bank: lender_type == bank and warrant_coverage_pct == 0.
- infeasible_at_zero_draw: sizing status "infeasible".
- runway_bound: sizing status "feasible" and binding_constraint "runway".
- dscr_bound: sizing status "feasible" and binding_constraint "dscr".
- rbf_shortfall: structure == rbf_capped and sum of revenue over the first
  rbf_horizon_periods times rbf_remittance_rate_pct is strictly below
  rbf_cap_multiple times the sized facility cents (the sized facility is the
  disclosed phase 2 advance proxy; the predicate migrates to the real RBF
  schedule in phase 3 with a decisions entry).
- Strata are independent; one borrower may count in several. The manifest
  publishes counts and member ids per stratum (member ids sorted). The build
  exits non-zero if any required stratum count is zero.

Book hash: book_sha256 is the sha256 hex digest of canonical_dumps over the
generator's direct output document (borrower inputs and paths only, before
pricing, sizing, and payload envelopes), so a rate refresh does not invalidate
it. The expected value is committed in
pipeline/fixtures/generator/book_sha256.json by the tester after the generator
lands. This file is explicitly exempt from the golden-fixture-first rule: it is
an RNG drift fingerprint derived from engine output by design, and changing it
requires a docs/decisions.md entry. Any RNG or mapping drift fails the build
loudly with old and new digests printed.

### P2.3 Pricing module

File: pipeline/src/vdebt/pricing.py.

- all_in_rate = base_rate + spread_pct, both percent_annual scale 4, summed in
  Decimal and quantized exactly once at the storage boundary. The sum is exact
  at scale 4 (F2: "4.0000" + "4.0000" = "8.0000").
- Rate shock: shocked_all_in_rate = all_in_rate + rate_shock_bp / 100 with
  rate_shock_bp = 200 from [scenario]. The shocked rate feeds shocked schedules
  and sensitivity in phase 4; phase 2 publishes the shocked all-in alongside
  the base all-in.
- Band excursion check: compares the base all-in against the total-rate columns
  of the lender-type pricing band (5.0000 to 8.0000 bank, 10.0000 to 14.0000
  non-bank), inclusive. Result is one of "within_band" or "excursion",
  published as a status envelope per borrower and printed in the build log. It
  is report-only and never fails the build (spec section 10.4: the check flags
  and reports, with the short-rate restatement disclosed on the methods page).
  The shocked all-in is a scenario figure and is excluded from the excursion
  check.
- Every rate figure published anywhere carries its base rate source and
  source_timestamp from the RateSnapshot (external provenance). A test asserts
  no rate envelope exists without them.

```python
@dataclass(frozen=True)
class Pricing:
    base_rate: Decimal
    spread_pct: Decimal
    all_in_rate: Decimal
    shocked_all_in_rate: Decimal
    band_excursion: str         # "within_band" | "excursion"

def all_in_rate(base_rate: Decimal, spread_pct: Decimal) -> Decimal
def shocked_rate(all_in_rate: Decimal, shock_bp: int) -> Decimal
def check_excursion(all_in_rate: Decimal, lender_type: str, cfg: ScenarioConfig) -> str
def price(spread_pct: Decimal, snapshot: RateSnapshot, cfg: ScenarioConfig) -> Pricing
```

### P2.4 Schedule module

File: pipeline/src/vdebt/schedule.py. Phase 2 implements exactly two branches:
"bullet" and "io_then_straight_line" (spec section 10.3 bullet and
IO-then-straight-line only). Any other structure raises NotImplementedError;
RBF and bridge land in phase 3.

Row model and rules:
- Rows carry period index, payment date, opening balance, interest, principal,
  closing balance, all money in integer cents.
- Payment dates come from dates.payment_dates(close, term_months): strictly
  increasing, roll forward only, close must be a business day (asserted).
- Interest accrues on the opening balance of the period:
  interest_t = quantize(all_in_rate / 100 * opening_t * yf_t, 0) once, where
  yf_t is the ACT/365 fraction of the actual span (period 1 spans
  (close, payment 1]). This is the F1 step 10 basis exactly.
- Principal settles at the payment date. Bullet: principal 0 for all periods
  except the final period which repays the full facility. IO-then-straight-line:
  principal 0 for the first io_months periods, then
  allocate_cents(facility, term_months - io_months) over the amortizing
  periods in order (early periods receive the extra cents, the F1 step 9
  distribution).
- Assertions at build time: sum of principal equals the facility, final closing
  balance is 0, closing balances never negative, dates strictly increasing.

Helpers kept here so fixtures F2 and F3 recompute end to end from engine code:

```python
@dataclass(frozen=True)
class ScheduleRow:
    period: int
    payment_date: date
    opening_cents: int
    interest_cents: int
    principal_cents: int
    closing_cents: int

@dataclass(frozen=True)
class Schedule:
    structure: str
    facility_cents: int
    close: date
    all_in_rate: Decimal
    rows: tuple[ScheduleRow, ...]
    total_interest_cents: int
    total_principal_cents: int

def build_schedule(structure: str, facility_cents: int, close: date,
                   term_months: int, io_months: int,
                   all_in_rate: Decimal) -> Schedule
def closing_fee_cents(facility_cents: int, closing_fee_pct: Decimal) -> int
def moic(total_interest_cents: int, total_principal_cents: int,
         warrant_value_cents: int, facility_cents: int) -> Decimal
```

- closing_fee_cents = quantize(facility * fee_pct / 100, 0) once (F2 expects
  1,000,000 cents at 1.00 percent of 100,000,000).
- moic follows spec section 5 literally: (interest + principal + warrant value)
  / facility, quantized once to multiple scale 2. Warrant value is 0 here;
  the warrant model is phase 3. The MOIC asymmetry (fee excluded) is the
  recorded phase 1 decision.

Schedules feed sizing inside the bisection loop: the feasibility predicate
builds the canonical schedule for each candidate F (P2.5) through
build_schedule and reads its quantized rows. There is no analytic shortcut and
no schedule caching across iterations: fixture F1 steps 21 to 23 prove the
analytic linear bound is 8 cents too high once row quantization binds, so every
candidate gets freshly quantized rows. At 60 iterations times at most 54 rows
the cost is trivial.

### P2.5 Sizing module

File: pipeline/src/vdebt/sizing.py. Every borrower is sized on the canonical
io-then-straight-line serviceability schedule (borrower io_months, term =
io_months + 36). Bullet balloons would degenerate min DSCR (the balloon period
repays the whole facility, making DSCR far below the floor for almost every
book), and one basis keeps the 24 capacities directly comparable.

```python
@dataclass(frozen=True)
class SizingInputs:
    cash_cents: int
    net_burn_cents: int          # monthly
    cfads_cents: tuple[int, ...] # per-period, from the generator path
    close: date
    term_months: int
    io_months: int
    all_in_rate: Decimal
    band_cap_cents: int          # percent of last_round quantized to cents

@dataclass(frozen=True)
class SizingResult:
    status: str                  # "feasible" | "infeasible"
    facility_cents: int | None
    binding_constraint: str | None   # "runway" | "dscr" | "band_cap"
    post_draw_runway_months: Decimal | None   # scale 1
    min_dscr: Decimal | None           # scale 4
    iterations: int
    final_lo: int
    final_hi: int

def size_facility(inputs: SizingInputs, cfg: ScenarioConfig) -> SizingResult
```

Algorithm, matching section 14 exactly:
- Initialize lo = 0, hi = band_cap_cents. Evaluate feasibility at lo and hi.
  If lo is infeasible, return status "infeasible" with the named constraint
  (the first failing constraint in the order runway, dscr) and no facility
  value. If hi is feasible, return hi with binding_constraint "band_cap".
- Otherwise loop while hi minus lo > 1: mid = (lo + hi) // 2; feasible sets
  lo = mid, else hi = mid. Exceeding 60 iterations raises SolverError printing
  the current bracket (hard failure, never a silent fallback). The integer grid
  halves 2e10 to 1 in about 35 steps, so 60 is a safe ceiling.
- Feasibility predicate at candidate F: rebuild the canonical schedule at F,
  compute DSCR_t = Decimal(cfads_t) / Decimal(interest_t + principal_t) per
  period on the quantized row cents but compared unquantized against the
  DSCR floor (equality feasible), and post_draw_runway = (cash - F) /
  net_burn compared unquantized against the runway floor (equality feasible).
  F1 step 23 pins this basis: the quantized rows are binding, the ratio is not
  quantized before comparison. Pre-revenue borrowers (all cfads zero) skip the
  DSCR constraint entirely (DSCR publishes not_applicable with a reason in the
  payload; the predicate reduces to runway only).
- Answer is lo (largest proven-feasible cent). Binding constraint at the
  answer: if lo == band_cap_cents then "band_cap"; else evaluate the predicate
  at lo + 1 and name the first failing constraint in the order runway, dscr
  (F1 step 14 logic).
- Published values quantized once at their boundary: facility as integer cents,
  post_draw_runway_months scale 1, min_dscr scale 4 (the F1 expected basis).
- Build log line per borrower:
  "sizing SYN-000K iterations=N bracket=[lo hi] answer=A binding=B".

F1 mapping (fixture cases drive the acceptance tests directly):
- F1a runway_bound: hi = 1,000,000,000 infeasible (runway 15.0 < 18.0),
  lo = 0 feasible; bisection converges to 700,000,000; at 700,000,001 the
  runway fails while DSCR holds, so binding = "runway".
- F1b dscr_bound: same bracket setup; the quantized-row bisection must land on
  418,394,934, exactly 8 cents below the analytic linear bound
  (418,394,942), because allocate_cents remainder lifts period 13 principal to
  the ceiling of F/18. Binding = "dscr" (DSCR fails at +1 cent, runway holds).
- F1c infeasible: lo = 0 fails the runway floor (1.5 < 18.0), so status
  "infeasible", binding_constraint "runway", no facility value key.
- The engine must reproduce these three outcomes to the cent from engine code
  alone (test in P2.7). The fixture arithmetic steps 21 to 24 are the normative
  description of the quantize-inside-the-loop requirement.

### P2.6 Payloads and build

First canonical payload writer usage: build.run_build orchestrates
rates.load_rate_snapshot, generator.generate_book, pricing, sizing, schedules,
and writes the result through snapshot.write_payload, which returns the
sha256 of the written bytes.

Layout decision: ONE aggregate file per asof, not one file per borrower.
- payloads/<asof>/book.json: the single canonical document (see shape below).
- payloads/build_meta.json: wall clock, git SHA, Python version; lives outside
  the asof directory, excluded from byte identity by definition (it contains
  the wall clock).
- Rationale: the borrower book is one deterministic document with one
  canonical hash (the book hash fixture matches exactly one artifact); the
  byte-identity diff is trivial (one file, one digest); the site loads the
  whole book once anyway (the borrower selector filters client side per spec
  section 12); per-borrower files would multiply SHA256 bookkeeping and give
  cross-document keys (strata manifest, rate metadata) no clean home.
  Trade-off: no per-borrower caching and one large file (a few hundred KB at
  24 borrowers with 54-period schedules); acceptable at this scale.

asof derivation: the asof directory name and every envelope asof value equal
the observation_date of the RateSnapshot, which is the date of the committed
base-rate observation used at close (the source_timestamp of the external
rate envelope). Never the wall clock. Fixtures pin their own asof and are
untouched.

book.json shape (all numeric leaves are metric envelopes per section 6.4,
keys sorted by canonical_dumps):

```text
{
  "meta": { generator_version, seed, currency, rounding_mode, book_sha256,
            strata_manifest: { stratum: {count, members} } },
  "rates": { series, label, observation_date, base_rate_envelope,
             source, source_timestamp, manifest_hashes },
  "borrowers": [
    { "borrower_id", "lender_type", "structure", "pre_revenue",
      "inputs": { last_round, arr, net_burn, cash, cash_coverage_months,
                  monthly_growth, gross_margin, warrant_coverage, spread,
                  closing_fee, rbf_cap, io_months, term_months },
      "paths": { revenue_cents: [...], cfads_cents: [...] },
      "pricing": { base_rate, spread_pct, all_in_rate,
                   shocked_all_in_rate, band_excursion },
      "sizing": { status, facility_cents, binding_constraint,
                  post_draw_runway_months, min_dscr },
      "schedule": { rows: [...] } | { status: "not_applicable",
                   reason: "structure schedule engine lands in phase 3" }
    }, ...
  ]
}
```

- Schedules appear for bullet and io_then_straight_line borrowers only;
  rbf_capped and bridge borrowers publish the structure schedule with status
  "not_applicable" and the reason above (they are still sized on the canonical
  schedule and still carry pricing and paths).
- Band values (any leaf sourced from a rule-of-thumb band) carry citation and
  band per the section 6.4 matrix. Generated inputs carry the "synthetic
  seed=20261110 generator v1" provenance. The base rate carries external
  provenance.
- write_payload is called once per build for book.json (plus build_meta.json
  written with plain json outside the envelope rules, since it is not a
  published payload surface).

Byte-identity test (kill gate): run the full build twice into clean output
snapshots (subprocess `python -m vdebt build` with an output directory
argument), then diff the two payloads/ trees recursively excluding
build_meta.json. Assert: identical file lists, identical bytes per file,
identical book_sha256 across runs, and the same asof directory name. Also
assert the committed book_sha256 fixture value matches the generated book.
Any drift prints both digests and the first differing byte offset. The build
must run with no environment or locale sensitivity; PYTHONHASHSEED does not
matter because no hash iteration feeds output (insertion or sorted order only,
per section 11).

CLI: `python -m vdebt build` (new subcommand beside `verify`) runs
build.run_build and prints the build log: base rate and asof, per-borrower
sizing lines (iterations, bracket, answer, binding constraint), the excursion
report, and the strata manifest counts. Exit 0 on success; non-zero on any
hard failure (manifest mismatch, label mismatch, stratum count zero, solver
non-convergence, schema violation).

### P2.7 Test plan for phase 2

Existing suites extended (61 tests remain green throughout):
- test_config.py: [rates] and [generator] tables parse; float rejection still
  fires on the new decimal keys; band citation presence for the four new
  bands; weight-sum assertion; slot override band validation raises on an
  out-of-band override.
- test_snapshot.py: generated-provenance envelopes from the book pass
  validate_payload (regression guard for the new payload shape).
- test_fixtures.py and test_verify.py: untouched (fixture envelope rules
  unchanged; the generator book hash file lives outside pipeline/fixtures/golden
  so the F1 to F6 enumeration is unaffected).

New suites:
- test_rates.py (tester): manifest verification happy path on the committed
  data; tampered byte fails; missing manifest line fails; unlisted file fails;
  label assertion passes on the committed label and fails with the actual
  label printed (assert the message text); series code mismatch fails; base
  rate parsing and scale-4 quantize; asof equals the observation date and is a
  business day; the no-network confinement test (only refresh_rates.py
  references urllib anywhere under pipeline/).
- test_generator.py (tester): determinism (two generate_book calls
  identical); draw accounting (exactly 336 uniforms consumed, 14 per
  borrower, order recorded via a spy Random); every emitted value inside its
  band post-override; pre_revenue borrower with ARR 0 exists; zero-warrant
  bank borrower exists; revenue path formula spot-check (ARR/12 grown, one
  quantize per period); CFADS = revenue x margin spot-check; cash = coverage
  x burn; book_sha256 equals the committed fixture value (fails loudly with
  both digests on drift); strata manifest counts at least one in all six
  strata; member lists sorted.
- test_pricing.py (tester): all_in equals base plus spread exactly at scale 4;
  excursion check passes at 5.0000 and 8.0000 and flags 4.9999 and 8.0001
  (bank), same at the 10 to 14 non-bank edges; shocked rate equals base plus
  2.0000 plus spread; every rate envelope carries source and source_timestamp.
- test_schedule.py (tester): F2 recomputed from engine code (bullet, 4
  periods) matches fixture expected to the cent including dates, rows,
  totals, closing fee, moic, and the warrant not_granted companion; F3
  recomputed from engine code (IO-then-straight-line, 6 periods, 3 amortizing)
  same, including the allocate_cents distribution; dates strictly increasing;
  final closing balance 0; interest formula spot-check against a hand row.
- test_sizing.py (tester): F1a, F1b, F1c from engine code to the cent
  (facility, binding_constraint, post_draw_runway_months, min_dscr, and the
  infeasible companion); iteration and bracket fields populated; 60-iteration
  hard failure via a monkeypatched predicate; band_cap binding via a synthetic
  feasible-hi case; pre_revenue predicate reduces to runway only.
- test_build.py (tester): the byte-identity kill gate (two subprocess builds,
  tree diff excluding build_meta.json); build log contains per-borrower
  sizing lines; a planted zero-count stratum fails the build (monkeypatched
  manifest).

Dash-scan hygiene: every new file passes the section 9 scan; regex patterns in
scenario.toml are hyphen-free; the refresh script's URL strings contain no
double hyphen; no command-line flags appear in docs beyond flag tokens.

### P2.8 Task breakdown (sequential slices)

| # | Slice | Owner | Depends on |
|---|-------|-------|-----------|
| 1 | scenario.toml [rates], [generator], four bands; config.py extension + tests | backend-dev | none |
| 2 | rates.py reader (manifest, label assert, snapshot) + test_rates.py | backend-dev, tester | 1 |
| 3 | refresh_rates.py + refresh-rates.yml; run the first manual refresh, commit BTB3M.json and SHA256SUMS, tighten label pattern if the live label differs (decision entry) | backend-dev (data via 10-git) | 2 |
| 4 | generator.py (draw order, overrides, paths, strata manifest, book hash) + test_generator.py; commit book_sha256.json after review | backend-dev, tester | 1 |
| 5 | pricing.py + test_pricing.py | backend-dev, tester | 2 |
| 6 | schedule.py bullet and IO-then-straight-line + fee/moic helpers + F2/F3 recompute tests | backend-dev, tester | none (fixtures F2/F3 exist) |
| 7 | sizing.py + F1 recompute tests (kill-gate half one) | backend-dev, tester | 6 |
| 8 | build.py, CLI `build`, book.json assembly, build log, build_meta.json | backend-dev | 2, 4, 5, 7 |
| 9 | test_build.py byte-identity (kill-gate half two) + strata manifest gate + excursion reporting in the log | tester | 8 |
| 10 | Kill-gate run: full suite green, verify gate green, two-build diff clean, decisions.md updated, phase commit | orchestrator, 10-git | 9 |

Each slice is one sitting. Slices 2 and 6 are independent and can run in
parallel; everything else is strictly ordered. Fixtures F1 to F6 are never
regenerated; the book hash fixture (slice 4) is the single engine-derived
committed value and carries its own decision record.
