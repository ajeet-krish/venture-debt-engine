# Venture Debt Sizing and Lender Returns Engine: Build Spec

Last updated: 2026-11-10

Standalone build spec for a new, empty repository. It assumes zero context on any other repository or organization. Specification only: no implementation exists at the time of writing.

Status: Specification (2026-11-10)

---

## 1. Purpose

Build one public repository that demonstrates the structuring and returns analytics a venture lending analyst performs: size the venture debt facility a burn-rate startup can actually service, price it off a public short-rate curve, and compute the lender's returns across structure choices. Every borrower is synthetic and every rate is a public observation.

Primary audience: a hiring manager on a tech and life sciences credit advisory or venture lending desk at a Canadian bank (the target posting family is RBCx-style Analyst, Credit Advisory seats). Secondary audience: any corporate banking, venture lending, or credit risk role that values reproducible verification.

The candidate is a May 2026 mechanical engineering graduate with no finance job experience. This project exists to close the venture debt structuring and serviceability gaps and to supply resume vocabulary that matches these job descriptions: structuring, serviceability, DSCR, covenants, pricing, lender IRR, warrant coverage.

This is the single selected credit advisory project for the finance resume track, joining Storm Fund and the GoC Rates Monitor. The target roles are credit advisory, venture debt, corporate banking, and venture lending analyst seats.

The project is a portfolio artifact. It is not a product, not a lender system, and not a claim of production experience. Compliance optics: this is an illustrative sizing tool, not credit approval.

## 2. Non-goals and prohibited claims

Non-goals:

- No real lender data, no real borrower data, no real term sheets, no proprietary term sheet database, ever.
- No forecasts, no rate predictions, no directional views, no recommendations about any real transaction.
- No real-money transactions, no funding, no commitments, no outbound messages of any kind.
- No regulatory, legal, tax, or accounting advice of any kind.
- No accounts, no server runtime, no secrets, no runtime API calls from the browser.
- No dependence on PitchBook, Crunchbase, or any paid data service. All rules of thumb are cited from public guides; the rate curve is a public Bank of Canada series.
- No claim of replicating any specific lender's internal credit policy or approval process.

The project must never claim:

- Never claim experience arranging, underwriting, advising on, or executing any real facility. Every borrower and every schedule in this repository is synthetic.
- Never present this engine as a credit approval, a credit approval case, a credit memo, or a lending recommendation. It is an illustrative sizing tool, not credit approval.
- Never claim production lending system experience, core banking system experience, or exposure to a lender's internal origination or servicing platform.
- Never publish a number that is not produced by the pipeline and traceable to a committed input.
- Never present the rule-of-thumb bands (sizing, spreads, warrant coverage, cap multiples) as facts about any specific deal. They are cited market heuristics, and every band printed anywhere carries its citation.

Compliance optics rules, mandatory in every artifact this project ships:

1. Every page, README, and export carries the framing line from section 4. The framing line is not optional and may not be edited to sound stronger.
2. Every rule of thumb appears with its citation (source name and access date) on the methods page next to the value, never as an unsourced number.
3. The sizing output is labeled illustrative sizing, not credit approval, wherever it renders.
4. Prohibited claim vocabulary (arranged, advised on, executed, credit approval, production system) is caught by a documentation test over the tree.

## 3. Decisions log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-11-10 | Python (uv) offline pipeline, static report site, no runtime API | House pattern; the site consumes committed payloads only, which keeps verification honest and the deployment free of secrets |
| 2026-11-10 | Seeded deterministic synthetic borrower generator | Byte-identical rebuilds are a blocking CI gate, so all randomness must be reproducible from a single seed |
| 2026-11-10 | Integer cents (USD minor units) for money, Decimal internally, single rounding at the metric boundary | Float money hides rounding drift; integer cents at the boundary plus Decimal inside gives one rounding point and auditable outputs. Float is never used for money anywhere in the engine |
| 2026-11-10 | Bank of Canada Valet public short-rate series as the floating base rate | Public, no key, and it anchors synthetic facilities to a real rate level for realism; the project does not depend on any other repository |
| 2026-11-10 | Raw Valet responses committed under a SHA256 manifest with a startup label assertion | CI never touches the network; a changed or wrong series fails loudly printing the real label, the GoC monitor discipline restated from scratch |
| 2026-11-10 | Bisection solver for debt capacity | Monotone in facility size under a runway floor and DSCR floor, so bisection converges provably and is testable against a hand-computed fixture |
| 2026-11-10 | ACT/365 day count, monthly payment periods on a business-day-adjusted schedule | Standard for USD-denominated credit-style schedules; stated on the methods page and tested against closed forms |
| 2026-11-10 | Independent verify gate sharing no code with the engine | A shared bug must not be able to hide behind a shared implementation; it recomputes IRR and DSCR straight from schedules |
| 2026-11-10 | Structure comparison table on identical borrower inputs | Bridge versus term versus revenue-based financing on one borrower is exactly the structuring judgment interviewers ask for |
| 2026-11-10 | Solana devnet anchoring optional and cosmetic | Credit advisory job descriptions do not weigh on-chain proof; it is the first thing to drop |
| 2026-11-10 | Post-draw runway is conservative: (cash minus facility) / net monthly burn | Disclosed on the methods page as a lender-view assumption where proceeds are ring-fenced for debt service and growth, not runway extension |
| 2026-11-10 | CFADS is margin-based: CFADS per period = monthly revenue times a configured gross margin band | Disclosed as a stylized proxy, not operating cash flow; pre-revenue borrowers (ARR 0) are sized on runway and minimum cash only, with DSCR marked not applicable with a stated reason |
| 2026-11-10 | Per-period revenue path: ARR/12 grown at a seeded per-borrower monthly growth rate drawn from a configured band | The revenue path and the derived CFADS path stay deterministic from the committed seed and band config |
| 2026-11-10 | Pricing base restated: short-rate Valet series as base, spread bands restated as short rate plus spread | The deviation from the cited prime-plus convention is disclosed on the methods page, plus a build-time check that flags all-in rates outside the cited total-rate bands |
| 2026-11-10 | Verify gate scope: the gate validates the committed borrower book structurally and against a committed book hash, then naively recomputes everything downstream (schedules, DSCR, IRR, MOIC, sensitivity) from the committed book and rates | It does not re-implement the RNG draw sequence; generator determinism is proven by two-build byte-identity plus the book-hash fixture |
| 2026-11-10 | Payload serialization clarified: all non-integer decimals serialize as fixed-scale decimal strings; only integers (cents, counts) serialize as JSON numbers | The asof date derives from the committed rate snapshot date, never the wall clock |
| 2026-11-10 | RNG policy: stdlib random.Random using only the .random() method with inverse-CDF transforms, documented draw order | Python pinned to 3.12.x so two builds stay byte-identical |
| 2026-11-10 | Lender cash flows include a closing fee band (0.5 to 2 percent of facility) received at funding | MOIC denominates on gross facility amount, stated on the methods page |
| 2026-11-10 | Downside transmission: downside changes RBF remittances, covenant headroom and first-breach months, and warrant value | Term-loan contractual cash flows and IRR are invariant by construction, stated on the methods page |
| 2026-11-10 | Warrant value uses a disclosed fixed-factor model (coverage times facility times a configured factor) | Labeled a stated assumption, not a market quote; formula pinned in docs/architecture.md |
| 2026-11-10 | Added feature bundle approved: case study walkthrough with README screenshots, covenant first-breach month flags, static charts rendered from committed payloads, closing fees in lender cash flows, 24-borrower book dashboard, illustrative structuring rationale notes | Features are distributed into phases 3 to 5 under the cut-line recorded in section 20 |
| 2026-11-10 | Cut: Solana devnet anchoring phase and the optional cross-language rule twin, both removed from the plan entirely | Neither carries weight for the target roles, so both are gone rather than deferred |
| 2026-11-10 | Fixture arithmetic format and the dash-scan exemption table are pinned by the tech lead in docs/architecture.md during phase 1 | One canonical place for fixture comment format and source-code flag exemptions |

## 4. Naming and location

- Repository: `venture-debt-engine`
- Local path: `/Users/ajeet/Projects/finance/venture-debt-engine`
- Site: `https://ajeet-krish.github.io/venture-debt-engine/`
- Product name on the site: `Venture Debt Engine`
- Subtitle: `Venture debt sizing and lender returns`
- Footer framing line (verbatim, on every page and in the README): `A portfolio demonstration of venture debt structuring and lender returns on synthetic borrowers and public rate data. Illustrative sizing tool, not credit approval.`

## 5. Domain/product knowledge model

This section is the domain contract. Every rule here is implemented exactly as written, cited on the methods page, and never published without its citation. Citations are from the 2026-11-10 credit advisory role research (Mercury venture debt term sheet guide and CVCA venture debt market analysis as primary guides, plus the SBA DSCR reference); the citation string travels with every band value in every payload.

### Structure vocabulary and rules of thumb

| Rule of thumb | Band or convention | Citation |
|---------------|--------------------|----------|
| Facility sizing band | 20 to 50 percent of the last equity round size | Mercury venture debt term sheet guide (via 2026-11-10 research) |
| Interest-only period | Typically 12 to 18 months before principal repayment begins | Mercury (via 2026-11-10 research) |
| Loan term | 3 to 5 years | Mercury (via 2026-11-10 research) |
| Warrant coverage | 0 to 20 percent of facility amount; banks 0 to 5 percent, non-banks 10 to 20 percent | Mercury (via 2026-11-10 research) |
| Bank pricing | Prime plus 1 to 3 percent (5 to 8 percent total) | Mercury (via 2026-11-10 research) |
| Non-bank pricing | Prime plus 3 to 4 percent (10 to 14 percent total) | Mercury (via 2026-11-10 research) |
| Revenue-based repayment cap | Repay 1.3 to 2.5 times the advance via a percent of revenue | Mercury (via 2026-11-10 research) |
| DSCR floor reference | Minimum 1.15x (SBA reference; banks often set higher policy minimums) | SBA reference via 2026-11-10 research |
| Canadian venture debt market | Under CAD 200M in 2018 to over CAD 700M in 2022 | CVCA data analysis (via 2026-11-10 research) |

### Structure vocabulary

- **Draw schedule**: facilities in this project are single-draw at close for sizing purposes. A milestone-linked tranche structure (second tranche released on a revenue or financing milestone) is modeled as a structure variant whose tranche sizes are capped by the same sizing band; milestone linkage is not simulated as a contingent event.
- **Interest-only period then amortization**: after the IO period, principal repays either as a bullet at maturity or on a straight-line monthly schedule. The straight-line monthly principal payment is the facility amount divided by the number of amortization periods (the research amortization example: 4-year facility, 12 months IO, 36 months straight-line, monthly principal equals loan divided by 36).
- **Floating pricing**: all-in rate = base rate + spread. The base rate is the committed public short-rate observation at close; the spread band depends on lender type (bank versus non-bank, table above). The base rate is a committed observation, never a forecast.
- **Warrant coverage**: an equity kicker granted by the borrower, expressed as a percent of facility amount, priced at a strike convention stated on the methods page (option-value model: coverage times facility times a stated strike-to-moneyness assumption, disclosed as an assumption, not a market quote).
- **Royalty / revenue-based repayment**: a percent of revenue remitted until a cap multiple of the advance is repaid (band 1.3x to 2.5x). If revenue never reaches the cap within the stated horizon, the residual is a disclosed shortfall, not a default simulation.
- **Covenant package**: net leverage (net debt / EBITDA, max-type), interest cover (EBITDA / cash interest, min-type), minimum cash (min-type), and a DSCR floor with the 1.15x reference. Each covenant carries headroom = limit minus actual (max-type) or actual minus limit (min-type), published with the schedule. Pre-profit synthetic borrowers have negative EBITDA, so EBITDA-based covenants are marked not applicable with a stated reason rather than computed into nonsense; the DSCR floor and minimum cash carry the serviceability story.
- **SAFE versus convertible note**: an equity instrument the debt sits behind, not a facility structure. SAFE: no interest, no maturity, converts at the next round. Convertible note: debt that converts, with interest and a maturity date. The methods page explains the distinction as context for what the venture debt sits behind; neither is priced by this engine in v1.
- **Fees**: closing fee of 0.5 to 2 percent of facility amount, received by the lender at funding, included in lender IRR cash flows; MOIC denominator is the gross facility amount.

### Equations

- **Lender IRR**: solve r for `sum over periods t of (CF_t / (1 + r)^(t / 365)) = 0` where CF_t are dated cash flows from the lender's perspective (negative at funding, interest and principal received on payment dates, plus warrant value at exit), with ACT/365 year fractions from the close date. Solved by bisection on r.
- **MOIC**: (total interest received + principal received + warrant value) / amount funded, published as a multiple with the funding date and horizon stated.
- **DSCR**: `DSCR = CFADS / (scheduled principal + interest)` per payment period, where CFADS is cash available for debt service for that period. The sizing engine enforces a DSCR floor (default 1.15x) across periods.
- **Debt capacity**: the maximum facility amount F such that (a) post-draw runway (cash minus F divided by average monthly net burn) stays at or above the runway floor in months, and (b) minimum DSCR across periods stays at or above the DSCR floor. Solved by bisection on F over the interval [0, upper bound], where the upper bound is the 50 percent sizing band cap. Convergence proven against a hand-computed fixture.
- **Post-draw runway**: `runway_months = (cash minus facility F) / average monthly net burn`, enforced at or above the runway floor (default 18 months); a conservative lender-view convention where facility proceeds do not extend the operating runway, disclosed on the methods page.
- **CFADS**: `CFADS_t = monthly_revenue_t times gross_margin` with gross margin from a configured band; DSCR uses this CFADS; pre-revenue borrowers have CFADS 0 and DSCR marked not applicable with a stated reason.

### Money and rounding

All money is stored as integer cents of its currency (USD minor units, exponent 2) with a minor-unit table. Internal arithmetic uses `decimal.Decimal`; intermediate values are never rounded. A value is quantized exactly once, when it crosses the boundary into storage or a published metric (IRR percent, MOIC multiple, DSCR, basis points). Rounding mode is ROUND_HALF_UP, declared on the methods page. The published IRR is rounded once at the metric boundary to 4 decimal places in percent; the verify gate recomputes from unrounded schedules.

## 6. Architecture

```
Bank of Canada Valet (short-rate series) ──┐
Seeded synthetic borrower generator ────────┼──> pipeline (Python, uv) ──> payloads/*.json ──> static site (Vite + TS) ──> GitHub Pages
Committed rate snapshot + SHA256 manifest ─┘         |
                                                sizing engine (bisection) ──> schedule engine ──> pricing engine ──> returns analytics
                                                          |
                                                    verify gate (independent recompute, blocking, non-zero exit on drift)
```

Rules:

- Python 3.12 managed by uv. Runtime dependencies are the standard library only (decimal, datetime, json, urllib for the manual refresh workflow). Development dependencies: pytest, ruff.
- No runtime API calls from the browser. The site build consumes committed payloads; every number carries its unit, source, timestamp, and rule-of-thumb citation where applicable.
- Raw Valet responses are committed under `pipeline/data/rates/` with a `SHA256SUMS` manifest. CI reads the committed cache and verifies hashes; it never calls the network.
- The verify gate runs in CI as a blocking step and exits non-zero on any drift.
- Rate data discipline (described from scratch; this repository does not import anything from any other project): at startup the client calls the Valet series-detail endpoint (in the manual refresh workflow only) and asserts the returned series label matches the expected pattern for the chosen series. A mismatch prints the actual label and fails the run. The expected labels are committed as assertions; the series codes themselves are treated as unverified until the assertion passes once.

## 7. Repository layout

```
venture-debt-engine/
  pipeline/
    src/vdebt/
      money.py          integer cents, minor-unit table, single rounding rule
      dates.py          ACT/365 year fractions, business-day payment date roll (forward only)
      generator.py      seeded synthetic borrower generator (round size, ARR, burn, cash)
      sizing.py         bisection debt capacity solver under runway floor and DSCR floor
      schedule.py       bullet, IO-then-straight-line, RBF with cap multiple, bridge schedules
      pricing.py        base rate from committed snapshot plus spread bands, rate-shock scenario
      returns.py        lender IRR, MOIC, interest plus warrant value, per structure
      compare.py        structure comparison table on identical borrower inputs
      covenants.py      net leverage, interest cover, minimum cash, DSCR floor, headroom
      snapshot.py       canonical JSON payload writer (unit, source, timestamp, citation)
      cli.py            build, verify, report commands
    src/vdebt_verify/
      gate.py           independent naive recompute, import isolation, dash scan, SHA256 print
    tests/
    fixtures/golden/
      sizing_fixture.json      hand-computed bisection fixture
      schedule_bullet.json     hand-computed bullet schedule fixture
      schedule_io_amort.json   hand-computed IO-then-straight-line fixture
      schedule_rbf.json        hand-computed RBF with cap fixture
      bridge_fixture.json      hand-computed bridge fixture
      rate_shock_fixture.json  hand-computed rate-shock IRR fixture
    data/rates/
      *.json            committed raw Valet responses plus SHA256SUMS manifest
    config/scenario.toml    seed, borrower bands, covenant floors, rate-shock deltas
  web/
    src/                routes: index, sizing, structures, returns, methods, verification
    public/              Open Graph preview image
  payloads/
    YYYY-MM-DD/         canonical per-asof payloads
    build_meta.json     git SHA, wall clock, duration (excluded from byte-identity comparison)
  docs/
    methods.md          canonical conventions text, rendered as the methods page
  .github/workflows/
    ci.yml              all blocking gates
    site.yml            site build and deploy
    refresh-rates.yml   manual workflow to re-fetch and re-hash Valet data
```

## 8. Data contracts

Every numeric field in every payload carries a unit. Schema validation requires the `unit` key; a missing unit fails the build. Every numeric field also carries provenance and a timestamp: external observations carry `source` and `source_timestamp`; derived values carry `asof` and a derivation string; generated values carry the seed and generator version. Every rule-of-thumb band value additionally carries a `citation` field (source name plus access date) and a `band` field naming the rule.

Required unit vocabulary: `usd_minor_units`, `percent` (scale 4 for rates, scale 2 for band percentages), `percent_annual` (scale 4), `multiple` (scale 2), `ratio` (scale 4), `bp` (scale 1), `months` (scale 1), `count`, `date` (ISO 8601), `sha256_hex`, `status`.

Canonical serialization rules, enforced by the snapshot writer: UTF-8, LF line endings, two-space indent, keys sorted, trailing newline, fixed-scale decimal strings for rates and multiples, integers for cents and counts, no wall-clock fields, no environment or git data. Wall-clock time and git SHA live only in `payloads/build_meta.json`, which is excluded from byte-identity comparison.

Example facility payload (values illustrative, computed from fixture F1 inputs):

```json
{
  "asof": "2026-11-10",
  "seed": 20261110,
  "borrower": {
    "borrower_id": "SYN-0007",
    "last_round_usd": { "value": 1200000000, "unit": "usd_minor_units",
      "provenance": "synthetic seed=20261110 generator v1" },
    "arr_usd": { "value": 450000000, "unit": "usd_minor_units",
      "provenance": "synthetic seed=20261110 generator v1" },
    "net_burn_monthly_usd": { "value": 32000000, "unit": "usd_minor_units",
      "provenance": "synthetic seed=20261110 generator v1" },
    "cash_usd": { "value": 850000000, "unit": "usd_minor_units",
      "provenance": "synthetic seed=20261110 generator v1" },
    "asof": "2026-11-10"
  },
  "sizing": {
    "band_cap_usd": { "value": 600000000, "unit": "usd_minor_units",
      "citation": "Mercury venture debt term sheet guide (accessed 2026-11-10)",
      "band": "facility_sizing_band_20_50_pct_of_last_round" },
    "facility_usd": { "value": 480000000, "unit": "usd_minor_units",
      "provenance": "derived: bisection, runway floor 18 months, DSCR floor 1.15x" },
    "post_draw_runway_months": { "value": "24.6", "unit": "months", "scale": 1,
      "provenance": "derived: (cash minus facility) / net monthly burn" },
    "min_dscr": { "value": "1.2100", "unit": "ratio", "scale": 4,
      "provenance": "derived: min over periods of CFADS / debt service" }
  },
  "structure": {
    "type": "io_then_straight_line",
    "term_months": { "value": 48, "unit": "count" },
    "io_months": { "value": 12, "unit": "count",
      "citation": "Mercury venture debt term sheet guide (accessed 2026-11-10)",
      "band": "interest_only_12_18_months" },
    "base_rate": { "value": "4.7500", "unit": "percent_annual", "scale": 4,
      "source": "Bank of Canada Valet AVG.INTWO", "source_timestamp": "2026-11-07",
      "provenance": "committed snapshot at close" },
    "spread": { "value": "2.0000", "unit": "percent_annual", "scale": 4,
      "citation": "Mercury venture debt term sheet guide (accessed 2026-11-10)",
      "band": "bank_pricing_prime_plus_1_3" },
    "warrant_coverage": { "value": "3.00", "unit": "percent", "scale": 2,
      "citation": "Mercury venture debt term sheet guide (accessed 2026-11-10)",
      "band": "bank_warrant_0_5_pct" }
  },
  "returns": {
    "lender_irr": { "value": "9.1234", "unit": "percent_annual", "scale": 4,
      "provenance": "derived: IRR on dated lender cash flows, ACT/365" },
    "moic": { "value": "1.42", "unit": "multiple", "scale": 2,
      "provenance": "derived: (interest + principal + warrant value) / funded" },
    "total_interest_usd": { "value": 118000000, "unit": "usd_minor_units",
      "provenance": "derived: sum of schedule interest" },
    "warrant_value_usd": { "value": 4500000, "unit": "usd_minor_units",
      "provenance": "derived: option-value model, stated strike assumption" }
  }
}
```

All non-integer decimals in this example serialize as fixed-scale decimal strings; only integer cent and count values serialize as JSON numbers.

Values in this example are illustrative and are not fixture ground truth. Fixture expected values are committed separately by the tester with arithmetic in comments.

## 9. Data sources

| Source | Use | Access | Notes |
|--------|-----|--------|-------|
| Bank of Canada Valet API, short-rate series (candidate codes: AVG.INTWO for CORRA, and the 3-month and 6-month bill series as the floating base rate proxy) | Floating base rate for pricing and rate-shock scenarios | Public JSON, no key | Series codes are unverified until the label assertion runs; the refresh workflow calls the series-detail endpoint and asserts the returned label matches an expected pattern per series, and a mismatch prints the actual label and fails the run |
| Seeded synthetic borrower generator | All borrowers, cash flows, burn paths | Deterministic from one seed | Seed committed in `scenario.toml`; same seed plus same inputs means byte-identical outputs |
| Committed rule-of-thumb bands | Sizing band, IO period, term, spreads, warrant coverage, cap multiple | Local config with citations | Every band carries source name and access date; bands are heuristics, never facts about a specific deal |

Raw Valet responses are committed under `pipeline/data/rates/` with a `SHA256SUMS` manifest. CI reads the committed cache and verifies hashes; it never calls the network. The manual `refresh-rates.yml` workflow re-fetches, re-hashes, and opens a pull request.

## 10. Core module specs

Each module lists its spec and then its acceptance criteria. Every acceptance criterion is testable in CI.

### 10.1 Borrower generator

- Seed 20261110 committed in `config/scenario.toml`. Inputs per borrower: last round size, ARR, average monthly net burn, cash balance, plus a lender-type label (bank or non-bank) and a structure selection.
- Realistic bands (all citable heuristics, recorded in config): last round 10 to 400 million USD; ARR 2 to 150 million USD; monthly net burn 0.5 to 8 million USD; cash 15 to 150 months of burn coverage; a small share of pre-revenue borrowers with ARR 0 and burn only.
- The generator emits a borrower book of 24 synthetic borrowers sorted deterministically by borrower id, each with all inputs carrying unit, provenance, seed, and generator version.
- The generator also emits a per-period monthly revenue path (ARR/12 grown at a seeded per-borrower monthly growth rate from a configured band) and the derived CFADS path.
- The generator emits a strata coverage manifest guaranteeing at least one borrower in each of: infeasible at zero draw, runway-bound, DSCR-bound, pre-revenue (ARR 0), zero-warrant bank borrower, and one RBF shortfall case; CI fails if any required stratum count drops to zero.
- All randomness is drawn from one seeded generator in sorted iteration order; no wall clock anywhere in generated content.

Acceptance: rebuilding twice from the same seed produces byte-identical payloads over `payloads/` (excluding `build_meta.json`); every borrower input falls inside its declared band; a pre-revenue borrower exists in the book with ARR 0; the borrower book hash is stable across two builds; every required stratum in the coverage manifest has a count of at least one.

### 10.2 Sizing engine

- Bisection solve for the maximum facility F in [0, band cap] such that post-draw runway >= runway floor (default 18 months) and minimum DSCR across periods >= DSCR floor (default 1.15x).
- Convergence criterion: the bracket width falls below 1 cent (0.01 USD) or 60 iterations elapse; hitting the iteration limit without converging is a hard failure, not a silent fallback.
- Edge borrowers (ARR 0, burn so high the floor is infeasible) return an explicit infeasible status with the binding constraint named, never a zero-sized facility presented as a solution.
- The solver prints its iteration count and final bracket in the build log.

Acceptance: the solver converges on all 24 borrowers in the book; a hand-computed fixture (F1 in section 11) pins the expected facility to the cent with the arithmetic in the fixture comments; the binding constraint (runway or DSCR) is reported per borrower; an infeasible borrower returns infeasible with a named constraint, asserted by test.

### 10.3 Schedule engine

- Four structures, all on monthly payment periods with business-day-adjusted payment dates (roll forward only, never backward):
  - **Bullet**: interest-only for the full term, principal in one payment at maturity.
  - **IO then straight-line**: interest-only for the IO period (12 to 18 months), then equal monthly principal payments for the remaining periods, plus interest on the declining balance.
  - **RBF with cap**: percent-of-revenue remittance each period until the cap multiple (1.3x to 2.5x) of the advance is repaid; a residual shortfall beyond the stated horizon is reported, never hidden.
  - **Bridge**: short tenor (3 to 6 months), single repayment at maturity with a fee; sized against the same band cap.
- Day count: ACT/365 year fractions from close to each payment date for interest accrual; the convention is declared on the methods page and tested against closed forms (for example, a full non-leap year accrues exactly 365/365 of the annual rate).
- Payment dates are computed from the close date, rolled forward to the next business day if needed, and asserted to be strictly increasing.

Acceptance: every structure's schedule matches a hand-computed closed-form fixture (F2, F3, F4, F5 in section 11) to the cent; interest accrual round trips within 0.01 percent against the closed form; payment dates are strictly increasing and business days in a pinned month; the RBF schedule hits the cap multiple exactly on the cap period for a fixture revenue path.

### 10.4 Pricing engine

- All-in rate = committed base rate snapshot at close + spread from the lender-type band (bank prime plus 1 to 3 percent, non-bank prime plus 3 to 4 percent). The base rate is a committed observation with source and source_timestamp; it is never a forecast.
- Rate-shock scenario: a declared parallel shift of the base rate (default plus 200 bp, configured, not guessed), applied to the same schedules to produce a shocked interest total and shocked IRR.
- Rate sensitivity: facility DV01 or basis-point sensitivity published per structure (change in lender IRR and in total interest per 100 bp move), with the unit `bp` on every published figure.

Acceptance: the all-in rate equals base plus spread exactly at scale 4 for every structure; the rate-shock payload reproduces the hand-computed shocked IRR from fixture F6; the per-100-bp sensitivity matches a recompute from the shocked and base schedules within the published tolerance; no pricing figure appears anywhere without its base rate source and timestamp; a build-time band-excursion check flags any all-in rate outside the cited total-rate band (5 to 8 percent bank, 10 to 14 percent non-bank) and reports the excursion, with bands restated as short rate plus spread and the restatement disclosed on the methods page.

### 10.5 Returns analytics

- Lender IRR per structure, per scenario (base and downside), from dated cash flows: funding outflow, interest receipts, principal receipts, and warrant value at exit, under ACT/365.
- MOIC, total interest, and warrant value published per structure. Warrant value uses the stated option-value model and strike assumption; the assumption is disclosed on the methods page.
- Downside scenario: burn 50 percent higher than generated, revenue path 30 percent lower, both configured; the downside IRR and the covenant headroom under downside are published side by side with base.
- Lender cash flows include the closing fee received at funding (band 0.5 to 2 percent of facility amount); MOIC denominates on gross facility amount, stated on the methods page.
- Downside transmission: under downside the RBF remittances, covenant headroom, first-breach month, and warrant value change, while contractual term-loan cash flows and IRR are invariant by construction, stated on the methods page.

Acceptance: IRR recomputed by the independent verify gate from the same schedules matches within 0.0001 percent; MOIC equals the closed-form ratio for fixture F2; every returns figure carries unit, source, and asof; a structure with no warrant prints warrant value 0 with an explicit not granted status, never a missing field.

### 10.6 Structure comparison table

- One table comparing bridge, IO-then-straight-line term, and RBF on identical borrower inputs (same borrower, same close date, same base rate snapshot, same band config).
- Columns: structure, facility amount, all-in rate, term, IO months, total interest, closing fee, lender IRR, MOIC, min DSCR, binding constraint, first-breach month under downside, warrant coverage, and the rule-of-thumb citation for each band used.
- Illustrative structuring rationale notes render beneath the table as teaching commentary with citations, never as a recommendation.
- The table is the headline of the structures page and is computed from the same payloads the verify gate checks.

Acceptance: all three structures share byte-identical borrower inputs and base rate source in the payload (asserted by test); the table renders from committed payloads with zero browser arithmetic; every band cell carries its citation; the binding constraint column is populated for all three rows.

### 10.7 Methods and controls report

A daily-style methods page (canonical text in `docs/methods.md`) enumerating every convention, every rule-of-thumb citation, and every limitation:

- Conventions: ACT/365 day count, business-day roll forward only, monthly periods, integer cents with single rounding at the metric boundary and ROUND_HALF_UP, IRR bisection tolerance, DSCR floor 1.15x reference, runway floor 18 months, band caps and their sources with access dates.
- Rule-of-thumb citations: the full table from section 5 with source names and access dates.
- Data disclosure: all borrowers are synthetic from a seeded generator; base rates are real Bank of Canada observations used only as a rate level anchor; no facility in this repository is real.
- Limitations: single-draw sizing in v1, milestone tranching capped but not simulated as contingent, EBITDA covenants marked not applicable for pre-profit borrowers, warrant value is a stated-assumption model not a market quote, no collateral or recovery modeling, no tax or accounting treatment.
- Prohibited claims: repeat the section 2 list verbatim in prose.

Acceptance: every convention in section 5 appears on the methods page; a documentation test asserts the DSCR reference, the sizing band, both pricing bands, and the cap multiple appear with citations in the text; no prohibited claim appears anywhere in the repository.

## 11. Verification culture

The bar is the independent verify gate pattern, restated in full so this repository matches the house standard:

1. Hand-computed golden fixtures: the five structure-branch fixtures required by this spec (bullet facility, IO-then-amortizing facility, RBF with cap, bridge, rate-shock case) plus the sizing convergence fixture required by section 10.2, six fixtures total: F1 sizing bisection, F2 bullet schedule, F3 IO-then-straight-line schedule, F4 RBF with cap, F5 bridge schedule, F6 rate-shock IRR. A human computes the expected outputs and writes both the numbers and the arithmetic into the fixture file as comments, so the origin of every expected value is auditable.
2. An independent verify gate that shares no code with the engine. It recomputes every published figure (IRR, MOIC, DSCR, schedule balances, sensitivity) straight from the raw inputs (committed rates, seed, borrower book) using straightforward naive implementations, so a shared bug cannot hide behind a shared implementation. A CI check asserts the verify package imports nothing from the engine package.
3. Non-zero exit on drift. The gate exits non-zero on any payload mismatch, fixture mismatch, schema or unit violation, hash mismatch, or banned dash character.
4. Deterministic rebuilds. Two consecutive builds from unchanged inputs produce byte-identical payload directories, excluding `build_meta.json`.
5. The gate prints the artifact SHA256 for every payload file, recorded as printed rather than claimed as reverified, and the verification page displays those digests.
6. A dash-character scan over the whole tree as part of the gate (the ASCII double-hyphen exemption table for source-code flags is pinned in docs/architecture.md during phase 1; Unicode dashes are banned repo-wide with no exemptions).
7. Fixtures must be committed before the corresponding engine module is written and must never be regenerated from engine output; this is a code-review checklist item.

Fixture expected values for F1 through F6 are computed by the tester during phase 1 with the arithmetic written into the fixture files as comments; the spec pins inputs and required coverage, not the arithmetic result, so no transcription error in this document becomes ground truth.

## 12. Site spec

Routes: index, sizing, structures, returns, methods, verification, dashboard, case study.

Five tasks the site must support within one minute:

| Task | Route and element |
|------|-------------------|
| How big a facility can this borrower service | Sizing page, facility amount with binding constraint, runway and DSCR floor inputs, band cap with citation |
| Which structure should the lender choose | Structures page, comparison table on identical inputs, headline block |
| What does the lender earn per structure | Returns page, IRR, MOIC, interest, warrant value, base versus downside side by side |
| What are the conventions and what is illustrative | Methods page, every convention, citation, and limitation |
| Did anything drift | Verification page, verify gate status, artifact SHA256, fixture status |

Cross-cutting: base versus downside delta on every headline number, deep links via `?asof=YYYY-MM-DD` over the payload days, Open Graph preview image, phone-readable landing page, footer stamp of snapshot date, commit SHA, and checks passed (the SHA and wall clock come from `build_meta.json`, so payload byte-identity is preserved). Zero arithmetic in the browser: loaders validate schema and format values, never compute them. The framing line from section 4 on every page.

Two further surfaces ship with the site: a book dashboard over the 24 borrowers (capacity distribution, binding-constraint mix, infeasible count) and a case study route or section walking one borrower end to end. Charts render committed payload values with formatting only, zero browser arithmetic. The sizing page floor inputs display configured values only (no interactive recomputation); the borrower selector filters committed payloads client side.

## 13. CI and deploy gates

All gates blocking. A failing blocking gate keeps the last good deploy live.

| Gate | What it proves |
|------|----------------|
| Sizing bisection convergence fixture (F1) passes and the solver converges on all 24 borrowers | The debt capacity solve is right and converges |
| Schedule closed-form round trips (F2, F3, F4, F5) pass to the cent | Every structure branch is right |
| IRR recompute within 0.0001 percent tolerance by the independent verify gate | Returns analytics are right |
| Rate-shock fixture (F6) passes | Pricing and sensitivity are right |
| Byte-identical rebuild: two consecutive builds diff clean over `payloads/` excluding `build_meta.json` | Determinism holds |
| Payload schema valid: every numeric field has a unit, plus provenance and timestamp, plus citation on every band value | The data contract holds |
| Valet series label assertion passes (or fails loudly printing the real label in the refresh workflow) | The rate source is what the code claims |
| Verify gate recompute matches committed payloads, exits non-zero on drift | Independent verification holds |
| Verify package imports nothing from the engine package | Independence is structural, not aspirational |
| Band excursion check reports any all-in rate outside the cited total band | Pricing stays inside the cited rule-of-thumb bands, or the excursion is reported instead of hidden |
| Strata manifest: every required borrower stratum count is at least one | The committed book covers every required structural case, so no coverage silently disappears |
| Banned dash character scan: no em dash (U+2014), no en dash (U+2013), no other Unicode dash, no ASCII double hyphen outside table separator rows (the ASCII double-hyphen exemption table for source-code flags is pinned in docs/architecture.md during phase 1; Unicode dashes are banned repo-wide with no exemptions) | The repository-wide writing convention holds |

## 14. Phase plan and acceptance criteria

| Phase | Output | Acceptance criteria |
|-------|--------|---------------------|
| 0 | Repo bootstrap: git init, .gitignore, MIT LICENSE, AGENTS.md, spec amended with decisions log, docs skeleton | Repository commits cleanly; spec shows every approved decision in the decisions log |
| 1 | Domain model: money, dates, rule-of-thumb band config with citations; six golden fixtures F1 to F6 committed with hand-computed arithmetic; verify skeleton (dash scan, import isolation, fixture runner) | All six fixture files committed with inputs, expected values, and arithmetic in comments before any engine module exists; money round trip idempotent; ACT/365 accrual matches closed form; dash scan runs |
| 2 | Seeded borrower generator with revenue and CFADS paths and strata manifest, committed rate snapshot with manifest and label assertion, pricing engine, bullet and IO-then-straight-line schedules, sizing engine | Two builds from seed 20261110 byte-identical over `payloads/`; all 24 borrowers sized or explicitly infeasible; F1 convergence fixture passes to the cent; Valet label assertion passes or fails loudly with the real label printed. Kill-criterion gate: bisection proves convergence against the hand-computed fixture |
| 3 | RBF and bridge schedules, returns analytics with fees and warrants, structure comparison table, payloads, site shell with index, sizing, and structures pages | F2 to F5 pass to the cent; all-in rate equals base plus spread exactly; IRR and MOIC recomputed within tolerance; comparison table, charts, and rationale notes render from committed payloads on a live site with the framing line on every page; a usable artifact exists from the end of this phase |
| 4 | Covenant package with first-breach flags, downside and rate-shock scenarios with sensitivity, book dashboard, methods page, returns page | DSCR floor and headroom published per period; first-breach month published per structure under downside; EBITDA covenants marked not applicable for pre-revenue borrowers with a stated reason; F6 rate-shock fixture passes; dashboard renders the 24-borrower book; methods page covers every section 5 convention and citation |
| 5 | Full verify gate: naive recompute, import isolation check, SHA256 printout, verification page, all CI workflows blocking, case study walkthrough, README with screenshots, attribution tests | Gate exits non-zero on an introduced drift (demonstrated in a test); planted em dash caught by the scan; import isolation check green; two-build byte-identity green in CI; every CI gate in section 13 runs and blocks; case study renders; attribution present on methods page, README, and site footer |

## 16. Agent assignments

| Work | Agent |
|------|-------|
| Data contracts, module interfaces, phase breakdown, scenario config | 02-tech-lead |
| Money, dates, generator, sizing, schedules, pricing, returns, covenants | 03-backend-dev |
| Golden fixtures with committed arithmetic, convergence and round-trip tests, fixture generation, drift demonstrations | 06-tester |
| Numerical and code review before each phase commit; verify gate independence review | 04-code-reviewer |
| Methods page, README, attribution, limitations, prohibited-claim sweep | 09-documentation |
| Commits and release tags | 10-git |
| Site routes, comparison table, status chips, telephone layout | 11-frontend-dev (load frontend-design first) |

## 17. Risk register

| Risk | Severity | Mitigation |
|------|----------|------------|
| Wrong day-count or payment convention published under the candidate's name | High | Five hand-computed golden fixtures with visible arithmetic and cited convention sources, closed-form round-trip tests, independent verify gate, fail-closed gates |
| Bisection non-convergence on edge borrowers | High | Iteration limit is a hard failure with the bracket printed, infeasible status with named binding constraint, convergence fixture as a blocking CI gate, kill criterion in section 18 |
| Rule-of-thumb bands presented as facts without citation | High | Citation field required on every band value by schema validation, methods page table with access dates, documentation test asserting citations appear in prose |
| Rate data staleness | Medium | Committed snapshot with source_timestamp, staleness banner on the site, manual refresh workflow, no forecast language anywhere |
| Synthetic borrower book reading as toy | Medium | Real BoC rate anchor, realistic band ranges with citations, prominent synthetic disclosure on every page footer and the methods page, verification depth as the engineering story |
| Compliance optics of publishing sizing content | Medium | Framing line on every page, illustrative sizing not credit approval, no real data, no forecasts, prohibited-claim list enforced by a documentation test |
| Scope creep delays the first public release | High | Kill criteria below; the site goes live at the end of phase 3 |
| Feature bundle delays first public release | Medium | Features are distributed into phases 3 to 5 with a fixed cut-line; the site still goes live at the end of phase 3 |

## 18. Kill criteria

- If the bisection solver cannot prove convergence against the hand-computed fixture by the end of phase 2, ship a fixed-band sizing table (the 20 to 50 percent band applied directly, honestly labeled as a band lookup, not a solver) with the solver deferred.
- If any schedule branch (bullet, IO-then-straight-line, RBF, bridge) cannot match its closed-form fixture to the cent by the end of phase 3, drop that branch from v1, disclose the omission on the methods page, and ship the remaining branches.
- If the site is not publicly live by the end of day 6 of agentic work, ship the repository with markdown reports and defer the site.
- If the case study or dashboard slips past phase 5, ship the remaining features and record the omission in the methods page limitations.

## 19. Attribution requirements

Confirm exact wording from each licence before publishing and place attribution on the methods page, the repository README, and the site footer.

- Bank of Canada Valet data (the chosen short-rate series, named exactly): Open Government Licence Canada, naming the series.
- Rule-of-thumb bands: cite Mercury venture debt term sheet guide, CVCA venture debt market analysis, and the SBA DSCR reference by name with access dates, as the sources of the bands. These are industry guides, not regulators, and the citation is definitional, not an endorsement.
- Synthetic borrowers: no attribution needed, but every surface that shows them must label them synthetic.
- Project licence: MIT for the code, stated in the README.

## 20. Effort estimate

| Phase | Days |
|-------|------|
| 0 | 0.25 |
| 1 | 1.0 |
| 2 | 1.25 |
| 3 | 1.75 |
| 4 | 1.5 |
| 5 | 1.25 |

Phases 0 to 5 total the core build: 0.25 + 1.0 + 1.25 + 1.75 + 1.5 + 1.25 = 7.0 agentic days with the feature bundle. Phases 1 to 3 still produce a publicly live artifact at the end of phase 3. If time runs short, the cut-line is: charts and dashboard compress first, the case study never cuts.

## 21. Draft resume bullets

Three drafts, following the house conventions: strong past-tense action verb, specific numbers, no em dashes. Every number below is derivable from the acceptance criteria in this spec. Before use, re-check each number against the actual CI logs, per the no-fabrication rule, and record the final wording in the master project database first.

1. Engineered a venture debt sizing and returns engine in Python that solves maximum facility size by bisection under an 18-month post-draw runway floor and a 1.15x DSCR floor across 24 seeded synthetic borrowers, with every sizing band traceable to a cited market rule of thumb and a hand-computed convergence fixture as a blocking CI gate.
2. Built four venture debt schedule structures (bullet, 12-month IO then straight-line, revenue-based with 1.3x to 2.5x cap multiple, and bridge) with ACT/365 day count and business-day payment dates, each validated against hand-computed closed-form fixtures to the cent.
3. Delivered lender IRR, MOIC, and rate-shock sensitivity analytics across all structures with an independent verify gate that recomputes every figure from schedules sharing no code with the engine, exits non-zero on drift, and produces byte-identical rebuilds on unchanged inputs.

## 22. Interview mapping

Each module maps to a duty of the target posting family (RBCx-style Analyst, Credit Advisory; TDIP-style capital solutions associate) or to a typical venture debt interview question. Duty phrasing is paraphrased from the 2026-11-10 credit advisory research; re-check live postings at application time rather than quoting this table.

| Project module | JD duty or interview question | How the module answers |
|----------------|-------------------------------|------------------------|
| Domain model and rule-of-thumb table | "What is venture debt and how is a facility sized?" | Sizing band 20 to 50 percent of last round, IO period, term, pricing and warrant bands, each with its citation and band value in the payload |
| Sizing engine | "How do you size a facility a burn-rate startup can service?" | Bisection solve under a runway floor and DSCR floor, binding constraint named per borrower, hand-computed convergence fixture |
| Schedule engine | "Walk me through an amortization schedule with an interest-only period." | Bullet, IO-then-straight-line, RBF with cap, and bridge branches, each tested against closed forms with ACT/365 stated |
| Pricing engine | "How would you price a floating-rate facility and stress it?" | Base rate from a committed public snapshot plus spread bands by lender type, rate-shock scenario, per-100-bp sensitivity in published units |
| Returns analytics | "What return does the lender earn on this structure?" | Lender IRR on dated cash flows, MOIC, interest plus warrant value, base versus downside, recomputed independently |
| Structure comparison table | "Would you structure this as a bridge, a term loan, or revenue-based financing, and why?" | Three structures on identical borrower inputs with IRR, MOIC, DSCR, and binding constraint side by side, every band cited |
| Covenant package | "What covenants would you put on a pre-revenue borrower, and what does DSCR mean?" | Net leverage, interest cover, minimum cash, DSCR floor 1.15x reference, headroom per period, EBITDA covenants marked not applicable with a stated reason |
| Methods page and verify gate | "How do you keep numbers trustworthy?" "Describe your controls mindset." | Independent recompute sharing no code with the engine, non-zero exit on drift, dash scan, artifact hashing, enumerated limitations |
| Rate data discipline | "Where do your rate assumptions come from?" | Committed Bank of Canada snapshot with SHA256 manifest, label assertion that fails loudly printing the real label, no forecast anywhere |
| Site and comparison page | "Tell me about a project end to end." | One URL showing the sizing answer first, then structures, returns, methods, and verification, with every assumption and limit stated |
