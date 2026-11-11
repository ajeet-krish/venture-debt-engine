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
