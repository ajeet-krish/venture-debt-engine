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
- pipeline/data/rates/TB.CDN.90D.MID.json: the raw Valet response body, committed
  verbatim as received (UTF-8).
- pipeline/data/rates/SHA256SUMS: one line per committed JSON file, sorted by
  file name, format "64 lowercase hex characters, two ASCII spaces, file name",
  LF line endings, listing exactly the *.json files in the directory and
  nothing else.

Series selection:
- Primary: TB.CDN.90D.MID, the 3-month Treasury bill rate (label "Treasury
  bills, 3-month"). Rationale: a 3-month bill is
  the standard short-rate proxy for floating loan pricing, its tenor matches
  the monthly accrual reset, and it is a published government rate rather than
  a bank quote. This matches the spec section 9 rebasing decision (short rate
  plus spread, restatement disclosed on the methods page).
- Fallback: TB.CDN.180D.MID, the 6-month bill (label "Treasury bills,
  6-month"). Same series family and response shape. It
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
  section 3 decisions log), and in the refresh script against the live label
  before anything is written.
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
- The committed document is the observations endpoint response: GET
  /observations/<CODE>/json?recent=90 returns seriesDetail (keyed by the
  series code) plus an observations list whose entries nest the value under
  the requested code, for example {"d": "2026-11-10", "TB.CDN.90D.MID":
  {"v": "2.61"}}, in descending date order. The reader also accepts the flat
  {"d": ..., "v": ...} shape, so both stay readable.
- The base observation is the entry with the LATEST date among those whose
  value is non-null (lexicographic compare on the ISO date), never merely the
  last list entry, so a descending response cannot misorder the base. Its
  value is parsed with Decimal and quantized once to percent_annual
  scale 4 (for example "2.74" becomes "2.7400").
- observation_date must be a business day; it is the book close date and the
  asof (P2.6). The published base-rate envelope uses the external provenance
  class with source "Bank of Canada Valet" and source_timestamp set to this
  date.

Manual refresh workflow (the only network code in the repository):
- pipeline/scripts/refresh_rates.py, standard library argparse plus
  urllib.request plus json plus hashlib. Steps in order: (1) GET the
  observations endpoint /observations/<CODE>/json?recent=90 and assert the
  label from its seriesDetail via the same assertion as the reader; on
  mismatch print the actual label and exit non-zero with nothing written
  (a second GET of the series-detail endpoint is used only when the
  observations document lacks seriesDetail); (2) parse the observations,
  taking the latest non-null date and value; (3) write the raw bytes to
  pipeline/data/rates/<CODE>.json; (4) regenerate SHA256SUMS (sorted, two
  spaces, LF); (5) print the old and new digests and the observation date.
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
series = "TB.CDN.90D.MID"
fallback_series = "TB.CDN.180D.MID"
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
min_usd = "1000000000"
max_usd = "40000000000"
unit = "usd_minor_units"
scale = 0
band = "generator_last_round_10_400_million_usd"
citation = "Venture Debt Engine synthetic generator band per spec section 10.1 (accessed 2026-11-11)"

[bands.arr]
min_usd = "200000000"
max_usd = "15000000000"
unit = "usd_minor_units"
scale = 0
band = "generator_arr_2_150_million_usd"
citation = "Venture Debt Engine synthetic generator band per spec section 10.1 (accessed 2026-11-11)"

[bands.net_burn]
min_usd = "50000000"
max_usd = "800000000"
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
| SYN-0004 | runway_bound | last_round = 40,000,000,000 cents, net_burn = 50,000,000 cents, cash_coverage_months = "20.0", arr = 15,000,000,000 cents, gross_margin_pct = "80.00", monthly_growth_pct = "0.5000" (runway bound is far below the 200,000,000.00 band cap while CFADS gives DSCR huge slack) |
| SYN-0005 | dscr_bound | last_round = 1,000,000,000 cents, arr = 200,000,000 cents, gross_margin_pct = "50.00", monthly_growth_pct = "0.5000", cash_coverage_months = "150.0" (small CFADS puts the DSCR bound near 2,000,000.00 USD at any allowed all-in rate, below the 5,000,000.00 cap and far below the runway bound) |
| SYN-0006 | rbf_shortfall | structure = rbf_capped, rbf_cap = "2.50", arr = 200,000,000 cents, monthly_growth_pct = "0.5000", cash_coverage_months = "150.0" (projected remittance 5.00 percent of revenue over 24 periods is far below 2.50x any feasible advance) |

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
def price(spread_pct: Decimal, snapshot: RateSnapshot, cfg: ScenarioConfig,
          lender_type: str) -> Pricing
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
| 3 | refresh_rates.py + refresh-rates.yml; run the first manual refresh, commit TB.CDN.90D.MID.json and SHA256SUMS, tighten label pattern if the live label differs (decision entry) | backend-dev (data via 10-git) | 2 |
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

## Phase 3 design

Appended 2026-11-11 (phase 3 of 6). Sections 1 to 15 and the Phase 2 design above are binding and unchanged. This section pins the RBF-with-cap and bridge schedule branches, the returns analytics (dated lender cash flows, IRR, MOIC, fee and warrant treatment), the fixed-factor warrant model, the structure comparison table, the payload extensions, the web site shell, the phase 3 test plan, and the ordered task slices. Covenants, downside and rate-shock payload publication, the book dashboard, and the methods and returns pages are phase 4; the full verify gate and CI blocking gates are phase 5. Fixtures F1 to F6 are never regenerated.

### P3.0 Component map and data flow

| Component | Responsibility | File |
|-----------|---------------|------|
| RBF schedule branch | cap, remittance rows, shortfall residual | pipeline/src/vdebt/schedule.py |
| Bridge schedule branch | simple interest ACT/365, single repayment at maturity | pipeline/src/vdebt/schedule.py |
| Returns analytics | warrant value, dated lender cash flows, IRR bisection, MOIC | pipeline/src/vdebt/returns.py |
| Structure comparison | three rows on identical borrower inputs | pipeline/src/vdebt/compare.py |
| Payload assembly | schedule kinds, returns blocks, comparison.json | pipeline/src/vdebt/build.py |
| Site shell | index, sizing, structures, placeholders, charts | web/ |

Data flow additions to section 4: the generator revenue path and the sized facility feed the structure schedules (RBF consumes the per-period revenue path, bridge consumes the pinned tenor and the priced all-in rate); the schedules plus the fee and warrant inputs feed returns.py, which emits dated cash flows, IRR, MOIC, and warrant value; compare.py builds three schedules per borrower at the canonical facility and emits one comparison table per borrower; build.py writes book.json (now with returns blocks and real RBF and bridge schedules) and comparison.json; web/scripts/sync-payloads.js copies both payload files plus build_meta.json byte for byte into web/src/data at site build time; the site validates shape and formats values only. No path dead-ends: every published figure traces to a committed input (seed, bands, committed rate snapshot) or a fixture.

### P3.1 RBF schedule branch (fixture F4)

Mechanics, matching fixture F4 arithmetic steps exactly:
- Inputs: advance_cents, close, horizon (periods), remittance_rate_pct (5.00 from [generator] rbf_remittance_rate_pct), cap_multiple (the drawn rbf_cap, band 1.3 to 2.5), and the per-period revenue path in integer cents.
- cap_cents = quantize(cap_multiple x advance_cents, 0) once, ROUND_HALF_UP in MONEY_CONTEXT (F4 step 2: 1.50 x 60,000,000 = 90,000,000).
- Payment dates: dates.payment_dates(close, horizon), monthly from close, roll forward only (F4 steps 3 to 8).
- Per period t: nominal_t = quantize(remittance_rate_pct / 100 x revenue_t, 0) once (F4 step 9 basis); residual_before = cap_cents minus cumulative_{t-1}; remittance_t = min(nominal_t, residual_before); cumulative_t = cumulative_{t-1} + remittance_t.
- Stop rule: the row list ends at the first period whose cumulative reaches cap_cents (that period's row is included, its remittance is the exact residual when nominal exceeds it); after the cap there are no rows, because remittances stop at the cap (F4 step 13). When the horizon ends first, rows span the full horizon.
- Revenue feed: build_rbf_schedule takes revenue_cents as a parameter tuple, so fixtures feed their own arrays and build.py feeds borrower.revenue_path (which spans rbf_horizon_periods = 24 from the generator). Pre-revenue borrowers feed all-zero revenue: every remittance is a real 0 cents and the schedule ends in shortfall, never a fabricated number.
- Status: "feasible" when cumulative reaches the cap inside the horizon (F4a: final cumulative equals cap, residual 0); "shortfall" when the horizon ends first, with residual_cents = cap_cents minus final_cumulative_cents published both as the residual companion of the status envelope (section 6.4 matrix) and as a standalone residual leaf (F4 step 19).
- Row model extension: a separate RbfRow dataclass (period, payment_date, revenue_cents, remittance_cents, cumulative_cents) and RbfSchedule (advance, cap, rate, cap multiple, horizon, rows, final_cumulative_cents, residual_cents, status). The amortizing ScheduleRow is untouched and RBF rows carry NO interest, principal, or balance keys: F4 carries no interest term, so emitting fabricated zero interest and principal would violate the not_applicable principle. Rejected alternative: one union row model with optional fields, because optional money fields invite consumers to read absent as zero.
- Build assertions: dates strictly increasing; 0 <= remittance_t <= residual_before; cumulative never exceeds cap; if cumulative equals cap that row is last; sum of remittances equals final_cumulative exactly (integer sum of quantized rows, no further rounding).
- F4 recompute: test_schedule.py loads schedule_rbf.json and rebuilds both cases from engine code (advance 60,000,000, close 2026-05-01, rate "5.00", cap multiple "1.50", per-case horizon and revenue arrays), matching payment_dates, per-period rows, final_cumulative, residual, status plus the shortfall residual companion, and the warrant not_granted block by exact string or int equality.
- Strata predicate migration (pinned by P2.2 for phase 3): rbf_shortfall becomes structure == rbf_capped and build_rbf_schedule on the sized advance (the sized facility stays the disclosed advance proxy) returns status "shortfall". Membership counts may move from the phase 2 proxy (which counted 6); the build still fails on any zero count and the manifest republishes counts and sorted members. Decision entry recorded.
- Fixture scope note: F4 carries no fee and no warrant, so its expected surface has no fee or interest fields. That is fixture scope only; see P3.3 for the drawn closing fee and warrant in RBF lender cash flows.

### P3.2 Bridge branch (fixture F5)

- Tenor is pinned at 3 months ([generator] bridge_tenor_months = 3, decisions entry "Derived term parameters"); the build asserts borrower.term_months equals the pinned tenor for bridge borrowers. Fixture F5 tenor is 3.0 months, scale 1.
- maturity_date = roll_forward(add_months(close, tenor_months)) (F5 step 1: 2026-02-27 plus 3 months is 2026-05-27, Wednesday, unchanged).
- Simple interest ACT/365 on the whole span: interest = quantize(all_in_rate / 100 x facility_cents x year_fraction_act365(close, maturity), 0) once (F5 step 3: 12.0000 percent over 89 days gives 1,463,013.698... cents, quantized once to 1,463,014).
- Single repayment at maturity: principal = facility_cents paid with the accrued interest in one payment; total_repayment = interest + principal (exact integer sum, 51,463,014 in F5).
- Row model extension: NONE. build_bridge_schedule returns the existing Schedule and ScheduleRow types with exactly one row (period 1, payment_date maturity, opening facility, interest as above, principal facility, closing 0). The formula is algebraically one period of the amortizing accrual on the opening balance, so the shared interest helper applies unchanged. Rejected: a bespoke bridge row type, which would duplicate the amortizing shape for no gain.
- build_schedule keeps its phase 2 signature and its NotImplementedError for "rbf_capped" and "bridge", with the message pointing at build_rbf_schedule and build_bridge_schedule; the phase 2 test asserting that behavior is updated to the new message rather than deleted.
- F5 recompute: test_schedule.py rebuilds the bridge from fixture inputs (facility 50,000,000, close 2026-02-27, tenor 3, all_in_rate "12.0000", fee "2.00", coverage "0.00") and matches maturity_date, interest, total_repayment (integer sum of engine row values), closing_fee via closing_fee_cents (1,000,000), moic 1.03 via schedule.moic, and the warrant not_granted block, to the cent.

### P3.3 Returns analytics (pipeline/src/vdebt/returns.py)

returns.py is pure computation (dataclasses in, dataclasses out); all envelope assembly stays in build.py, the single envelope owner from phase 2.

```python
@dataclass(frozen=True)
class DatedCashFlow:
    flow_date: date
    amount_cents: int          # signed integer cents

@dataclass(frozen=True)
class IRRResult:
    status: str                # "feasible" | "no_solution"
    irr_percent: Decimal | None   # percent_annual scale 4 when published
    reason: str | None
    iterations: int

def warrant_value_cents(coverage_pct: Decimal, facility_cents: int,
                        valuation_factor: Decimal) -> int
def lender_cash_flows(schedule: Schedule | RbfSchedule,
                      closing_fee_cents: int,
                      warrant_cents: int,
                      warrant_date: date) -> tuple[DatedCashFlow, ...]
def npv(cash_flows: tuple[DatedCashFlow, ...], close: date, rate: Decimal) -> Decimal
def solve_irr(cash_flows: tuple[DatedCashFlow, ...], close: date,
              params: SolverParams) -> IRRResult
def moic(receipts_cents: int, warrant_value_cents: int, funded_cents: int) -> Decimal
def irr_delta_bp(base_percent: Decimal, shocked_percent: Decimal) -> Decimal
def sensitivity_per_100bp(delta_bp: Decimal, shock_bp: Decimal) -> Decimal
```

IRR, exactly the section 13 pins (XIRR-style: dated flows, ACT/365 year fractions from close):
- Solve r in sum_i CF_i x (1 + r) raised to power (-yf_i) = 0, yf_i = year_fraction_act365(close, flow_date_i). The power is computed in MONEY_CONTEXT; section 15 already records that sign-only bisection absorbs Decimal power noise near the root.
- Decimal bisection on r with bracket [-0.99, 10.00] from [solver.irr] (bracket_low, bracket_high), midpoint (lo + hi) / 2 in MONEY_CONTEXT, sign-based decisions only; terminate when hi minus lo is below tolerance_decimal ("0.000001"); max_iterations 200, exceeding raises SolverError, never a silent fallback.
- no_solution: when NPV(lo) and NPV(hi) share a sign there is no root in the bracket (for example an all-positive flow set, or an RBF shortfall borrower with no warrant whose only flow is the close outflow). The payload publishes status "no_solution" with the reason companion and no IRR value. A degenerate all-zero flow set also returns no_solution with its own reason.
- Published IRR: r x 100 quantized exactly once to percent_annual scale 4, ROUND_HALF_UP (F6 step 27 basis).

Dated lender cash flows per structure (aggregated per date, ascending; F6 step 25 basis, where the final date carries interest + principal + warrant as one flow):
- close (all four structures): closing_fee_cents minus facility_cents (fee received at funding, spec section 5 fees row and the decisions log entry "Lender cash flows include a closing fee band"; F6 step 23: 1,000,000 minus 100,000,000 = -99,000,000).
- bullet and io_then_straight_line: one receipt per payment date of interest + principal from the schedule rows.
- rbf_capped: one receipt per remittance date of remittance_cents; warrant value dated at the final row's payment date (the cap-hit date or the horizon end).
- bridge: one receipt at maturity of interest + principal (total_repayment); warrant value dated at maturity.

MOIC, spec section 5 read literally plus one pinned extension:
- numerator = total schedule receipts + warrant value, where receipts are interest + principal for bullet, io_then_straight_line, and bridge, and total remittances for rbf_capped (a royalty structure has no interest and principal split; the whole remittance is the lender's receipt). F4 carries no moic field, so this reading is engine-disclosed, cross-checked by closed form in tests, and stated on the methods page in phase 4.
- denominator = gross facility (advance) in cents, never net of fee (decisions entry "MOIC numerator excludes closing fee"); quantized once to multiple scale 2, ROUND_HALF_UP.
- schedule.moic stays as the F2 to F5 fixture surface and returns.moic is the general receipts-based form; a test asserts both agree on the F2 inputs (identical formula, duplicated three lines; moving moic would churn the phase 2 surface and create a schedule to returns import cycle, since returns consumes Schedule).

Fee treatment: the closing fee enters IRR cash flows at close only and is excluded from the MOIC numerator (phase 1 decision, unchanged). For RBF borrowers the drawn closing fee still enters the close cash flow even though fixture F4's schedule surface carries no fee field (fixture scope versus returns scope, P3.1).

F6 readiness (payload publication is phase 4, computation is phase 3): returns.py exposes everything F6 needs now. The shocked schedule is build_schedule at the shocked all_in_rate (F6: base "7.0000", shocked "9.0000"); solve_irr runs on base and shocked flow sets; irr_delta_bp = (shocked_percent minus base_percent) x 100 quantized once to bp scale 1 (F6 step 31: 222.6); sensitivity_per_100bp = delta_bp / (shock_bp / 100) quantized once to bp scale 1 (F6 step 32: 111.3 at a 200 bp shock). Phase 3 tests recompute F6 end to end from engine code with the fixture's own pinned rates and flows; the build publishes the shocked and sensitivity block in phase 4 beside the downside scenario.

### P3.4 Warrant model (fixed factor)

- Formula (section 13 pin, stated assumption never a market quote): warrant_value_cents = to_cents(coverage_pct / 100 x facility_cents x cfg.warrant.valuation_factor), with the product taken in MONEY_CONTEXT and quantized exactly once at the to_cents boundary, ROUND_HALF_UP. F6 step 24: 0.02 x 100,000,000 x 0.15 = 300,000.0000 becomes 300,000 cents. valuation_factor is "0.15" from [warrant].
- Dating: cfg.warrant.placement is "final_payment_date": the final period's payment date for term structures, maturity for bridge, the final remittance row's date for RBF (cap-hit or horizon end).
- Zero coverage: value 0 published with the "status": "not_granted" companion on the value leaf, never a missing field (section 6.4). The leaf shape is deliberately asymmetric and fixture-pinned: a not_granted leaf is {"value", "unit", "scale", "status": "not_granted"} (F4 and F5 shape) and a granted leaf carries no status key (F6 warrant_value shape). This asymmetry is the fixture contract; do not "clean it up".
- Location: returns.py (warrant value is a returns quantity consumed by cash flows and MOIC). Provenance string names the fixed factor model and the stated-assumption label; every site surface that renders a warrant value prints the assumption label (spec section 5).
- Downside: phase 4 only. Spec 10.5 downside transmission moves the warrant value under downside; phase 3 computes and publishes base only, and returns.py takes valuation as an explicit parameter so phase 4 passes the downscaled factor without reshaping the interface.

### P3.5 Structure comparison table (pipeline/src/vdebt/compare.py)

- Three rows per spec 10.6: "bridge", "io_then_straight_line", "rbf_capped" (the spec pins these three; the drawn bullet structure is not a comparison row). compare.py returns plain dataclasses; build.py assembles the comparison.json envelopes, keeping one envelope owner.
- Identical borrower inputs: all rows share the same borrower inputs, close date, base rate snapshot (source and source_timestamp), spread and all-in rate, facility amount, closing fee, and warrant coverage. The table document carries one shared block; test_compare asserts byte-identical borrower inputs and identical rate source and source_timestamp across the three rows (spec 10.6 acceptance).
- Facility column: repeats the canonical sized facility in all three rows. Sizing is canonical (one capacity per borrower, computed on the io-then-straight-line serviceability schedule per the phase 2 decision), so the facility is NOT re-sized per structure. This is stated explicitly on the table and the structures page as the disclosed serviceability proxy: the facility is the borrower's serviceable capacity and the structures differ in schedule and returns at that capacity. Disclosed on the methods page in phase 4.
- Columns, spec 10.6 order plus one disclosed extension: structure, facility amount, all-in rate, term, IO months, total interest, closing fee, lender IRR, MOIC, min DSCR, binding constraint, first-breach month under downside, warrant coverage, citations, and total receipts. The extension is required for legibility at zero browser arithmetic: the RBF row's contractual interest is 0, so its return would otherwise be invisible in the pinned columns.
- Per-structure cell semantics:
  - bridge: term 3 months (pinned tenor); IO months not_applicable with reason "bridge carries no interest-only period, single repayment at maturity"; total interest = bridge interest; min DSCR = CFADS at the maturity period divided by total_repayment (the only period with debt service; zero-service periods are excluded from the minimum); IRR and MOIC from returns on the bridge flows.
  - io_then_straight_line: term = io + 36; IO months = borrower io_months; total interest from the canonical schedule; min DSCR equals the sizing min_dscr (same schedule basis, so the row ties back to the sizing answer).
  - rbf_capped: term 24 months (pinned horizon); IO months not_applicable with reason "RBF remittances begin in period 1, no interest-only period"; total interest = 0 cents with provenance "derived: RBF carries no interest term" (a contractual zero, not a missing metric) and total receipts = final cumulative remittances; min DSCR = CFADS / remittance over periods with positive remittance, not_applicable with reason when no period has positive remittance (pre-revenue rows); IRR and MOIC from returns on the RBF flows.
  - all rows: closing fee and warrant coverage identical (same borrower draws); binding_constraint populated in all three rows repeating the sizing result (capacity is a property of the borrower, not the structure; disclosed). first_breach_month is a status leaf "not_applicable" with reason "covenant engine lands in phase 4"; phase 4 replaces the leaf in place so the column shape never moves.
- Citations: every band-sourced cell carries citation (with access date) and band per the section 6.4 matrix. Each row carries the set of bands it uses (facility sizing, pricing, closing fee, warrant coverage; the IO row adds interest_only; the RBF row adds rbf_cap), and the structures page prints each citation beside its value (spec section 2 rule 2).
- Consistency test: for each borrower, the row matching the borrower's drawn structure must equal the book schedule totals (the comparison rebuilds all three schedules from scratch at the canonical facility).
- Featured borrower: [site] featured_borrower_id = "SYN-0005" (new scenario.toml table, plain string). Rationale: SYN-0005 is the strata slot pinned to be feasible and DSCR-bound by construction at any base-rate level (the slot recipe was margin-chosen so rate drift cannot flip the stratum), it carries a live revenue path so all three structures show non-degenerate debt service and a non-trivial RBF cap path, and the DSCR-bound story is the credit question the target roles interview on. All 24 tables are computed and published; the site features SYN-0005 by default and the borrower selector exposes all 24. The choice is a reviewed config value, not a code constant, so it moves with a decisions entry if ever changed.
- Infeasible borrowers: the three rows still exist with a uniform shape; facility and returns cells publish status leaves (not_applicable, reason "sizing infeasible so no facility exists to fund") and never fabricated zeros.

### P3.6 Payloads

book.json extensions (canonical rules of section 6.4 unchanged: floats banned, fixed-scale strings, keys sorted, LF, trailing newline, no wall clock):

- The schedule block becomes a kind-tagged union (the phase 2 not_applicable placeholder reason for rbf_capped and bridge is retired; the infeasible-schedule reason remains):

```text
"schedule": { "kind": "amortizing", "rows": [ period, payment_date, opening_balance,
              interest, principal, closing_balance ] }
          | { "kind": "rbf", "rows": [ period, payment_date, revenue, remittance,
              cumulative ], "cap", "final_cumulative", "residual",
              "status": <envelope feasible | shortfall with residual companion> }
          | { "status": <not_applicable envelope>, "reason": <infeasible sizing reason> }
```

- Each borrower gains a returns block (base scenario only in phase 3):

```text
"returns": { "status": <envelope feasible | not_applicable with reason>,
  "lender_irr": { "status": <envelope feasible | no_solution>, "value": <percent_annual
                  scale 4 envelope, iff feasible>, "reason": <str, iff no_solution> },
  "moic": <multiple scale 2 envelope>,
  "total_receipts": <usd_minor_units envelope>,
  "closing_fee": <usd_minor_units envelope>,
  "warrant": <usd_minor_units leaf, "status": "not_granted" companion iff zero coverage>,
  "cash_flows": [ { "date": <date envelope>, "cash_flow": <signed usd_minor_units
                    envelope> }, ... ] }
```

- Status enums: "no_solution" (IRR, reason companion) and "shortfall" (RBF schedule, residual companion) are already in the section 6.4 Status enum and companion matrix; no enum change. Infeasible borrowers publish returns status not_applicable with reason and no cash-flow or value keys.
- The cash-flow list is published per borrower so the verify gate can recompute IRR straight from the published flows and the site can chart them with zero arithmetic. Size cost is bounded by the longest schedule (about 55 dated flows per borrower); acceptable at 24 borrowers.

comparison.json, a NEW canonical file at payloads/<asof>/comparison.json written through snapshot.write_payload in the same build. Recommendation: separate file, not a key inside book.json. Justification: book.json keeps its pinned phase 2 meta plus rates plus borrowers shape, so the phase 2 byte-identity and book-hash surfaces stay untouched; the comparison is a cross-borrower derived document that would be an orphan key outside the pinned book shape; the site structures route loads comparison.json only; and the phase 2 "one aggregate file" rationale targeted per-borrower fragmentation and metadata orphans, which one additional aggregate document with its own meta block does not create. Trade-off: a second file in the two-build byte-identity diff (trivial: the diff covers the whole payloads tree) and one more write_payload call.

```text
{ "meta": { "asof", "book_sha256": <same envelope as book meta>,
            "featured_borrower_id": "SYN-0005" },
  "tables": [ { "borrower_id",
                "shared": { close, base_rate (source + source_timestamp), all_in_rate,
                            facility, closing_fee, warrant_coverage },
                "rows": [ <three StructureComparison rows as envelopes,
                           including first_breach_month placeholder> ] }, ... ] }
```

meta.book_sha256 binds the comparison to the same generator document as book.json; a test asserts the two meta values match. All 24 tables are published (sorted by borrower_id), featured_borrower_id is a plain string echoed from scenario.toml [site].

### P3.7 Site shell (web/)

Toolchain affirmation (not reopening the phase 1 decision): Vite with vanilla TypeScript, no framework. The zero-arithmetic rule is provable with a few hundred lines of formatting-only TypeScript; a framework ships runtime code that must be audited for arithmetic and adds dependency churn; a static multi-page output deploys to GitHub Pages with no router or base-path tricks. This is recorded as an affirmation with rationale, not a challenge.

Layout:

```text
web/
  package.json  package-lock.json  tsconfig.json  vite.config.ts
  index.html  sizing.html  structures.html  returns.html  methods.html
  verification.html
  scripts/sync-payloads.js       byte copy of payloads into web/src/data/
  scripts/charts/                build-time SVG generators (Node, never shipped)
    capacity-chart.js  cashflow-chart.js
  src/
    routes/                      one module per page
    lib/payload.ts               types plus shape validation (throws on drift)
    lib/format.ts                display formatting only
    components/framing.ts  footer.ts  selector.ts  rationale.ts
    content/rationale.ts         illustrative structuring notes with citations
    data/                        generated by sync-payloads.js, git ignored
    charts/                      generated SVG, git ignored
    styles/main.css
  public/og.svg                  Open Graph placeholder (PNG swap at phase 5)
```

- Routes: Vite multi-page build, one HTML entry per route (no client router). Phase 3 ships index, sizing, and structures live; returns, methods, and verification render as honest placeholders (framing line plus "this page ships in a later phase"); dashboard and case study arrive in phases 4 and 5.
- Payload loading (recommendation: copy at build, not runtime fetch and not a Vite alias): npm prebuild runs web/scripts/sync-payloads.js, which copies payloads/<asof>/book.json, payloads/<asof>/comparison.json, and payloads/build_meta.json byte for byte (no parsing, no transformation) into web/src/data/ (git ignored). Route modules then use static TypeScript JSON imports, which Vite bundles at build time. This keeps import specifiers stable across asof changes, keeps the committed payload the single source of truth, and reduces the zero-arithmetic claim to "the browser formats and displays envelope values". Rejected: direct import of ../payloads/<date>/book.json (the dated directory makes the specifier environment dependent) and runtime fetch (banned by spec section 6).
- Zero-arithmetic boundary, pinned definition: format only means digit grouping, decimal-point placement per the unit's scale (cents to display dollars is point placement, not computation), suffixes, and date rendering. It excludes every sum, ratio, difference, interpolation, and threshold comparison in shipped browser code. lib/payload.ts validates shape (unit, scale, status companions) at load; lib/format.ts only renders. Any number the site shows must appear verbatim in a committed payload.
- Borrower selector: components/selector.ts renders a select over the committed borrower ids from book.json; changing it swaps which committed record or comparison table the page displays (client-side filter per spec section 12), with SYN-0005 as the default featured borrower. No recomputation of any kind.
- Static charts (recommendation: build-time SVG generation): scripts/charts/*.js read web/src/data/*.json and emit byte-stable SVG into web/src/charts/, imported as static assets. All chart geometry (scaling values to pixels) runs in the Node build script, never in the browser, so the zero-arithmetic boundary is auditable by inspection of the shipped bundle. Rejected: browser-side SVG generation (scaling arithmetic would ship to the client) and pre-rendered SVG committed under payloads/ (derived artifacts inside the canonical payload tree invite drift and pollute the byte-identity surface). Chart code lives in web/scripts/charts/ and is imported by no route module. Phase 3 charts (cut-line: charts compress first if time runs long): (1) structures page cumulative lender receipts path for the featured borrower, three series straight from the published cash-flow lists; (2) sizing page facility versus band cap bar for the featured borrower.
- Framing line: components/framing.ts renders the section 4 line verbatim on every page: "A portfolio demonstration of venture debt structuring and lender returns on synthetic borrowers and public rate data. Illustrative sizing tool, not credit approval." Plus the section 2 rule 3 label "illustrative sizing, not credit approval" wherever sizing output renders.
- Footer: components/footer.ts stamps the snapshot date (asof from the payload rates block) and the commit SHA from build_meta.json (copied at build). The checks-passed stamp ships with the verification page in phase 5: the footer never displays an unverified claim.
- Phone-readable: mobile-first CSS in styles/main.css, no CSS framework; single column below 640px; the comparison table scrolls horizontally with a sticky structure column (one DOM, no transposition logic). The index answers with links to the five spec section 12 tasks.
- Open Graph: public/og.svg placeholder (1200 by 630, PNG swap at phase 5 per the decisions record) carrying the product name and subtitle; head tags carry og:title, og:description (the framing line), and og:image. The real image lands with the phase 5 screenshots.
- asof deep link: phase 3 ships one asof; route code parses the spec section 12 parameter ?asof=YYYY-MM-DD and validates it against the committed asof (single-day behavior, honest), reserving multi-day switching for whenever more than one asof directory is committed.
- Site build and deploy: npm run build = sync-payloads, then tsc with the noEmit type check, then vite build to static dist/ with the GitHub Pages base path set in vite.config.ts. .github/workflows/site.yml runs npm install and npm run build and deploys, so the site is live at phase end (spec phase 3 acceptance). The kill gate runs the same build locally and greps dist/ for the framing line on every page.
- Dash-scan hygiene: web source (*.ts, *.html, *.css, *.svg, *.json, *.yml) is in the scanned source class; copied payloads and generated SVG must pass the section 9 scan (canonical JSON is already clean; chart scripts must emit hyphen-free SVG text). package-lock.json is scanned as *.json: command-line flag tokens are exempt but any other double hyphen would fail; if npm ever emits one, that is a reviewed exemption-table change with a decisions entry, never a silent skip. node_modules and dist are already excluded.

### P3.8 Test plan (158 existing tests stay green)

- test_schedule.py additions: F4 recompute, both cases field-by-field (payment dates, rows with revenue, remittance, cumulative, final_cumulative, residual, status plus shortfall residual companion, warrant not_granted); synthetic cap-before-horizon case (rows stop at the cap period, no trailing zero rows); F5 recompute field-by-field (maturity, interest, total repayment as integer sum, closing fee, moic, warrant not_granted); RBF invariants (remittance never exceeds residual, cumulative never exceeds cap, pre-revenue path yields all-zero remittances and shortfall, dates strictly increasing); bridge end-of-month clamp case; the updated build_schedule refusal message for rbf_capped and bridge.
- test_returns.py (new): F6 recompute from engine code, base and shocked schedules included; aggregated lender cash flows equal fixture lender_cash_flows_base exactly (dates and cents, final date combined per step 25); base and shocked IRR within abs 0.0001 percent; NPV bracket check recomputed with engine npv at published minus and plus the tolerance (fixture steps 28 and 30 basis: positive below, negative above); irr_delta_bp 222.6 and sensitivity 111.3 exact; total interests exact; moic 1.03; warrant 300,000 with no status key. MOIC closed form: F2 moic equals (interest + principal + warrant) / facility at scale 2 computed by direct Decimal division in the test; schedule.moic and returns.moic agree. no_solution: all-positive flow set and degenerate all-zero set return status no_solution with reason and no value; companion matrix accepts the block. Solver hard failure: monkeypatched tolerance forcing 200 iterations raises SolverError. Warrant: zero coverage gives 0 with not_granted companion; single-quantize boundary checked against a hand product. Fee: close flow equals fee minus facility for all four structures.
- test_compare.py (new): identical inputs assertion (three rows share byte-identical borrower inputs and base rate source plus source_timestamp, spec 10.6 acceptance); citation presence (every band-sourced cell carries citation with access date and band); binding constraint populated for all three rows; facility repeated across rows; IO-month cells for bridge and RBF are not_applicable with reasons; first-breach placeholder present with its reason; the row matching the drawn structure equals the book schedule totals; featured borrower id in meta equals the scenario.toml value; all 24 tables present with three rows each; comparison meta book_sha256 matches book meta.
- payload schema tests (test_snapshot.py and test_build.py additions): returns blocks and comparison.json pass validate_payload; status companions verified (no_solution reason, shortfall residual, not_granted zero value, not_applicable reason); no floats anywhere; the byte-identity kill gate extends to book.json plus comparison.json (two-build tree diff unchanged in mechanics).
- site build: note only in this phase, per the orchestrator. The kill gate runs npm install and npm run build and asserts dist/ exists with the framing line on every page; the automated CI run lands with the phase 5 workflow.
- Fixture-runner compare mode: deferred to phase 5 with the full gate (see the decisions record); the section 10 pin admits later phases and phase 3 recompute already runs end to end from engine code in pytest.

### P3.9 Task breakdown (one sitting per slice)

| # | Slice | Owner | Depends on | Parallel |
|---|-------|-------|-----------|----------|
| 1 | schedule.py RBF branch (RbfRow, RbfSchedule) + F4 tests | backend-dev, tester | none | with 2, 3, 4 |
| 2 | schedule.py bridge branch + F5 tests | backend-dev, tester | none | with 1, 3, 4 |
| 3 | returns.py (warrant, cash flows, IRR, MOIC, delta helpers) + test_returns F6 | backend-dev, tester | none | with 1, 2, 4 |
| 4 | web/ scaffold: Vite vanilla TS, sync-payloads, payload loader, format lib, framing and footer, placeholder routes | frontend-dev | none (shapes pinned here) | with 1, 2, 3 |
| 5 | compare.py + [site] config key + test_compare | backend-dev, tester | 1, 2, 3 | |
| 6 | build.py: schedule kinds, returns blocks, comparison.json, rbf_shortfall predicate migration (+ decisions entry), byte identity on both files | backend-dev | 1, 2, 3, 5 | with 7 |
| 7 | site routes: index, sizing (selector), structures (comparison table, rationale notes, chart data wiring) | frontend-dev | 4 | with 6 |
| 8 | chart build scripts, SVG generation, OG placeholder | frontend-dev | 7 | |
| 9 | site live: site.yml, GitHub Pages deploy, npm build in the kill gate | frontend-dev, 10-git, orchestrator | 6, 8 | |
| 10 | Kill gate: full suite green, verify gate green (dash scan over web files), two-build diff clean, npm build green, docs updated, phase commit | orchestrator, 10-git | 9 | |

Notes: slices 1 to 4 run in parallel (two schedule streams, one returns stream, one site stream); 7 may develop against the pinned payload shapes in parallel with 5 and 6 and integrates at slice 9. Spec section 18 kill criterion applies: any schedule branch that cannot match its closed-form fixture to the cent by gate time is dropped from v1 with the omission disclosed on the methods page, and the remaining branches ship.

### P3.10 Verification checklist and risks

- [x] Every component has one owner and one file (P3.0 table).
- [x] Data flow has no dead ends (P3.0).
- [x] File structure matches the spec section 7 layout (returns.py, compare.py, web/) with no new modules beyond it.
- [x] All technology choices carry rationale and rejected alternatives (row models, comparison payload placement, site toolchain, chart generation, payload copy).
- [x] Phase integrates cleanly with prior phases: fixtures F1 to F6 untouched, canonical serialization unchanged, sizing and pricing untouched except the disclosed strata predicate migration, book hash fixture untouched.
- [x] Risks with mitigations:
      - RBF row semantics drift from F4: the stop-at-cap rule and the min(nominal, residual) rule are tested against both fixture cases plus a synthetic early-cap case.
      - Returns layer inventing an interest split for RBF: the receipts-based MOIC reading is pinned and disclosed; F4 carries no moic so no fixture is bent.
      - Comparison table reading as per-structure sizing: the facility column repeats by design and the serviceability-proxy disclosure renders on the table itself and in the rationale notes.
      - Browser arithmetic creeping into the site: the format-only boundary is pinned in P3.7 and charts run entirely at build time; the shipped bundle can be audited for arithmetic.
      - package-lock.json failing the dash scan: reviewed exemption change only, flagged in P3.7.
      - Strata membership drift from the predicate migration: the zero-count gate still fails loudly and the manifest republishes with counts and sorted members.

## Phase 4 design

Appended 2026-11-12 (phase 4 of 6). Sections 1 to 15 and the Phase 2 and Phase 3 designs above are binding and unchanged. This section pins the covenant package (pipeline/src/vdebt/covenants.py), the downside scenario and its transmission, the rate-shock and sensitivity publication, the methods page pipeline, the returns page and book dashboard routes, the payload and schema extensions (including the new canonical scenarios payload), the phase 4 test plan, and the ordered task slices. The full verify gate and CI blocking gates are phase 5. Fixtures F1 to F6 are never regenerated.

### P4.0 Component map and data flow

| Component | Responsibility | File |
|-----------|---------------|------|
| Covenant engine | four covenants, per-period actuals and headroom envelopes, first-breach month | pipeline/src/vdebt/covenants.py |
| Scenario runner | base, downside, and rate-shock blocks; invariance enforcement; deltas | pipeline/src/vdebt/scenarios.py |
| Payload assembly | covenants and scenario blocks in book.json, scenarios.json, dashboard summary, comparison first-breach cells | pipeline/src/vdebt/build.py |
| Envelope and status | breach status enum member; validator otherwise untouched | pipeline/src/vdebt/snapshot.py |
| Methods content | canonical docs/methods.md compiled to a TS module | docs/methods.md, web/scripts/build-methods.js, web/src/content/methods.ts |
| Returns page | base versus downside side by side, published deltas | web/src/routes/returns.ts |
| Book dashboard | committed counts verbatim, build-time SVG | web/src/routes/dashboard.ts, web/scripts/charts/dashboard-charts.js |
| Dashboard config | capacity bin edges | pipeline/config/scenario.toml [dashboard] |

Data flow additions to section 4 and P3.0: the generator paths and the sized facility feed covenants.py together with the structure schedules and the configured covenant limits; covenants.py emits typed per-period headroom rows for base and downside; scenarios.py recomputes the downside paths and the RBF remittance schedule, assembles the scenario flow sets and every published delta, and derives the rate-shock block from the phase 3 returns helpers; build.py writes book.json (covenants summaries and scenario blocks added), scenarios.json (per-period envelope detail), and comparison.json (real first-breach cells), plus the book summary block the dashboard renders; web/scripts/sync-payloads.js copies all payload files byte for byte; route modules import book.json and comparison.json only (scenarios.json is read by build-time chart scripts and reaches the browser only as inert SVG); docs/methods.md is compiled by web/scripts/build-methods.js into web/src/content/methods.ts at site build time. No path dead ends: every published figure traces to a committed input, a configured band with its citation, or a fixture.

Phase 4 technology decisions:

| Decision | Choice | Why | Alternative rejected | Trade-off |
|----------|--------|-----|----------------------|-----------|
| Covenant module placement | new pipeline/src/vdebt/covenants.py | spec section 7 lists it; one owner per metric family | folding into returns.py | one more module |
| Scenario module placement | new pipeline/src/vdebt/scenarios.py beside build.py | scenario math must be pure and testable; build.py owns envelopes and already spans 1,300 lines | extending build.py with scenario sections | one more module (flagged like the config.py addition) |
| Per-period covenant detail | separate canonical payloads/<asof>/scenarios.json | keeps book.json small enough to statically bundle; byte identity already covers the whole payloads tree | nesting per-period rows in book.json | third file in the tree diff |
| Scenario headline placement | nested per-borrower scenarios block in book.json | the returns page reads one borrower record side by side with no third import | a separate headline payload | book.json grows by summaries only |
| EBITDA proxy | EBITDA_t = cfads_t per period | CFADS is the project's only earnings proxy; no cash-cost model exists | annualized run rate | leverage reads as a coverage proxy; disclosed |
| Net debt | closing debt balance minus cash balance per period | cash netting is the standard convention and both quantities exist | facility only | depends on the derived cash path |
| Min cash limit | 6.0 months of net burn, stated assumption | scales with the borrower like the runway floor; months of burn is the book's cash vocabulary | fixed dollar floor | not a market quote; labeled |
| Downside return metrics | base warrant valuation inside IRR and MOIC in both scenarios (owner confirmed 2026-11-12) | makes the pinned term-loan IRR invariance literal | scenario-pure flows (recorded alternative, see P4.2) | downside MOIC understates the revalued kicker; disclosed |
| Dashboard numbers | committed count envelopes in book.json meta | the phase 5 gate can recompute them naively | Node-side counting | meta block grows |
| Methods delivery | docs/methods.md compiled to a TS string module at build time | canonical doc stays canonical; no runtime markdown dependency; dash clean by construction | build-time markdown to HTML; runtime parser | one generated file (git ignored) |
| First-breach unit | months scale 1, value equals the period index | periods are monthly, so the index is months since close | count unit | slightly odd scale for integers |

### P4.1 Covenant module (pipeline/src/vdebt/covenants.py)

Vocabulary (spec section 5), four covenants pinned with type, limit, and data:

| Covenant | Type | Limit | Consumes per period t | Applicability |
|----------|------|-------|----------------------|---------------|
| net_leverage | max | [covenants] net_leverage_max "3.0000" | EBITDA_t = cfads_t; net_debt_t = closing_balance_t minus cash_t | not_applicable when EBITDA_t is not strictly positive in every tested period |
| interest_cover | min | [covenants] interest_cover_min "2.0000" | EBITDA_t = cfads_t; cash interest = interest_t of the structure row | not_applicable for zero EBITDA (pre-revenue) and for structures with no interest term (rbf_capped) |
| min_cash | min | months of burn floor (below) | cash_t recurrence | always applicable |
| dscr_floor | min | cfg.sizing.dscr_floor ("1.1500", bands.dscr_floor_reference, SBA citation) | cfads_t / (interest_t + principal_t); for RBF cfads_t / remittance_t over positive-remittance periods only | not_applicable when no tested period has positive debt service (pre-revenue RBF) |

Pinned data definitions:
- EBITDA proxy: EBITDA_t = cfads_t (the gross-margin proxy) because cash costs are not modeled in the generator. Published as a ratio leaf with provenance "derived: EBITDA proxy is period CFADS, gross margin basis, cash costs not modeled". Rationale: CFADS is the book's only earnings quantity and reusing it keeps the covenant math hand-checkable; a synthetic opex model would invent a second cost base. Trade-off: the leverage ratio reads as a coverage multiple rather than a market EBITDA multiple; disclosed on the methods page.
- Net debt: net_debt_t = closing_balance_t minus cash_t (exact integer cents, may be negative). closing_balance_t is the structure schedule's closing balance for period t (bridge: the single row; RBF: the advance carried constant because no principal amortizes). Rationale: net debt conventionally nets cash and both quantities already exist; facility-only net debt would overstate leverage for cash-rich borrowers. Trade-off: the ratio depends on the derived cash path, which is pinned here and published per period in scenarios.json.
- Cash path: cash_t = cash_(t-1) minus net_burn_cents minus debt_service_t, with cash_0 = cash_cents (the drawn cash) and debt_service_t = interest_t + principal_t of the structure row (RBF: remittance_t; bridge: the single maturity payment). Facility proceeds never enter the recurrence (the conservative lender-view runway convention from the decisions log, extended to the cash path). No zero clamp: a negative balance publishes as a signed integer and is exactly what a breach shows. Every step is exact integer cents with no rounding point. Derived values: burn_floor_cents = quantize(min_cash_floor_months times net_burn_cents, 0) once; the minimum-cash recurrence reads cash_(t-1) minus burn_floor_cents as its threshold, which is identical to computing the full path and subtracting the constant floor at the end.
- Minimum cash limit: limit_t = burn_floor_cents (base) or quantize(min_cash_floor_months times downside_burn_cents, 0) (downside), so the floor scales with the scenario's burn. Stated project assumption with the citation "Venture Debt Engine stated covenant assumption, not a market quote (accessed 2026-11-12)".

Headroom per period: headroom_t = limit minus actual for max types (net_leverage) and actual minus limit for min types (interest_cover, min_cash, dscr_floor), computed in Decimal at MONEY_CONTEXT on the unquantized ratio and quantized exactly once at the publication boundary. Headroom scale follows the actual's metric scale: ratio 4 for net_leverage, interest_cover, and dscr_floor; usd_minor_units 0 for min_cash (the difference of two cent figures is exact). Per-period DSCR ratios stay unquantized for comparison (the F1 step 23 basis) while the published per-period DSCR leaf quantizes once to ratio scale 4.

Per-period publication shape (the headroom envelopes, per applicable covenant and scenario):

```text
"periods": [ { "period": <count envelope>, "payment_date": <date envelope>,
               "actual": <ratio 4 or usd_minor_units envelope>,
               "headroom": <same unit envelope>, "status": <status envelope
               "feasible" | "breach"> }, ... ]
```

- One row per structure schedule period over the borrower's own schedule horizon (the drawn structure in book.json summaries; each of the three comparison structures in scenarios.json). The limit is published once per covenant block, not per row.
- Zero-service periods (for example RBF periods with zero remittance) publish no DSCR row; when the whole horizon is excluded the covenant block publishes not_applicable with its reason.

First-breach month: the first period index t whose headroom status is "breach" under the pinned scenario (spec 10.6 column: under downside). Published as { "status": <envelope breach | feasible | not_applicable>, "value": <months scale 1 envelope iff breach>, "reason": <str iff not_applicable> }. Pins:
- The value's unit is months, scale 1 ("13.0"), because periods are monthly and the period index equals months since close.
- When no period breaches through the horizon the status is "feasible" (all floors satisfied over the tested horizon, section 6.4 semantics) with no value and no reason; the derived provenance states "no breach through the horizon".
- not_applicable with reason covers borrowers with no facility (infeasible sizing) and covenants with no tested periods.
- The borrower-level first_breach_month is the minimum breach period across applicable covenants, or the feasible leaf when none breaches. This is the value the comparison column and the structures page render.

not_applicable rules (stated reasons, never fabricated zeros):
- pre_revenue borrowers (ARR 0, cfads all zero cents): net_leverage and interest_cover publish not_applicable with reasons "pre-revenue borrower, EBITDA proxy (CFADS) is zero so net leverage is undefined" and "... so interest cover is undefined". This covers the spec's "pre-profit borrowers have negative EBITDA" case: under the pinned proxy EBITDA is never negative, so zero EBITDA is the undefined case and is marked the same way.
- rbf_capped structures: interest_cover not_applicable with reason "RBF carries no interest term so cash interest is undefined".
- pre-revenue RBF rows: dscr_floor not_applicable with reason "no period carries positive remittance so RBF DSCR is undefined".
- min_cash is always applicable (the cash recurrence is defined for every borrower and structure).

Interface:

```python
@dataclass(frozen=True)
class CovenantSpec:
    name: str            # "net_leverage" | "interest_cover" | "min_cash" | "dscr_floor"
    kind: str            # "max" | "min"
    limit: Decimal | int # ratio Decimal, or cents int for min_cash
    unit: str            # "ratio" | "usd_minor_units"
    citation: str        # band citation or stated-assumption citation
    band: str | None     # band label for dscr_floor, else None

@dataclass(frozen=True)
class CovenantPeriod:
    period: int
    payment_date: date
    actual: Decimal | int | None    # None in excluded periods
    headroom: Decimal | int | None
    status: str                     # "feasible" | "breach"

@dataclass(frozen=True)
class CovenantResult:
    spec: CovenantSpec
    status: str                     # "feasible" | "breach" | "not_applicable"
    reason: str | None
    periods: tuple[CovenantPeriod, ...]
    first_breach_month: int | None
    min_headroom: Decimal | int | None

def cash_path(cash_cents: int, net_burn_cents: int,
              debt_service_cents: tuple[int, ...]) -> tuple[int, ...]
def burn_floor_cents(net_burn_cents: int, floor_months: Decimal) -> int
def evaluate_covenant(spec: CovenantSpec, *, ebitda: tuple[int, ...],
                      net_debt: tuple[int, ...], cash: tuple[int, ...],
                      cash_interest: tuple[int, ...] | None,
                      debt_service: tuple[int, ...] | None,
                      dates: tuple[date, ...]) -> CovenantResult
def evaluate_package(covenants: tuple[CovenantSpec, ...], ...) -> tuple[CovenantResult, ...]
```

covenants.py is pure computation like returns.py: dataclasses in, dataclasses out; all envelope assembly stays in build.py, the single envelope owner. New provenance strings pin every derivation verbatim for the methods page (for example "derived: headroom = actual minus limit (min type)"). Test vectors for test_covenants are hand-computed by the tester from this pin before the module lands (the fixture-first rule applied to this new surface; the six golden fixtures stay untouched).

### P4.2 Downside scenario (pipeline/src/vdebt/scenarios.py)

Mechanics:
- The facility is sized on the BASE case once and held fixed in every scenario. The downside never re-sizes (a stressed re-size would move the capacity headline and break the comparison facility column).
- Configured shocks from [downside]: burn_uplift_pct "50.00" and revenue_downlift_pct "30.00". New key [downside] warrant_factor = "0.75" (the pinned warrant downside factor configuration key): downside_warrant_cents = quantize(coverage/100 x facility x valuation_factor x warrant_factor, 0) once, equivalently quantize(base_warrant x warrant_factor, 0). Provenance: "derived: warrant fixed factor model revalued at the downside factor 0.75, stated assumption, standalone scenario figure".
- Path transforms, one quantize per period, ROUND_HALF_UP: revenue_t^D = quantize(revenue_t x (1 minus 30/100), 0); cfads_t^D = quantize(revenue_t^D x gross_margin / 100, 0); burn^D = quantize(net_burn x 1.5, 0). The RBF remittance schedule rebuilds through schedule.build_rbf_schedule on the downside revenue path at the same advance, rate, and cap: remittances fall, rows stop at the cap-hit period or run the horizon, and a base cap-hit borrower can degrade to shortfall (published with its residual companion).
- Downside transmission (the pinned reading of the decisions record "Downside transmission"; owner confirmed 2026-11-12 as Package A):
  - Changes: RBF remittance rows and receipts (fall), covenant headroom and first-breach months (recomputed on downside cfads, downside cash path, downside burn floor), the warrant value (revalued standalone figure with its published delta).
  - Invariant by construction: term-loan (bullet, io_then_straight_line, bridge) contractual cash flows (close fee minus facility, interest, principal) and therefore the lender IRR, MOIC, total interest, and total receipts of those structures, byte-identical across scenarios. Mechanism: the scenario return metrics (lender_irr and moic) carry the BASE fixed-factor warrant valuation in both scenarios, so the only scenario inputs to the return math are the receipts, which are contractual for term structures and downside-only for RBF.
  - RBF return metrics move through the remittance channel only (its receipts are its remittances).
  - The revalued downside warrant is published ONLY as the standalone figure in the scenario block with its own delta and its own provenance naming the pinned rule. The methods page statement (quoted verbatim on every surface that renders the comparison): "Under downside the term-loan contractual cash flows and lender IRR are invariant by construction. The warrant is revalued under downside and published as a standalone figure; per the pinned transmission rule it does not enter the scenario IRR or MOIC. RBF remittances fall with revenue and move the RBF return metrics."
  - Rejected alternative (recorded, not chosen): scenario-pure flow sets where the revalued warrant enters the downside IRR, moving the term-loan total IRR through the warrant channel and requiring a separate contractual_irr figure to carry the invariance. If this reading is ever adopted, only scenarios.py flow assembly and the methods wording change; the payload shape is identical.
- Deltas: every headline delta (downside minus base) is computed in scenarios.py and published as its own envelope (lender_irr_delta as bp scale 1 to match the rate-shock sensitivity vocabulary, moic_delta as multiple scale 2, total_interest_delta, total_receipts_delta, and warrant_delta as usd_minor_units). The browser never subtracts (the P3.7 zero-arithmetic boundary).

Module placement: a new pipeline/src/vdebt/scenarios.py, not build.py sections. Rationale: the scenario math (path transforms, RBF rebuild, flow assembly, delta derivation) is pure computation that test_scenarios must exercise directly, and build.py already owns envelope assembly only. Trade-off: one more module in the vdebt package map (AGENTS.md map gains covenants and scenarios).

Payload shape (recommended and pinned): nested scenario blocks in book.json for the site's headline needs plus ONE new canonical file for the per-period detail.
- book.json borrowers[] gain two keys (the meta plus rates plus borrowers top shape is unchanged):

```text
"covenants": {
  "base":     { <four covenant blocks: spec leaves (name, kind, limit with
                 citation), status, reason iff not_applicable, min_headroom,
                 first_breach_month> },
  "downside": { <same shape> },
  "first_breach_month": <borrower-level months leaf or status leaf>
},
"scenarios": {
  "base":     { "lender_irr", "moic", "total_interest", "total_receipts",
                "closing_fee", "warrant", "warrant_contractual" },
  "downside": { "lender_irr", "moic", "total_interest", "total_receipts",
                "closing_fee", "warrant", "warrant_contractual",
                "rbf_status", "rbf_residual" (iff structure rbf_capped) },
  "deltas":   { "lender_irr_delta", "moic_delta", "total_interest_delta",
                "total_receipts_delta", "warrant_delta" },
  "rate_shock": { <P4.3 block> }
}
```

  Infeasible borrowers publish the not_applicable shape (status plus reason) in both blocks, never fabricated zeros.

- NEW canonical file payloads/<asof>/scenarios.json, written through snapshot.write_payload in the same build: meta (asof, book_sha256 from the same generator document) plus one "borrowers" list with, per borrower, per scenario (base, downside), per structure (the drawn structure plus the three comparison structures), the per-period covenant blocks in the P4.1 shape, the per-period cash path, and the scenario schedule rows (RBF remittance rows differ under downside). Justification: the per-period envelope rows for 4 covenants times 2 scenarios times up to 54 periods would multiply book.json by megabytes and bloat every route bundle (book.json is statically imported and inlined by Vite), while no phase 4 page renders per-period rows; the byte-identity diff already covers the whole payloads tree (P3.6 note), so a third file costs nothing in determinism mechanics. Trade-off: a third canonical file and a third SHA256 in the gate printout; sync-payloads.js copies it byte for byte but no route module imports it.
- comparison.json: the first_breach_month cell replaces its placeholder in place (the P3.5 pin: the column shape never moves). Each of the three rows runs its own covenant computation on its own structure schedule under downside. A row whose borrower is infeasible keeps the not_applicable shape with the sizing reason.

### P4.3 Rate shock and sensitivity publication

Computation exists since phase 3 (returns.irr_delta_bp, returns.sensitivity_per_100bp; F6 pins 222.6 and 111.3). Phase 4 publishes, per borrower, in the scenarios.rate_shock block of book.json:
- shocked_all_in_rate (already published in pricing; echoed here by the same envelope)
- shocked_total_interest: usd_minor_units envelope, total interest of the schedule rebuilt at the shocked all-in rate
- shocked_lender_irr: percent_annual scale 4 envelope with the lender_irr document status companion shape (no_solution possible)
- irr_delta_bp: bp scale 1 envelope, (shocked minus base) x 100 quantized once
- sensitivity_per_100bp: bp scale 1 envelope, delta_bp / (rate_shock_bp / 100) quantized once
- The warrant stays at its base value in the shocked flow set (the shock is a rate scenario only). RBF borrowers publish shocked_total_interest as the contractual zero with the "derived: RBF carries no interest term" provenance and their shocked IRR fields as not_applicable with reason "RBF carries no interest term so a rate shock does not reprice its receipts" (never a fabricated 0 bp).
- Every published figure carries unit bp on the two delta leaves (spec 10.4) and its base rate source plus source_timestamp on the rate leaves (external provenance).
- The band-excursion check stays report-only and base-only (phase 2 pin unchanged); the shocked all-in remains excluded from it.
- F6 mapping: the F6 fixture acceptance now maps to this publication; the engine fixture-output compare mode lands in phase 5, while the pytest recompute asserts the published block equals the F6 expected values on the fixture's own inputs (delta 222.6 exact, sensitivity 111.3 exact, shocked IRR 12.9080 within abs 0.0001 percent).

### P4.4 Methods page

- docs/methods.md is the canonical text (the documentation agent authors it in a later task). Required section outline, pinned from spec 10.7:
  1. Conventions: ACT/365 day count; business-day roll forward only with no holiday table; monthly periods; integer cents with single rounding at the metric boundary and ROUND_HALF_UP; IRR bisection tolerance and bracket; DSCR floor 1.15x reference; runway floor 18 months; band caps and their sources with access dates; the EBITDA proxy and cash-path definitions from P4.1; the capacity serviceability-proxy disclosure; the RBF receipts MOIC reading; the MOIC fee-exclusion asymmetry; the short-rate plus spread restatement; the downside transmission statement (P4.2 wording, verbatim); the warrant fixed-factor model and its downside standalone revaluation; the rate-shock scenario definition.
  2. Rule-of-thumb citations: the full spec section 5 table reproduced with source names and access dates (Mercury, CVCA, SBA), each row next to its value.
  3. Data disclosure: all borrowers synthetic from the seeded generator; base rates are real Bank of Canada Valet observations used only as a rate level anchor; no facility in this repository is real; the gross-margin, growth, and covenant-limit bands are stated project assumptions, not market quotes.
  4. Limitations: single-draw sizing in v1; milestone tranching capped but not simulated as contingent; EBITDA covenants marked not applicable for pre-revenue borrowers; warrant value is a stated-assumption model not a market quote; no collateral or recovery modeling; no tax or accounting treatment; capacity is a serviceability proxy on the canonical schedule; per-period covenant detail lives in scenarios.json.
  5. Prohibited claims: the spec section 2 list repeated verbatim in prose (the five "Never claim, present, publish" bullets plus the non-goals), with the compliance optics rules restated.
  6. Attribution (spec section 19, needed by the phase 5 attribution gate): Open Government Licence Canada naming TB.CDN.90D.MID; the three research citations by name; MIT for the code.
- Delivery (pinned): a build-time compile step, not a runtime markdown dependency. web/scripts/build-methods.js (Node, run in the npm prebuild chain after sync-payloads) reads docs/methods.md and emits web/src/content/methods.ts, a generated TypeScript module exporting PRE-RENDERED STATIC HTML plus a plain-text export for the prohibited-claim test. The compiler is deliberately minimal (headings, paragraphs, lists, thematic breaks, pipe tables, inline code) and hyphen-free; no npm dependency is added. routes/methods.ts renders the static HTML string through the existing page shell. Rationale: docs/methods.md stays the single canonical file the documentation agent edits; no markdown parsing ships to the browser; the generated module is dash clean because the compiler escapes its output and the source is scanned. Rejected: a hand-written TS string module (canonical text would fork from docs/methods.md) and a runtime markdown library (dependency churn and shipped parsing code). Trade-off: one generated file (git ignored alongside src/data and src/charts) and a small compiler to maintain.
- Acceptance wiring (spec 10.7): the documentation test asserts the DSCR reference, the sizing band, both pricing bands, and the cap multiple appear with citations in the text, and that no prohibited-claim vocabulary appears anywhere except the verbatim prohibited-claims section (the phrase list is assembled from fragments in the test so the test source passes the dash scan).

### P4.5 Returns page (web/src/routes/returns.ts)

- Renders base versus downside side by side per spec 12: lender IRR, MOIC, total interest, warrant value, plus total receipts and closing fee, each with its published delta envelope (downside minus base, computed in the pipeline per P4.2). A third column renders the rate-shock block (shocked IRR, shocked total interest, irr delta bp, sensitivity per 100 bp).
- Data: the borrower record's scenarios block in book.json only (committed payloads, static import, no fetch). The existing borrower selector swaps records client side; SYN-0005 is the default featured borrower.
- Every number renders verbatim from its envelope through lib/format.ts (digit grouping and point placement only); the delta column renders published delta envelopes and never subtracts. A delta of exactly zero on the invariant term-loan rows renders as "0.00 (invariant by construction)" from a published status label on the delta envelope, not from a threshold comparison in the browser.
- The warrant row carries the stated-assumption label (spec section 5) and the downside warrant row carries the standalone-revaluation label from the P4.2 provenance string. The framing line and the illustrative sizing label render per the phase 3 shell pins.

### P4.6 Dashboard (web/src/routes/dashboard.ts, new route)

- New route dashboard.html; the spec section 12 route list gains "dashboard" (the case study stays phase 5). Surfaces: capacity distribution, binding-constraint mix, infeasible count over the 24 borrowers, rendered as committed numbers plus build-time SVG charts.
- Number provenance (pinned, never in the browser): book.json meta gains a "book_summary" block computed by build.py at build time and published as count envelopes:
  - capacity_distribution: a sorted list of {bin_low, bin_high, count} entries whose edges come from scenario.toml [dashboard] capacity_bin_edges_usd (integer cents: 0, 100,000,000, 250,000,000, 500,000,000, 1,000,000,000, 2,500,000,000, 5,000,000,000 cents, that is 0 to 500 million USD in six bins), low edge inclusive and high edge exclusive except the last bin which includes its high edge. The binning is engine arithmetic: a histogram binning is arithmetic and belongs in the pipeline where the phase 5 gate can recompute it naively.
  - binding_constraint_mix: counts per binding_constraint value ("runway", "dscr", "band_cap") plus infeasible_count and feasible_count (count envelopes, provenance "derived: count of sizing outcomes over the committed book").
  - A count of committed records is a length read in principle, but the browser does not even do that: every dashboard number is a committed envelope value the route renders verbatim (the zero-arithmetic boundary excludes counts once filtering is involved).
- Charts per the pinned build-time pattern: web/scripts/charts/dashboard-charts.js reads web/src/data/book.json and emits web/src/charts/dashboard-capacity.svg (histogram bars from the committed bin counts) and web/src/charts/dashboard-mix.svg (binding-constraint mix bars from the committed mix counts). Geometry and labels only; the script never counts or bins (a counting script would become a second arithmetic engine outside the verify gate). The browser loads the SVGs as inert static assets.
- Config: scenario.toml gains [dashboard] capacity_bin_edges_usd = [0, 1000000000, 2500000000, 5000000000, 10000000000, 25000000000, 50000000000] (integer cents; integers are legal TOML values under the decimal policy).

### P4.7 Payload and schema impacts

| Surface | Change | Byte-identity impact |
|---------|--------|----------------------|
| book.json | borrowers gain covenants and scenarios blocks (summaries only); meta gains book_summary | payloads regenerate; two-build tree diff mechanics unchanged; committed freshness test covers the new bytes |
| comparison.json | first_breach_month cells replace the placeholder in place; row shape otherwise unchanged | same |
| scenarios.json | NEW canonical file (per-period detail) | third file in the tree diff and in the gate SHA256 printout |
| build_meta.json | untouched | none (excluded from byte identity) |
| pipeline/fixtures/generator/book_sha256.json | UNCHANGED (hash covers the generator document only) | none |
| F1 to F6 | untouched | none |

Status enum (section 6.4): one new member, "breach", added to the Status Literal and STATUS_VALUES in snapshot.py with a docs/decisions.md entry. Rationale: "infeasible" is a solver outcome (no value satisfies the floors); "breach" is a per-period covenant test outcome (a tested period below its floor); overloading infeasible would make the sizing and covenant vocabularies collide. Companion matrix addition: breach requires no companion (the row's limit, actual, and negative headroom are its evidence). feasible, not_applicable, not_granted, shortfall, and no_solution keep their existing semantics and companions; the first-breach month reuses feasible (no breach through the horizon) and not_applicable (no facility, no tested periods) per P4.1. web/src/lib/payload.ts gains the "breach" member in its status validation. No new units (bp exists); new derived provenance strings pin every covenant and scenario derivation verbatim.

### P4.8 Test plan (187 existing tests stay green)

- test_covenants.py (new): headroom sign convention both types on hand rows (max: limit minus actual; min: actual minus limit); the cash path recurrence on a hand case (cash_0 minus t x burn minus cumulative debt service, no zero clamp); burn floor quantize; EBITDA proxy equals cfads per period; net debt equals closing balance minus cash; not_applicable rules and reason strings (pre-revenue EBITDA pair, RBF interest cover, pre-revenue RBF DSCR); excluded zero-service periods produce no DSCR rows; first-breach month on a synthetic path that breaches in period 3 (assert months "3.0") and the feasible leaf when none breaches; validate_payload accepts the new breach enum member; min-cash headroom is exact cents.
- test_scenarios.py (new): downside path transforms (revenue x 0.7, cfads recomputed from downside revenue, burn x 1.5, one quantize each); RBF downside remittance recompute (a base cap-hit case degrades to shortfall with residual companion); warrant downscale (single quantize at the product boundary; the configured key value 0.75 asserted); term-loan IRR invariance assertion: for bullet, io_then_straight_line, and bridge borrowers the base and downside lender cash flow tuples are equal by value and the lender_irr, moic, total_interest, and total_receipts envelopes are byte-identical across scenarios (string equality of canonical values); zero-warrant term borrower SYN-0002 total IRR identical across scenarios; RBF return metrics move only through remittances (contractual receipts equal, remittance receipts differ); covenant headroom and first-breach month differ across scenarios on a synthetic stressed case; every delta envelope equals the naive subtraction recomputed in the test at the published scale.
- test_returns.py additions (F6 publication mapping): the published rate-shock block on the F6 inputs equals the fixture expected (shocked total interest exact, shocked IRR within abs 0.0001 percent, irr_delta_bp 222.6 exact, sensitivity_per_100bp 111.3 exact); RBF rate-shock not_applicable leaves carry their reasons; every rate leaf carries source and source_timestamp.
- test_build.py additions: book.json covenants and scenarios blocks and scenarios.json pass validate_payload; the byte-identity kill gate extends to the third file (mechanics unchanged); meta.book_summary counts equal a naive recount over the committed borrower list (mix counts sum to 24, bin counts sum to the feasible count); committed payload freshness (committed book.json, comparison.json, and scenarios.json equal a fresh build); comparison first_breach_month cells carry real values or the not_applicable shape, never the phase 4 placeholder reason.
- Web kill-gate greps over dist/ (phase 3 pattern): returns page renders the framing line, the base versus downside block, one published delta per headline number, and the invariance label on zero deltas; dashboard renders the committed infeasible count and mix numbers verbatim and both SVG assets; methods renders docs/methods.md content (grep for the DSCR reference, both pricing bands, the cap multiple, and the SBA and Mercury citations).
- Documentation tests (new test_methods.py): spec 10.7 acceptance (DSCR reference 1.15, the 20 to 50 percent sizing band, both pricing bands, the cap multiple appear with citations in the prose); every spec section 5 rule-of-thumb row appears with access dates; the prohibited-claim vocabulary sweep over the whole tree with the verbatim section 2 list exempted only inside the methods prohibited-claims section (phrase list assembled from fragments so the test source is dash clean); the framing line appears in docs/methods.md.

### P4.9 Task breakdown (one sitting per slice)

| # | Slice | Owner | Depends on | Parallel |
|---|-------|-------|-----------|----------|
| 1 | scenario.toml [covenants] and [dashboard] keys; config.py extension plus tests | backend-dev, tester | none | with 2, 5, 6 |
| 2 | covenants.py (dataclasses, cash path, four covenants, first breach) plus test_covenants.py (tester hand vectors from the P4.1 pin) | backend-dev, tester | none | with 1, 5, 6 |
| 3 | scenarios.py (downside transforms, RBF rebuild, Package A flow assembly, deltas, rate-shock block) plus test_scenarios.py | backend-dev, tester | 2 | |
| 4 | build.py wiring: covenants and scenarios blocks in book.json, scenarios.json, meta.book_summary, comparison first-breach cells, breach enum in snapshot.py plus validate_payload, byte-identity extension | backend-dev | 1, 3 | |
| 5 | web/scripts/build-methods.js and methods route wiring (compiler skeleton; docs/methods.md text comes later) | frontend-dev | none | with 1, 2, 6 |
| 6 | web/scripts/charts/dashboard-charts.js and dashboard.html route skeleton | frontend-dev | none (shapes pinned here) | with 1, 2, 5 |
| 7 | returns route: base versus downside side by side, deltas, rate-shock column | frontend-dev | 4 (or against the pinned shapes, integrating at 8) | with 4 |
| 8 | dashboard route final wiring (committed book_summary values, SVG imports) | frontend-dev | 4, 6 | with 7 |
| 9 | docs/methods.md authored to the P4.4 outline plus documentation tests; decision records; npm build and site deploy | 09-documentation, 10-git, orchestrator | 5, 7, 8 | |
| 10 | Kill gate: full suite green, verify gate green (dash scan over new files), two-build diff clean over three payload files, npm build green, docs updated, phase commit | orchestrator, 10-git | 9 | |

Parallelism notes: slices 1, 2, 5, and 6 own disjoint files and can run concurrently (the backend pair shares the tester but no files with the frontend pair); slice 3 follows 2 (it consumes the covenant results); slice 4 is strictly after 3; slices 7 and 8 may develop against the pinned payload shapes in parallel with 4 and integrate at the kill gate. Spec section 18 kill criterion applies: if the dashboard or methods page slips, ship the covenant and scenario payload surfaces with the omission recorded in the methods page limitations (the case study never cuts, and it is phase 5 anyway).

### P4.10 Verification checklist and risks

- [x] Every component has one owner and one file (P4.0 table).
- [x] Data flow has no dead ends (P4.0: scenarios.json reaches the browser only as inert SVG; every headline traces to a committed input or a configured band).
- [x] File structure matches the spec section 7 layout (covenants.py is listed there; scenarios.py is a flagged addition with the same rationale as the config.py addition, recorded in decisions).
- [x] All technology choices carry rationale and rejected alternatives (P4.0 decisions table plus the per-section pins).
- [x] Phase integrates cleanly with prior phases: fixtures F1 to F6 untouched, the book hash fixture untouched, canonical serialization unchanged, the phase 2 and 3 payload shapes extended additively, the comparison column shape unchanged per the in-place replacement pin.
- [x] Risks with mitigations:
      - Downside IRR semantics: Package A pinned and owner confirmed 2026-11-12 with the alternative recorded; flipping needs one decisions entry and touches only scenarios.py flow assembly.
      - EBITDA proxy distortion (period CFADS as EBITDA): leverage reads as a coverage proxy; the limit band is a stated assumption and the methods page discloses the proxy; the limit value can move with a decisions entry.
      - Payload size creep: per-period detail isolated in scenarios.json, summaries only in book.json; the byte-identity gate covers all three files so drift stays loud.
      - Browser arithmetic creeping into deltas or dashboard counts: every delta and count is a published envelope; the P3.7 format-only boundary is unchanged and the shipped bundle stays auditable.
      - Breach enum misread as sizing infeasible: a separate enum member with its own decision record and one companion-matrix update in snapshot.py.
      - Methods compiler output failing the dash scan: the compiler escapes its output, docs/methods.md is scanned as prose, and the kill gate runs the scan over the generated module before the site build.
      - First-breach month drifting between comparison rows and book.json: both consume the same covenants.py result for the same structure and scenario; test_build asserts agreement between the borrower-level leaf and the comparison cell.
