"""Canonical payload build orchestration (architecture P2.6, P3.6, P4.2).

run_build assembles payloads/<asof>/book.json,
payloads/<asof>/comparison.json, and payloads/<asof>/scenarios.json: the
committed Valet rate snapshot, the seeded 24-borrower book, per-borrower
pricing, sizing, structure schedules, returns blocks, the three-row
structure comparison tables, the phase 4 covenant and scenario summary
blocks, the dashboard book summary, and the per-period scenario detail,
all validated with snapshot.validate_payload and written through
snapshot.write_payload. asof is the rate snapshot observation date, never
the wall clock, so all three payload files are byte identical across runs;
payloads/build_meta.json (wall clock, git SHA, Python version) is the only
build output that may differ between two runs and it sits outside the asof
directory, excluded from byte identity by definition.

Sizing runs on the canonical io-then-straight-line serviceability schedule
for every borrower (io_months, term = io + 36, decisions entry "Canonical
serviceability schedule for sizing"). Borrowers whose generator path is
shorter than that canonical term (rbf_capped at the 24 period horizon and
bridge at the pinned tenor) extend the CFADS path with the generator's own
growth formula, so no borrower is sized on invented cash flows and no
pre-revenue borrower is charged DSCR on zero CFADS.

Every feasible borrower publishes a kind-tagged structure schedule: bullet
and io_then_straight_line through schedule.build_schedule, bridge through
schedule.build_bridge_schedule, and rbf_capped through
schedule.build_rbf_schedule as kind "rbf" with cap, final cumulative,
residual, and status. A borrower whose sizing is infeasible has no facility
to amortize and publishes the not_applicable shape with the sizing reason
(the phase 2 rbf_capped and bridge placeholder reason is retired). Each
borrower also gains a returns block: status, lender IRR, MOIC, total
receipts, closing fee, warrant leaf, and the dated lender cash flows. The
build then writes comparison.json with one shared block plus three
comparison rows for each of the 24 borrowers.

Phase 4 additions (architecture P4.1, P4.2, P4.6): every borrower gains a
covenants block (base and downside packages over the drawn structure plus
the downside borrower-level first-breach leaf) and a scenarios block
(base, downside, published deltas, and the P4.3 rate-shock block); meta
gains book_summary with the dashboard capacity bins and binding-constraint
mix; comparison rows carry their own downside first-breach cell in place of
the retired placeholder; and scenarios.json carries the per-period
covenant rows, cash paths, and scenario schedule rows for the drawn
structure plus the three comparison structures.

The strata manifest is computed with every sizing result wired in
(adapting sizing.SizingResult to the lightweight generator SizingResult the
predicates consume), and any stratum count below one fails the build with
the stratum named.

Every numeric leaf in book.json, comparison.json, and scenarios.json is a
metric envelope per architecture section 6.4: drawn generator inputs carry
the generated provenance class plus their band citation and band label,
derived values carry a derived provenance and asof, and every rate figure
in the pricing, rates, and returns blocks carries the snapshot source and
source_timestamp (external provenance).

Failures are loud: rate manifest and label problems, solver
non-convergence, zero stratum counts, and schema validation errors raise
(or surface as) exceptions, and the CLI maps any of them to a non-zero exit
status. Standard library plus vdebt engine modules only; no network access.
The only clock read in this module feeds build_meta.json.
"""

import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from vdebt.compare import (
    INFEASIBLE_REASON,
    ROW_STRUCTURES,
    STRUCTURE_BRIDGE,
    STRUCTURE_IO,
    STRUCTURE_RBF,
    ComparisonRow,
    SharedInputs,
    build_comparison,
)
from vdebt.config import Band, ScenarioConfig, load_scenario
from vdebt.covenants import (
    CovenantPeriod,
    CovenantResult,
    CovenantSpec,
    evaluate_package,
)
from vdebt.generator import (
    MONTHS_PER_YEAR,
    STRATA_NAMES,
    Borrower,
    BorrowerBook,
    compute_strata_manifest,
    generate_book,
)
from vdebt.generator import (
    SizingResult as StrataSizingResult,
)
from vdebt.money import MONEY_CONTEXT, format_fixed, quantize
from vdebt.pricing import (
    STATUS_EXCURSION,
    Pricing,
    price,
)
from vdebt.rates import PERCENT_ANNUAL_SCALE, RateSnapshot, load_rate_snapshot
from vdebt.returns import (
    IRRResult,
    lender_cash_flows,
    solve_irr,
    warrant_value_cents,
)
from vdebt.returns import (
    moic as receipts_moic,
)
from vdebt.scenarios import (
    RateShockBlock,
    ScenarioBlock,
    ScenarioDeltas,
    assemble_scenarios,
    build_rate_shock,
    build_scenario_covenant_inputs,
    downside_burn,
    downside_cfads,
    downside_revenue,
)
from vdebt.schedule import (
    RbfRow,
    RbfSchedule,
    Schedule,
    ScheduleRow,
    build_bridge_schedule,
    build_rbf_schedule,
    build_schedule,
    closing_fee_cents,
)
from vdebt.sizing import SizingInputs, SizingResult, size_facility
from vdebt.snapshot import envelope, validate_payload, write_payload

# Repository layout anchors, resolved from this file so the build runs from
# any working directory (the byte-identity test drives subprocess builds).
PIPELINE_DIR: Path = Path(__file__).resolve().parents[2]
REPO_ROOT: Path = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH: Path = PIPELINE_DIR / "config" / "scenario.toml"
DEFAULT_RATES_DIR: Path = PIPELINE_DIR / "data" / "rates"
DEFAULT_OUTPUT_ROOT: Path = REPO_ROOT / "payloads"

# Canonical artifact names (architecture P2.6 layout decision, P3.6 second
# file, P4.2 third file).
BOOK_FILE_NAME: str = "book.json"
COMPARISON_FILE_NAME: str = "comparison.json"
SCENARIOS_FILE_NAME: str = "scenarios.json"
BUILD_META_FILE_NAME: str = "build_meta.json"

# The schedule not_applicable shape now appears only when sizing is
# infeasible (architecture P3.6): the phase 2 rbf_capped and bridge
# placeholder reason is retired with the kind-tagged schedule union.
# compare.INFEASIBLE_REASON ("sizing infeasible so no facility exists to
# fund") is imported as the shared reason for infeasible returns blocks and
# comparison cells.
INFEASIBLE_SCHEDULE_REASON: str = "facility sizing infeasible so no facility exists to amortize"

# DSCR is undefined for a borrower with zero ARR and zero CFADS; the payload
# publishes not_applicable with this reason, never a fabricated zero.
PRE_REVENUE_DSCR_REASON: str = (
    "pre-revenue borrower, ARR and CFADS are zero so DSCR is undefined"
)

# Structures with a phase 2 schedule payload; rbf_capped and bridge publish
# the pinned not_applicable status instead (architecture P2.6).
_SCHEDULED_STRUCTURES: frozenset[str] = frozenset(
    {"bullet", "io_then_straight_line"}
)

# Derived provenance strings. Each names the derivation literally so the
# methods page can quote them verbatim (section 6.4 derived class).
_DERIVED_CASH: str = "derived: cash coverage months times monthly net burn"
_DERIVED_TERM_AMORTIZING: str = "derived: io_months plus the pinned amortizing periods"
_DERIVED_TERM_RBF: str = "derived: pinned RBF horizon"
_DERIVED_TERM_BRIDGE: str = "derived: pinned bridge tenor"
_DERIVED_REVENUE_PATH: str = "derived: ARR divided by 12 grown at the monthly growth rate"
_DERIVED_CFADS_PATH: str = "derived: revenue times the gross margin percent"
_DERIVED_BOOK_HASH: str = "derived: sha256 of the canonical generator document"
_DERIVED_STRATA: str = "derived: strata predicate over the book and sizing results"
_DERIVED_RUNWAY: str = "derived: (cash minus facility) / net monthly burn"
_DERIVED_MIN_DSCR: str = "derived: min over periods of CFADS / debt service"
_DERIVED_BAND_CAP: str = "derived: 50 percent sizing band cap applied to last round"
_DERIVED_SCHEDULE_PERIOD: str = "derived: schedule period index"
_DERIVED_SCHEDULE_DATE: str = "derived: roll_forward of add_months from the close date"
_DERIVED_SCHEDULE_OPENING: str = "derived: opening balance carried from the prior closing balance"
_DERIVED_SCHEDULE_INTEREST: str = "derived: ACT/365 interest quantized on the opening balance"
_DERIVED_SCHEDULE_PRINCIPAL_BULLET: str = "derived: bullet principal repaid at maturity"
_DERIVED_SCHEDULE_PRINCIPAL_IO: str = "derived: straight-line principal allocation"
_DERIVED_SCHEDULE_CLOSING: str = "derived: opening balance minus principal"
_DERIVED_SCHEDULE_STATUS: str = "derived: structure schedule build decision"
_DERIVED_BRIDGE_PRINCIPAL: str = "derived: bridge principal repaid at maturity"
_DERIVED_RBF_CAP: str = "derived: RBF cap multiple times the advance"
_DERIVED_RBF_REVENUE: str = "derived: RBF period revenue from the generator path"
_DERIVED_RBF_REMITTANCE: str = (
    "derived: RBF remittance rate times period revenue, capped at the residual"
)
_DERIVED_RBF_CUMULATIVE: str = "derived: running sum of quantized RBF remittances"
_DERIVED_RBF_FINAL_CUMULATIVE: str = "derived: sum of the published RBF remittances"
_DERIVED_RBF_RESIDUAL: str = "derived: RBF cap minus the final cumulative remittance"
_DERIVED_RBF_STATUS: str = "derived: RBF cap reached inside the horizon or not"
_DERIVED_RETURNS_STATUS: str = "derived: returns computed over the structure schedule"
_DERIVED_TOTAL_INTEREST: str = "derived: sum of schedule period interest"
_DERIVED_RBF_TOTAL_INTEREST: str = "derived: RBF carries no interest term"
_DERIVED_RECEIPTS: str = "derived: schedule receipts, interest plus principal or remittances"
_DERIVED_CLOSING_FEE: str = "derived: closing fee percent times the facility"
_DERIVED_MOIC: str = "derived: (total receipts plus warrant value) / gross facility"
_DERIVED_IRR: str = "derived: Decimal bisection of NPV over the dated lender cash flows"
_DERIVED_CASH_FLOW_DATE: str = "derived: lender cash flow date at close or a payment date"
_DERIVED_CASH_FLOW: str = "derived: dated lender cash flow aggregated per date"
_DERIVED_CLOSE: str = "derived: close date from the committed rate snapshot observation date"
_DERIVED_IO_NA: str = "derived: structure carries no interest-only period"
_DERIVED_FIRST_BREACH: str = "derived: first period index whose covenant headroom is negative"
_DERIVED_NO_BREACH: str = "derived: no breach through the horizon"
_DERIVED_COVENANT_LIMIT: str = "derived: configured covenant limit from scenario.toml"
_DERIVED_DSCR_LIMIT: str = "derived: configured sizing DSCR floor from scenario.toml"
_DERIVED_MIN_CASH_LIMIT: str = "derived: min cash floor months times the scenario net burn"
_DERIVED_COVENANT_STATUS: str = "derived: covenant headroom test against the configured limit"
_DERIVED_MIN_HEADROOM: str = "derived: minimum covenant headroom across the tested periods"
_DERIVED_PERIOD_ACTUAL: str = "derived: covenant actual over the period paths"
_DERIVED_PERIOD_HEADROOM_MAX: str = "derived: headroom = limit minus actual (max type)"
_DERIVED_PERIOD_HEADROOM_MIN: str = "derived: headroom = actual minus limit (min type)"
_DERIVED_CASH_PATH: str = "derived: prior period cash minus net burn minus debt service"
_DERIVED_SCENARIO_DELTA: str = "derived: downside minus base at the published scale"
_DERIVED_BIN_EDGES: str = "derived: capacity bin edges from scenario.toml"
_DERIVED_BOOK_COUNT: str = "derived: count of sizing outcomes over the committed book"

# Stated covenant assumption citation (P4.1): the three covenant limits that
# are not band draws publish this citation beside their configured value.
STATED_COVENANT_CITATION: str = (
    "Venture Debt Engine stated covenant assumption, not a market quote "
    "(accessed 2026-11-12)"
)

# Published scale for the months first-breach value (P4.1: months scale 1).
_FIRST_BREACH_SCALE: int = 1

# Published scales for the scenario delta leaves (P4.2: bp and multiple).
_BP_SCALE: int = 1
_MULTIPLE_SCALE: int = 2

# The three binding constraint values sizing can publish (sizing module).
_BINDING_CONSTRAINTS: tuple[str, ...] = ("runway", "dscr", "band_cap")

# Published ratio scale for DSCR cells (section 6.4 metric scales).
_DSCR_SCALE: int = 4

# Published scale for covenant ratio limits, actuals, and headrooms (the
# ratio metric scale from section 6.4; covenants.RATIO_SCALE pins the same
# value at the engine boundary).
_RATIO_SCALE: int = 4


class BuildError(RuntimeError):
    """Hard build failure: strata coverage or schema validation violation.

    Rate manifest and label failures surface as RateError and solver
    non-convergence as SolverError; both propagate out of run_build the same
    way, and the CLI maps every one of them to a non-zero exit status.
    """


def _generated_provenance(cfg: ScenarioConfig) -> str:
    """Return the pinned generated provenance string for cfg's seed.

    The exact form "synthetic seed=<seed> generator <version>" is what
    snapshot's generated provenance class matches (architecture section 6.4),
    so the seed and generator version travel on every drawn generator input.
    """
    return f"synthetic seed={cfg.seed} generator {cfg.generator_version}"


def _sizing_provenance(cfg: ScenarioConfig) -> str:
    """Return the derived provenance string for sizing outcomes."""
    return (
        f"derived: bisection, runway floor {cfg.runway_floor_months} months, "
        f"DSCR floor {cfg.dscr_floor}"
    )


def _warrant_provenance(cfg: ScenarioConfig) -> str:
    """Return the warrant provenance naming the fixed factor model (P3.4).

    The string names the stated-assumption model and quotes the pinned
    valuation factor, so the methods page can render it verbatim beside
    every warrant value.
    """
    return (
        "derived: warrant fixed factor model, coverage times facility times "
        f"valuation factor {cfg.warrant_valuation_factor}, stated assumption"
    )


def _warrant_downside_provenance(cfg: ScenarioConfig) -> str:
    """Return the downside warrant revaluation provenance (P4.2).

    The string names the pinned standalone revaluation rule and quotes the
    configured downside factor, so the methods page can render it verbatim
    beside the downside warrant figure.
    """
    return (
        "derived: warrant fixed factor model revalued at the downside factor "
        f"{cfg.downside.warrant_factor}, stated assumption, standalone scenario figure"
    )


def _require_band(cfg: ScenarioConfig, name: str) -> Band:
    """Return the required band or fail the build naming the missing table."""
    band = cfg.bands.get(name)
    if band is None:
        raise BuildError(f"scenario.toml is missing required band [bands.{name}]")
    return band


def _band_kwargs(band: Band) -> dict[str, object]:
    """Return the citation and band keys every band-sourced envelope carries."""
    return {"citation": band.citation, "band": band.band}


def _sizing_term_months(borrower: Borrower, cfg: ScenarioConfig) -> int:
    """Return the canonical sizing term: io_months plus amortizing periods.

    Every borrower is sized on the canonical io-then-straight-line schedule
    (decisions entry "Canonical serviceability schedule for sizing"), so the
    term is io + 36 regardless of the structure draw and always satisfies
    io_months < term_months.
    """
    return borrower.io_months + cfg.generator.term_amortizing_periods


def _sizing_cfads(borrower: Borrower, sizing_term_months: int) -> tuple[int, ...]:
    """Return the CFADS path over the canonical sizing term.

    Amortizing borrowers match by construction (their generator path already
    spans io + 36 periods). rbf_capped and bridge paths are shorter than the
    canonical term, so the tail extends the generator's own formula: one
    quantize per period on ARR / 12 grown by the monthly growth rate times
    the gross margin, exactly as vdebt.generator builds the published path.
    Pre-revenue borrowers have arr 0, so their extension stays all zero cents.
    """
    path = borrower.cfads_path
    if len(path) == sizing_term_months:
        return path
    if len(path) > sizing_term_months:
        raise BuildError(
            f"{borrower.borrower_id}: generator CFADS path length {len(path)} "
            f"exceeds the canonical sizing term {sizing_term_months}"
        )
    extended = list(path)
    base = MONEY_CONTEXT.divide(Decimal(borrower.arr_cents), Decimal(MONTHS_PER_YEAR))
    growth_fraction = MONEY_CONTEXT.divide(borrower.monthly_growth, Decimal(100))
    factor = MONEY_CONTEXT.add(Decimal(1), growth_fraction)
    for period in range(len(path) + 1, sizing_term_months + 1):
        grown = MONEY_CONTEXT.power(factor, Decimal(period - 1))
        revenue = int(quantize(MONEY_CONTEXT.multiply(base, grown), 0))
        cfads = int(
            quantize(
                MONEY_CONTEXT.divide(
                    MONEY_CONTEXT.multiply(Decimal(revenue), borrower.gross_margin),
                    Decimal(100),
                ),
                0,
            )
        )
        extended.append(cfads)
    return tuple(extended)


def _band_cap_cents(borrower: Borrower, cfg: ScenarioConfig) -> int:
    """Return the sizing band cap: max_pct_of_last_round percent of last round.

    bands.facility_sizing.max is the pinned 50 percent cap; the product is
    quantized to integer cents exactly once at this storage boundary
    (section 14: hi = band cap in cents).
    """
    band = _require_band(cfg, "facility_sizing")
    raw = MONEY_CONTEXT.divide(
        MONEY_CONTEXT.multiply(Decimal(borrower.last_round_cents), band.max),
        Decimal(100),
    )
    return int(quantize(raw, 0))


def _sizing_inputs(
    borrower: Borrower,
    pricing: Pricing,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
) -> SizingInputs:
    """Assemble SizingInputs from the borrower, pricing, and rate snapshot.

    close is the snapshot observation date (the book close date), the CFADS
    path spans the canonical sizing term, all_in_rate comes from pricing, and
    the band cap is the quantized 50 percent of last round in cents.
    """
    sizing_term = _sizing_term_months(borrower, cfg)
    return SizingInputs(
        cash_cents=borrower.cash_cents,
        net_burn_cents=borrower.net_burn_cents,
        cfads_cents=_sizing_cfads(borrower, sizing_term),
        close=snapshot.observation_date,
        term_months=sizing_term,
        io_months=borrower.io_months,
        all_in_rate=pricing.all_in_rate,
        band_cap_cents=_band_cap_cents(borrower, cfg),
    )


def _inputs_document(
    borrower: Borrower, cfg: ScenarioConfig, asof: str
) -> dict[str, object]:
    """Publish every generator input as an envelope with unit and provenance.

    Drawn values carry the generated provenance class plus their band's
    citation and band label. cash and term_months are derived values: they
    carry a derived provenance with no band keys, because a derived value is
    not itself a rule-of-thumb draw (the bands still gate the values they
    derive from, at the generator boundary).
    """
    generated = _generated_provenance(cfg)
    pricing_band = _require_band(
        cfg, "pricing_bank" if borrower.lender_type == "bank" else "pricing_nonbank"
    )
    if borrower.structure in _SCHEDULED_STRUCTURES:
        term_provenance = _DERIVED_TERM_AMORTIZING
    elif borrower.structure == "rbf_capped":
        term_provenance = _DERIVED_TERM_RBF
    else:
        term_provenance = _DERIVED_TERM_BRIDGE
    return {
        "last_round": envelope(
            borrower.last_round_cents,
            "usd_minor_units",
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "last_round")),
        ),
        "arr": envelope(
            borrower.arr_cents,
            "usd_minor_units",
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "arr")),
        ),
        "net_burn": envelope(
            borrower.net_burn_cents,
            "usd_minor_units",
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "net_burn")),
        ),
        "cash": envelope(
            borrower.cash_cents,
            "usd_minor_units",
            provenance=_DERIVED_CASH,
            asof=asof,
        ),
        "cash_coverage_months": envelope(
            format_fixed(borrower.cash_coverage_months, 1),
            "months",
            scale=1,
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "cash_coverage_months")),
        ),
        "monthly_growth": envelope(
            format_fixed(borrower.monthly_growth, 4),
            "percent",
            scale=4,
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "revenue_growth_monthly")),
        ),
        "gross_margin": envelope(
            format_fixed(borrower.gross_margin, 2),
            "percent",
            scale=2,
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "gross_margin")),
        ),
        "warrant_coverage": envelope(
            format_fixed(borrower.warrant_coverage, 2),
            "percent",
            scale=2,
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "warrant_coverage")),
        ),
        "spread": envelope(
            format_fixed(borrower.spread, PERCENT_ANNUAL_SCALE),
            "percent_annual",
            scale=PERCENT_ANNUAL_SCALE,
            provenance=generated,
            asof=asof,
            **_band_kwargs(pricing_band),
        ),
        "closing_fee": envelope(
            format_fixed(borrower.closing_fee, 2),
            "percent",
            scale=2,
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "closing_fee")),
        ),
        "rbf_cap": envelope(
            format_fixed(borrower.rbf_cap, 2),
            "multiple",
            scale=2,
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "rbf_cap")),
        ),
        "io_months": envelope(
            borrower.io_months,
            "count",
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "interest_only")),
        ),
        "term_months": envelope(
            borrower.term_months,
            "count",
            provenance=term_provenance,
            asof=asof,
        ),
    }


def _paths_document(borrower: Borrower, asof: str) -> dict[str, object]:
    """Publish the generator revenue and CFADS paths, one envelope per period."""
    return {
        "revenue_cents": [
            envelope(amount, "usd_minor_units", provenance=_DERIVED_REVENUE_PATH, asof=asof)
            for amount in borrower.revenue_path
        ],
        "cfads_cents": [
            envelope(amount, "usd_minor_units", provenance=_DERIVED_CFADS_PATH, asof=asof)
            for amount in borrower.cfads_path
        ],
    }


def _pricing_document(
    borrower: Borrower,
    pricing: Pricing,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
) -> dict[str, object]:
    """Publish the rate figures with external provenance from the snapshot.

    Every rate figure carries the snapshot source and source_timestamp
    (architecture P2.3), and spread_pct also carries its pricing band's
    citation and band label because it is a band-sourced draw. band_excursion
    is a report-only label, not a numeric leaf, so it stays a plain string.
    """
    external = {"source": snapshot.source, "source_timestamp": snapshot.source_timestamp}
    pricing_band = _require_band(
        cfg, "pricing_bank" if borrower.lender_type == "bank" else "pricing_nonbank"
    )
    scale = PERCENT_ANNUAL_SCALE
    return {
        "base_rate": envelope(
            format_fixed(pricing.base_rate, scale),
            "percent_annual",
            scale=scale,
            **external,
        ),
        "spread_pct": envelope(
            format_fixed(pricing.spread_pct, scale),
            "percent_annual",
            scale=scale,
            **external,
            **_band_kwargs(pricing_band),
        ),
        "all_in_rate": envelope(
            format_fixed(pricing.all_in_rate, scale),
            "percent_annual",
            scale=scale,
            **external,
        ),
        "shocked_all_in_rate": envelope(
            format_fixed(pricing.shocked_all_in_rate, scale),
            "percent_annual",
            scale=scale,
            **external,
        ),
        "band_excursion": pricing.band_excursion,
    }


def _sizing_document(
    borrower: Borrower,
    result: SizingResult,
    cfg: ScenarioConfig,
    asof: str,
) -> dict[str, object]:
    """Publish one borrower's sizing outcome following the F1 conventions.

    Every borrower, feasible or not, publishes band_cap: the 50 percent
    sizing band cap on last round in integer cents with the facility_sizing
    citation and band label (spec section 12: the sizing page shows the band
    cap with citation). The cap is a property of the drawn last round, not
    of feasibility, so an infeasible block carries it too. A feasible result
    then publishes facility_cents, binding_constraint,
    post_draw_runway_months, and min_dscr. An infeasible result publishes
    status plus its binding_constraint only, with no facility value
    (fixture F1c: a zero-sized facility is never presented as a solution).
    Pre-revenue borrowers publish min_dscr as a not_applicable status with
    the companion reason, never a fabricated zero.
    """
    sizing_provenance = _sizing_provenance(cfg)
    document: dict[str, object] = {
        "status": envelope(
            result.status, "status", provenance=sizing_provenance, asof=asof
        ),
        "band_cap": envelope(
            _band_cap_cents(borrower, cfg),
            "usd_minor_units",
            provenance=_DERIVED_BAND_CAP,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "facility_sizing")),
        ),
    }
    if result.status == "infeasible":
        document["binding_constraint"] = result.binding_constraint
        return document
    if result.facility_cents is None:
        raise BuildError(
            f"{borrower.borrower_id}: feasible sizing carries no facility value"
        )
    if result.post_draw_runway_months is None:
        raise BuildError(
            f"{borrower.borrower_id}: feasible sizing carries no runway figure"
        )
    document["facility_cents"] = envelope(
        result.facility_cents,
        "usd_minor_units",
        provenance=sizing_provenance,
        asof=asof,
    )
    document["binding_constraint"] = result.binding_constraint
    document["post_draw_runway_months"] = envelope(
        format_fixed(result.post_draw_runway_months, 1),
        "months",
        scale=1,
        provenance=_DERIVED_RUNWAY,
        asof=asof,
    )
    if result.min_dscr is None:
        if not borrower.pre_revenue:
            raise BuildError(
                f"{borrower.borrower_id}: min_dscr missing for a borrower "
                "with a revenue path"
            )
        document["min_dscr"] = envelope(
            "not_applicable", "status", provenance=sizing_provenance, asof=asof
        )
        document["reason"] = PRE_REVENUE_DSCR_REASON
    else:
        document["min_dscr"] = envelope(
            format_fixed(result.min_dscr, 4),
            "ratio",
            scale=4,
            provenance=_DERIVED_MIN_DSCR,
            asof=asof,
        )
    return document


def _schedule_row_document(
    row: ScheduleRow, borrower: Borrower, asof: str
) -> dict[str, object]:
    """Publish one schedule row: period, date, and four money envelopes."""
    if borrower.structure == "bullet":
        principal_provenance = _DERIVED_SCHEDULE_PRINCIPAL_BULLET
    elif borrower.structure == "bridge":
        principal_provenance = _DERIVED_BRIDGE_PRINCIPAL
    else:
        principal_provenance = _DERIVED_SCHEDULE_PRINCIPAL_IO
    return {
        "period": envelope(
            row.period, "count", provenance=_DERIVED_SCHEDULE_PERIOD, asof=asof
        ),
        "payment_date": envelope(
            row.payment_date.isoformat(),
            "date",
            provenance=_DERIVED_SCHEDULE_DATE,
            asof=asof,
        ),
        "opening_balance": envelope(
            row.opening_cents,
            "usd_minor_units",
            provenance=_DERIVED_SCHEDULE_OPENING,
            asof=asof,
        ),
        "interest": envelope(
            row.interest_cents,
            "usd_minor_units",
            provenance=_DERIVED_SCHEDULE_INTEREST,
            asof=asof,
        ),
        "principal": envelope(
            row.principal_cents,
            "usd_minor_units",
            provenance=principal_provenance,
            asof=asof,
        ),
        "closing_balance": envelope(
            row.closing_cents,
            "usd_minor_units",
            provenance=_DERIVED_SCHEDULE_CLOSING,
            asof=asof,
        ),
    }


def _rbf_revenue_path(borrower: Borrower, horizon: int) -> tuple[int, ...]:
    """Return the revenue path the RBF schedule consumes over the horizon.

    The generator pins rbf_capped term_months to the pinned RBF horizon, so
    the published path already spans exactly `horizon` periods. A mismatch
    fails the build loudly rather than silently padding or truncating the
    money flows the schedule remits from.
    """
    path = borrower.revenue_path
    if len(path) != horizon:
        raise BuildError(
            f"{borrower.borrower_id}: RBF revenue path length {len(path)} "
            f"does not equal the pinned RBF horizon {horizon}"
        )
    return path


def _grown_revenue_cents(borrower: Borrower, period: int) -> int:
    """Return the generator revenue formula at period, quantized once.

    ARR / 12 grown by the monthly growth rate per period, the same formula
    generator._paths uses and compare.py restates for its horizon
    adaptation. Pre-revenue borrowers have ARR 0, so the growth yields
    0 cents.
    """
    base = MONEY_CONTEXT.divide(Decimal(borrower.arr_cents), Decimal(MONTHS_PER_YEAR))
    growth_fraction = MONEY_CONTEXT.divide(borrower.monthly_growth, Decimal(100))
    factor = MONEY_CONTEXT.add(Decimal(1), growth_fraction)
    grown = MONEY_CONTEXT.power(factor, Decimal(period - 1))
    return int(quantize(MONEY_CONTEXT.multiply(base, grown), 0))


def _horizon_revenue_path(borrower: Borrower, horizon: int) -> tuple[int, ...]:
    """Return the revenue path over exactly `horizon` periods.

    Slices the generator path when it is longer and extends the tail with
    the generator growth formula when it is shorter, so a comparison
    structure with its own horizon consumes revenue from the generator's
    own draws only (the same rule compare._horizon_revenue_path applies).
    """
    path = borrower.revenue_path
    if len(path) == horizon:
        return path
    if len(path) > horizon:
        return path[:horizon]
    extended = list(path)
    for period in range(len(path) + 1, horizon + 1):
        extended.append(_grown_revenue_cents(borrower, period))
    return tuple(extended)


def _horizon_cfads_path(borrower: Borrower, horizon: int) -> tuple[int, ...]:
    """Return the CFADS path over exactly `horizon` periods.

    Slicing is exact because the generator derives CFADS from revenue; the
    extension applies the gross margin to the grown revenue with one
    quantize per period, the rule generator._paths uses.
    """
    path = borrower.cfads_path
    if len(path) == horizon:
        return path
    if len(path) > horizon:
        return path[:horizon]
    extended = list(path)
    for period in range(len(path) + 1, horizon + 1):
        revenue = _grown_revenue_cents(borrower, period)
        cfads = int(
            quantize(
                MONEY_CONTEXT.divide(
                    MONEY_CONTEXT.multiply(Decimal(revenue), borrower.gross_margin),
                    Decimal(100),
                ),
                0,
            )
        )
        extended.append(cfads)
    return tuple(extended)


def _schedule_for_structure(
    borrower: Borrower,
    structure: str,
    facility_cents: int,
    close: date,
    pricing: Pricing,
    cfg: ScenarioConfig,
) -> Schedule | RbfSchedule:
    """Build one structure's schedule at the canonical facility (P3.5, P4.1).

    rbf_capped goes through schedule.build_rbf_schedule over the pinned
    horizon with the drawn cap multiple; bridge goes through
    schedule.build_bridge_schedule at the pinned tenor; bullet and
    io_then_straight_line go through schedule.build_schedule at the priced
    all-in rate, the io row on the canonical io plus amortizing periods
    term. The drawn rbf_capped path keeps the loud length guard
    (_rbf_revenue_path); a comparison rbf row adapts the generator path to
    the pinned horizon the way compare.py does.
    """
    if structure == STRUCTURE_RBF:
        horizon = cfg.generator.rbf_horizon_periods
        if structure == borrower.structure:
            revenue = _rbf_revenue_path(borrower, horizon)
        else:
            revenue = _horizon_revenue_path(borrower, horizon)
        return build_rbf_schedule(
            facility_cents,
            close,
            horizon,
            cfg.generator.rbf_remittance_rate_pct,
            borrower.rbf_cap,
            revenue,
        )
    if structure == STRUCTURE_BRIDGE:
        return build_bridge_schedule(
            facility_cents,
            close,
            cfg.generator.bridge_tenor_months,
            pricing.all_in_rate,
        )
    if structure == STRUCTURE_IO:
        term_months = borrower.io_months + cfg.generator.term_amortizing_periods
    else:
        term_months = borrower.term_months
    return build_schedule(
        structure,
        facility_cents,
        close,
        term_months,
        borrower.io_months,
        pricing.all_in_rate,
    )


def _downside_structure_schedule(
    borrower: Borrower,
    structure: str,
    base_schedule: Schedule | RbfSchedule,
    close: date,
    pricing: Pricing,
    cfg: ScenarioConfig,
) -> Schedule | RbfSchedule:
    """Return the scenario schedule for one structure (P4.2).

    Term structures (bullet, io_then_straight_line, bridge) are contractual
    under downside, so the base schedule carries over unchanged. rbf_capped
    rebuilds its remittance schedule on the downside revenue path at the
    same advance, rate, cap, and horizon: remittances fall with revenue and
    a base cap-hit row can degrade to shortfall.
    """
    if structure != STRUCTURE_RBF:
        return base_schedule
    if not isinstance(base_schedule, RbfSchedule):
        raise BuildError(
            f"{borrower.borrower_id}: rbf_capped base schedule is "
            f"{type(base_schedule).__name__}, expected RbfSchedule"
        )
    lowered = downside_revenue(
        _horizon_revenue_path(borrower, base_schedule.horizon),
        cfg.downside.revenue_downlift_pct,
    )
    return build_rbf_schedule(
        base_schedule.advance_cents,
        close,
        base_schedule.horizon,
        base_schedule.remittance_rate_pct,
        base_schedule.cap_multiple,
        lowered,
    )


def _covenant_specs(cfg: ScenarioConfig, min_cash_limit_cents: int) -> tuple[CovenantSpec, ...]:
    """Build the four pinned CovenantSpecs for one scenario (P4.1).

    net_leverage and interest_cover take their configured limits from
    scenario.toml [covenants] with the stated covenant assumption citation;
    min_cash takes the scenario burn floor in integer cents computed by
    build_scenario_covenant_inputs (the floor scales with the scenario's
    burn); dscr_floor takes cfg.dscr_floor with the dscr_floor_reference
    band citation and band label, guarded so a diverging band value can
    never ride an honest citation.
    """
    dscr_band = _require_band(cfg, "dscr_floor_reference")
    if cfg.dscr_floor != dscr_band.max:
        raise BuildError(
            f"scenario.toml sizing.dscr_floor {cfg.dscr_floor} does not match "
            f"the bands.dscr_floor_reference value {dscr_band.max}"
        )
    return (
        CovenantSpec(
            "net_leverage",
            "max",
            cfg.covenants.net_leverage_max,
            "ratio",
            STATED_COVENANT_CITATION,
            None,
        ),
        CovenantSpec(
            "interest_cover",
            "min",
            cfg.covenants.interest_cover_min,
            "ratio",
            STATED_COVENANT_CITATION,
            None,
        ),
        CovenantSpec(
            "min_cash",
            "min",
            min_cash_limit_cents,
            "usd_minor_units",
            STATED_COVENANT_CITATION,
            None,
        ),
        CovenantSpec(
            "dscr_floor",
            "min",
            cfg.dscr_floor,
            "ratio",
            dscr_band.citation,
            dscr_band.band,
        ),
    )


@dataclass(frozen=True)
class _StructureCovenants:
    """One structure's covenant evaluation under both scenarios (P4.1, P4.2).

    base_schedule and downside_schedule are the schedules the covenant
    inputs were derived from (identical for term structures, the rebuilt
    remittance schedule for rbf_capped); base_package and downside_package
    are the four CovenantResults in pinned order; base_cash and downside_cash
    are the published per-period cash paths.
    """

    structure: str
    base_schedule: Schedule | RbfSchedule
    downside_schedule: Schedule | RbfSchedule
    base_package: tuple[CovenantResult, ...]
    downside_package: tuple[CovenantResult, ...]
    base_cash: tuple[int, ...]
    downside_cash: tuple[int, ...]


def _scenario_covenant_paths(
    borrower: Borrower,
    schedule: Schedule | RbfSchedule,
    cfg: ScenarioConfig,
    scenario: str,
) -> tuple[tuple[int, ...], int]:
    """Return (scenario CFADS over the schedule horizon, scenario net burn).

    base uses the generator CFADS path adapted to the schedule horizon and
    the drawn net burn; downside downlifts the horizon revenue path,
    recomputes CFADS from it with one quantize per period, and uplifts the
    net burn once (P4.2 path transforms).
    """
    horizon = len(schedule.rows)
    if scenario == "base":
        return _horizon_cfads_path(borrower, horizon), borrower.net_burn_cents
    lowered = downside_revenue(
        _horizon_revenue_path(borrower, horizon),
        cfg.downside.revenue_downlift_pct,
    )
    cfads = downside_cfads(lowered, borrower.gross_margin)
    burn = downside_burn(borrower.net_burn_cents, cfg.downside.burn_uplift_pct)
    return cfads, burn


def _evaluate_structure_covenants(
    borrower: Borrower,
    structure: str,
    facility_cents: int,
    close: date,
    pricing: Pricing,
    cfg: ScenarioConfig,
) -> _StructureCovenants:
    """Evaluate the four covenants for one structure under base and downside.

    Both scenarios run on the structure's own schedules with the scenario
    CFADS and burn paths, through scenarios.build_scenario_covenant_inputs
    and covenants.evaluate_package (the caller owns every limit, per the
    covenant module contract).
    """
    base_schedule = _schedule_for_structure(
        borrower, structure, facility_cents, close, pricing, cfg
    )
    downside_schedule = _downside_structure_schedule(
        borrower, structure, base_schedule, close, pricing, cfg
    )
    packages: dict[str, tuple[CovenantResult, ...]] = {}
    cash_paths: dict[str, tuple[int, ...]] = {}
    for scenario, schedule in (("base", base_schedule), ("downside", downside_schedule)):
        cfads, burn = _scenario_covenant_paths(borrower, schedule, cfg, scenario)
        inputs = build_scenario_covenant_inputs(
            borrower,
            schedule,
            burn,
            cfads,
            cfg.covenants.min_cash_floor_months,
        )
        specs = _covenant_specs(cfg, inputs.burn_floor_cents)
        packages[scenario] = evaluate_package(
            specs,
            ebitda=inputs.ebitda,
            net_debt=inputs.net_debt,
            cash=inputs.cash,
            cash_interest=inputs.cash_interest,
            debt_service=inputs.debt_service,
            dates=inputs.dates,
        )
        cash_paths[scenario] = inputs.cash
    return _StructureCovenants(
        structure=structure,
        base_schedule=base_schedule,
        downside_schedule=downside_schedule,
        base_package=packages["base"],
        downside_package=packages["downside"],
        base_cash=cash_paths["base"],
        downside_cash=cash_paths["downside"],
    )


def _rbf_row_document(row: RbfRow, asof: str) -> dict[str, object]:
    """Publish one RBF remittance row: period, date, revenue, remittance, cumulative.

    RBF rows carry no interest, principal, or balance keys (the F4 shape):
    the royalty structure has no interest term, so fabricated zero columns
    would violate the not_applicable principle.
    """
    return {
        "period": envelope(
            row.period, "count", provenance=_DERIVED_SCHEDULE_PERIOD, asof=asof
        ),
        "payment_date": envelope(
            row.payment_date.isoformat(),
            "date",
            provenance=_DERIVED_SCHEDULE_DATE,
            asof=asof,
        ),
        "revenue": envelope(
            row.revenue_cents, "usd_minor_units", provenance=_DERIVED_RBF_REVENUE, asof=asof
        ),
        "remittance": envelope(
            row.remittance_cents,
            "usd_minor_units",
            provenance=_DERIVED_RBF_REMITTANCE,
            asof=asof,
        ),
        "cumulative": envelope(
            row.cumulative_cents,
            "usd_minor_units",
            provenance=_DERIVED_RBF_CUMULATIVE,
            asof=asof,
        ),
    }


def _rbf_schedule_document(schedule: RbfSchedule, asof: str) -> dict[str, object]:
    """Publish the kind-tagged RBF schedule union member (architecture P3.6).

    cap, final_cumulative, and residual are integer cents leaves; status is
    an envelope whose shortfall companion is the residual key beside it in
    this document (section 6.4 matrix), while feasible carries no
    companion.
    """
    return {
        "kind": "rbf",
        "rows": [_rbf_row_document(row, asof) for row in schedule.rows],
        "cap": envelope(
            schedule.cap_cents, "usd_minor_units", provenance=_DERIVED_RBF_CAP, asof=asof
        ),
        "final_cumulative": envelope(
            schedule.final_cumulative_cents,
            "usd_minor_units",
            provenance=_DERIVED_RBF_FINAL_CUMULATIVE,
            asof=asof,
        ),
        "residual": envelope(
            schedule.residual_cents,
            "usd_minor_units",
            provenance=_DERIVED_RBF_RESIDUAL,
            asof=asof,
        ),
        "status": envelope(
            schedule.status, "status", provenance=_DERIVED_RBF_STATUS, asof=asof
        ),
    }


def _schedule_document(
    borrower: Borrower,
    schedule: Schedule | RbfSchedule | None,
    asof: str,
) -> dict[str, object]:
    """Publish the kind-tagged structure schedule union (architecture P3.6).

    A feasible amortizing or bridge borrower publishes {"kind":
    "amortizing", "rows": [...]}, an rbf_capped borrower publishes {"kind":
    "rbf", "rows": [...], "cap", "final_cumulative", "residual", "status"},
    and a borrower whose sizing is infeasible publishes the not_applicable
    status with the sizing reason: there is no facility to amortize. The
    phase 2 placeholder reason for rbf_capped and bridge is retired here.
    """
    if schedule is None:
        return {
            "status": envelope(
                "not_applicable",
                "status",
                provenance=_DERIVED_SCHEDULE_STATUS,
                asof=asof,
            ),
            "reason": INFEASIBLE_SCHEDULE_REASON,
        }
    if isinstance(schedule, RbfSchedule):
        return _rbf_schedule_document(schedule, asof)
    return {
        "kind": "amortizing",
        "rows": [
            _schedule_row_document(row, borrower, asof) for row in schedule.rows
        ],
    }


def _schedule_receipts_cents(schedule: Schedule | RbfSchedule) -> int:
    """Return total lender receipts: interest plus principal, or remittances.

    An RbfSchedule carries no interest or principal split, so its receipts
    are the final cumulative remittances; a Schedule's receipts are its
    interest plus principal totals (the P3.3 receipts basis).
    """
    final_cumulative = getattr(schedule, "final_cumulative_cents", None)
    if final_cumulative is not None:
        return final_cumulative
    return schedule.total_interest_cents + schedule.total_principal_cents


def _lender_irr_document(
    irr: IRRResult, snapshot: RateSnapshot, asof: str
) -> dict[str, object]:
    """Publish one borrower's lender IRR block (architecture P3.6).

    Shape note: snapshot.validate_payload treats any mapping that carries a
    "value" key as one envelope, so the P3.6 nested form {status, value}
    cannot validate (the container would be read as an envelope missing its
    unit). The published form therefore keeps every pinned P3.6 key while
    making the lender_irr node itself the percent_annual scale 4 value
    envelope: the feasible branch attaches the status envelope as a child of
    the value envelope (external provenance with the rate source and
    source_timestamp), and the no_solution branch publishes the status
    envelope plus the reason companion and no value keys (section 6.4
    matrix).
    """
    status = envelope(irr.status, "status", provenance=_DERIVED_IRR, asof=asof)
    if irr.status == "feasible":
        if irr.irr_percent is None:
            raise BuildError("feasible lender IRR carries no published percent value")
        value_envelope = envelope(
            format_fixed(irr.irr_percent, PERCENT_ANNUAL_SCALE),
            "percent_annual",
            scale=PERCENT_ANNUAL_SCALE,
            source=snapshot.source,
            source_timestamp=snapshot.source_timestamp,
        )
        value_envelope["status"] = status
        return value_envelope
    return {"status": status, "reason": irr.reason}


def _returns_document(
    borrower: Borrower,
    schedule: Schedule | RbfSchedule | None,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
    asof: str,
) -> dict[str, object]:
    """Publish one borrower's returns block (architecture P3.6).

    An infeasible borrower publishes status not_applicable with a reason and
    no cash-flow or value keys. A feasible borrower publishes the status
    envelope, the lender IRR block, MOIC, total receipts, the closing fee,
    the warrant leaf (zero coverage carries the not_granted companion on the
    value leaf, the fixture-pinned asymmetric shape), and the dated lender
    cash flows in ascending date order straight from
    returns.lender_cash_flows.
    """
    if schedule is None:
        return {
            "status": envelope(
                "not_applicable",
                "status",
                provenance=_DERIVED_RETURNS_STATUS,
                asof=asof,
            ),
            "reason": INFEASIBLE_REASON,
        }
    if isinstance(schedule, RbfSchedule):
        facility = schedule.advance_cents
    else:
        facility = schedule.facility_cents
    fee = closing_fee_cents(facility, borrower.closing_fee)
    warrant = warrant_value_cents(
        borrower.warrant_coverage, facility, cfg.warrant_valuation_factor
    )
    warrant_date = schedule.rows[-1].payment_date
    flows = lender_cash_flows(schedule, fee, warrant, warrant_date)
    irr = solve_irr(flows, snapshot.observation_date, cfg.solver)
    receipts = _schedule_receipts_cents(schedule)
    row_moic = receipts_moic(receipts, warrant, facility)
    warrant_leaf = envelope(
        warrant, "usd_minor_units", provenance=_warrant_provenance(cfg), asof=asof
    )
    if warrant == 0:
        warrant_leaf["status"] = "not_granted"
    return {
        "status": envelope(
            "feasible", "status", provenance=_DERIVED_RETURNS_STATUS, asof=asof
        ),
        "lender_irr": _lender_irr_document(irr, snapshot, asof),
        "moic": envelope(
            format_fixed(row_moic, 2),
            "multiple",
            scale=2,
            provenance=_DERIVED_MOIC,
            asof=asof,
        ),
        "total_receipts": envelope(
            receipts, "usd_minor_units", provenance=_DERIVED_RECEIPTS, asof=asof
        ),
        "closing_fee": envelope(
            fee, "usd_minor_units", provenance=_DERIVED_CLOSING_FEE, asof=asof
        ),
        "warrant": warrant_leaf,
        "cash_flows": [
            {
                "date": envelope(
                    flow.flow_date.isoformat(),
                    "date",
                    provenance=_DERIVED_CASH_FLOW_DATE,
                    asof=asof,
                ),
                "cash_flow": envelope(
                    flow.amount_cents,
                    "usd_minor_units",
                    provenance=_DERIVED_CASH_FLOW,
                    asof=asof,
                ),
            }
            for flow in flows
        ],
    }


def _limit_envelope(spec: CovenantSpec, cfg: ScenarioConfig, asof: str) -> dict[str, object]:
    """Publish one covenant's limit leaf, with citation where applicable (P4.2).

    ratio limits publish at ratio scale 4 and min_cash publishes integer
    cents. The three stated-assumption limits carry their configured value
    with a derived provenance; the dscr_floor limit is band sourced, so it
    carries the dscr_floor_reference citation and band label beside the
    derived provenance (the section 6.4 band rule).
    """
    if spec.unit == "ratio":
        if not isinstance(spec.limit, Decimal):
            raise BuildError(
                f"{spec.name} limit must be a Decimal ratio, got {spec.limit!r}"
            )
        value = format_fixed(spec.limit, _RATIO_SCALE)
        if spec.band is None:
            provenance = (
                _DERIVED_DSCR_LIMIT
                if spec.name == "dscr_floor"
                else _DERIVED_COVENANT_LIMIT
            )
            return envelope(
                value,
                "ratio",
                scale=_RATIO_SCALE,
                provenance=provenance,
                asof=asof,
            )
        return envelope(
            value,
            "ratio",
            scale=_RATIO_SCALE,
            provenance=_DERIVED_DSCR_LIMIT,
            asof=asof,
            citation=spec.citation,
            band=spec.band,
        )
    if not isinstance(spec.limit, int):
        raise BuildError(f"{spec.name} limit must be integer cents, got {spec.limit!r}")
    return envelope(
        spec.limit,
        "usd_minor_units",
        provenance=_DERIVED_MIN_CASH_LIMIT,
        asof=asof,
    )


def _headroom_envelope(result: CovenantResult, provenance: str, asof: str) -> dict[str, object]:
    """Publish one covenant headroom leaf at the covenant's metric scale."""
    value = result.min_headroom
    if value is None:
        raise BuildError(f"{result.spec.name}: applicable covenant carries no min headroom")
    if result.spec.unit == "ratio":
        if not isinstance(value, Decimal):
            raise BuildError(
                f"{result.spec.name} headroom must be a Decimal ratio, got {value!r}"
            )
        return envelope(
            format_fixed(value, _RATIO_SCALE),
            "ratio",
            scale=_RATIO_SCALE,
            provenance=provenance,
            asof=asof,
        )
    if not isinstance(value, int):
        raise BuildError(f"{result.spec.name}: min_cash headroom must be cents, got {value!r}")
    return envelope(value, "usd_minor_units", provenance=provenance, asof=asof)


def _first_breach_value_leaf(months: int, asof: str) -> dict[str, object]:
    """Publish a breach first-breach leaf: months value plus status child.

    The cell is the months scale 1 value envelope with the breach status
    envelope attached as a child, the same nesting the lender IRR cell uses:
    a container holding both a status envelope and a value key would be
    read as an envelope missing its unit (section 6.4 shape note).
    """
    leaf = envelope(
        format_fixed(Decimal(months), _FIRST_BREACH_SCALE),
        "months",
        scale=_FIRST_BREACH_SCALE,
        provenance=_DERIVED_FIRST_BREACH,
        asof=asof,
    )
    leaf["status"] = envelope(
        "breach", "status", provenance=_DERIVED_COVENANT_STATUS, asof=asof
    )
    return leaf


def _covenant_first_breach_leaf(result: CovenantResult, asof: str) -> dict[str, object]:
    """Publish one covenant's first-breach leaf (P4.1 status shapes).

    breach publishes the months value leaf, feasible publishes the status
    with the no-breach provenance and neither value nor reason, and
    not_applicable publishes the status with the covenant's stated reason.
    """
    if result.first_breach_month is not None:
        return _first_breach_value_leaf(result.first_breach_month, asof)
    status_provenance = (
        _DERIVED_NO_BREACH if result.status == "feasible" else _DERIVED_COVENANT_STATUS
    )
    if result.status == "not_applicable" and result.reason is None:
        raise BuildError(f"{result.spec.name}: not_applicable covenant carries no reason")
    leaf: dict[str, object] = {
        "status": envelope(
            result.status, "status", provenance=status_provenance, asof=asof
        )
    }
    if result.reason is not None:
        leaf["reason"] = result.reason
    return leaf


def _package_first_breach_leaf(
    package: tuple[CovenantResult, ...], asof: str
) -> dict[str, object]:
    """Publish the borrower or structure level first-breach leaf (P4.1).

    The leaf is the minimum breach period across the applicable covenants,
    the feasible leaf when no period breaches through the horizon, or the
    not_applicable leaf only when no covenant is applicable at all (min_cash
    is always applicable, so that last case fails the build loudly).
    """
    breaches = [
        result.first_breach_month
        for result in package
        if result.first_breach_month is not None
    ]
    if breaches:
        return _first_breach_value_leaf(min(breaches), asof)
    if any(result.status != "not_applicable" for result in package):
        return {
            "status": envelope(
                "feasible", "status", provenance=_DERIVED_NO_BREACH, asof=asof
            )
        }
    raise BuildError("covenant package carries no applicable covenant to publish")


def _covenant_block_document(
    result: CovenantResult, cfg: ScenarioConfig, asof: str
) -> dict[str, object]:
    """Publish one covenant's book.json summary block (P4.2 shape).

    Spec leaves (name, kind, limit with citation where applicable), the
    status envelope, the reason companion exactly when not_applicable, the
    min_headroom leaf exactly when applicable, and the covenant's own
    first-breach leaf. An not_applicable covenant publishes no headroom
    figure rather than a fabricated zero.
    """
    block: dict[str, object] = {
        "name": result.spec.name,
        "kind": result.spec.kind,
        "limit": _limit_envelope(result.spec, cfg, asof),
        "status": envelope(
            result.status, "status", provenance=_DERIVED_COVENANT_STATUS, asof=asof
        ),
    }
    if result.status == "not_applicable":
        if result.reason is None:
            raise BuildError(f"{result.spec.name}: not_applicable covenant carries no reason")
        block["reason"] = result.reason
    else:
        block["min_headroom"] = _headroom_envelope(
            result, _DERIVED_MIN_HEADROOM, asof
        )
    block["first_breach_month"] = _covenant_first_breach_leaf(result, asof)
    return block


def _covenant_package_document(
    package: tuple[CovenantResult, ...], cfg: ScenarioConfig, asof: str
) -> dict[str, object]:
    """Publish the four covenant blocks of one scenario, pinned order."""
    return {
        result.spec.name: _covenant_block_document(result, cfg, asof)
        for result in package
    }


def _covenants_document(
    covenants: _StructureCovenants | None, cfg: ScenarioConfig, asof: str
) -> dict[str, object]:
    """Publish one borrower's covenants block (P4.2 book.json shape).

    A feasible borrower publishes the base and downside packages evaluated
    on the drawn structure's schedule plus the borrower-level first-breach
    leaf taken from the downside package (spec 10.6 column). An infeasible
    borrower publishes the not_applicable shape with the sizing reason in
    both blocks and in the leaf: no facility, no covenant test.
    """
    if covenants is None:
        return {
            "base": _na_cell(_sizing_provenance(cfg), INFEASIBLE_REASON, asof),
            "downside": _na_cell(_sizing_provenance(cfg), INFEASIBLE_REASON, asof),
            "first_breach_month": _na_cell(
                _sizing_provenance(cfg), INFEASIBLE_REASON, asof
            ),
        }
    return {
        "base": _covenant_package_document(covenants.base_package, cfg, asof),
        "downside": _covenant_package_document(covenants.downside_package, cfg, asof),
        "first_breach_month": _package_first_breach_leaf(
            covenants.downside_package, asof
        ),
    }


def _warrant_leaf(
    warrant_cents: int, cfg: ScenarioConfig, asof: str, *, downside: bool
) -> dict[str, object]:
    """Publish a warrant value leaf with the pinned provenance (P3.4, P4.2).

    base and contractual figures carry the fixed factor model provenance;
    the downside scenario figure carries the revalued standalone
    provenance. A zero warrant carries the not_granted companion on the
    value leaf (the fixture-pinned asymmetric shape).
    """
    provenance = (
        _warrant_downside_provenance(cfg) if downside else _warrant_provenance(cfg)
    )
    leaf = envelope(warrant_cents, "usd_minor_units", provenance=provenance, asof=asof)
    if warrant_cents == 0:
        leaf["status"] = "not_granted"
    return leaf


def _scenario_block_document(
    block: ScenarioBlock,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
    asof: str,
    contractual_warrant_cents: int,
    *,
    downside: bool,
) -> dict[str, object]:
    """Publish one scenario block of the book.json scenarios shape (P4.2).

    Both scenarios carry lender_irr (the Package A flows solved with the
    base warrant), moic, total interest, total receipts, closing fee, the
    scenario warrant figure, and warrant_contractual holding the base
    figure in both scenarios so the page can show the revaluation pair.
    rbf_capped adds rbf_status and rbf_residual on the downside; the
    shortfall companion travels inside the status envelope because the
    section 6.4 matrix looks for the literal residual key beside the
    status value.
    """
    rbf = block.structure == STRUCTURE_RBF
    interest_provenance = _DERIVED_RBF_TOTAL_INTEREST if rbf else _DERIVED_TOTAL_INTEREST
    document: dict[str, object] = {
        "lender_irr": _lender_irr_document(block.irr, snapshot, asof),
        "moic": envelope(
            format_fixed(block.moic, _MULTIPLE_SCALE),
            "multiple",
            scale=_MULTIPLE_SCALE,
            provenance=_DERIVED_MOIC,
            asof=asof,
        ),
        "total_interest": envelope(
            block.total_interest_cents,
            "usd_minor_units",
            provenance=interest_provenance,
            asof=asof,
        ),
        "total_receipts": envelope(
            block.total_receipts_cents,
            "usd_minor_units",
            provenance=_DERIVED_RECEIPTS,
            asof=asof,
        ),
        "closing_fee": envelope(
            block.closing_fee_cents,
            "usd_minor_units",
            provenance=_DERIVED_CLOSING_FEE,
            asof=asof,
        ),
        "warrant": _warrant_leaf(block.warrant_cents, cfg, asof, downside=downside),
        "warrant_contractual": _warrant_leaf(
            contractual_warrant_cents, cfg, asof, downside=False
        ),
    }
    if rbf and downside:
        if block.rbf_status is None or block.rbf_residual_cents is None:
            raise BuildError(
                "downside rbf scenario block carries no status or residual"
            )
        status_leaf = envelope(
            block.rbf_status, "status", provenance=_DERIVED_RBF_STATUS, asof=asof
        )
        residual_leaf = envelope(
            block.rbf_residual_cents,
            "usd_minor_units",
            provenance=_DERIVED_RBF_RESIDUAL,
            asof=asof,
        )
        if block.rbf_status == "shortfall":
            status_leaf["residual"] = residual_leaf
        document["rbf_status"] = status_leaf
        document["rbf_residual"] = residual_leaf
    return document


def _delta_leaf(
    value: int | str,
    unit: str,
    scale: int | None,
    label: str | None,
    asof: str,
) -> dict[str, object]:
    """Publish one scenario delta with its published status label (P4.5).

    The label child carries "invariant by construction" for a zero delta
    and "downside minus base" otherwise, so the browser renders the
    distinction from the published label and never subtracts.
    """
    if label is None:
        raise BuildError(f"delta for unit {unit} carries no published label")
    leaf = envelope(
        value,
        unit,
        scale=scale,
        provenance=_DERIVED_SCENARIO_DELTA,
        asof=asof,
    )
    leaf["status"] = label
    return leaf


def _deltas_document(deltas: ScenarioDeltas, asof: str) -> dict[str, object]:
    """Publish the scenarios deltas block, downside minus base (P4.2)."""
    document: dict[str, object] = {}
    if deltas.lender_irr_delta_bp is None:
        if deltas.lender_irr_delta_reason is None:
            raise BuildError("missing lender IRR delta carries no reason")
        document["lender_irr_delta"] = {
            "status": envelope(
                "no_solution", "status", provenance=_DERIVED_SCENARIO_DELTA, asof=asof
            ),
            "reason": deltas.lender_irr_delta_reason,
        }
    else:
        document["lender_irr_delta"] = _delta_leaf(
            format_fixed(deltas.lender_irr_delta_bp, _BP_SCALE),
            "bp",
            _BP_SCALE,
            deltas.lender_irr_delta_label,
            asof,
        )
    document["moic_delta"] = _delta_leaf(
        format_fixed(deltas.moic_delta, _MULTIPLE_SCALE),
        "multiple",
        _MULTIPLE_SCALE,
        deltas.moic_delta_label,
        asof,
    )
    document["total_interest_delta"] = _delta_leaf(
        deltas.total_interest_delta_cents,
        "usd_minor_units",
        None,
        deltas.total_interest_delta_label,
        asof,
    )
    document["total_receipts_delta"] = _delta_leaf(
        deltas.total_receipts_delta_cents,
        "usd_minor_units",
        None,
        deltas.total_receipts_delta_label,
        asof,
    )
    document["warrant_delta"] = _delta_leaf(
        deltas.warrant_delta_cents,
        "usd_minor_units",
        None,
        deltas.warrant_delta_label,
        asof,
    )
    return document


def _rate_shock_document(
    shock: RateShockBlock,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
    asof: str,
) -> dict[str, object]:
    """Publish the P4.3 rate-shock block inside the scenarios shape.

    Every numeric leaf carries the base rate source and source_timestamp
    (external provenance): the shocked all-in rate, the shocked lender IRR
    with its lender_irr status companion shape, and the two bp delta
    leaves. RBF publishes shocked total interest as the contractual zero
    with its provenance string and the shocked IRR and delta fields as
    not_applicable with the pinned reason, never a fabricated 0 bp.
    """
    external = {"source": snapshot.source, "source_timestamp": snapshot.source_timestamp}
    document: dict[str, object] = {
        "shocked_all_in_rate": envelope(
            format_fixed(shock.shocked_all_in_rate, PERCENT_ANNUAL_SCALE),
            "percent_annual",
            scale=PERCENT_ANNUAL_SCALE,
            **external,
        ),
        "shocked_total_interest": envelope(
            shock.shocked_total_interest_cents,
            "usd_minor_units",
            provenance=shock.total_interest_reason or _DERIVED_TOTAL_INTEREST,
            asof=asof,
        ),
    }
    if shock.shocked_irr is None:
        if shock.irr_reason is None:
            raise BuildError("unpublished shocked lender IRR carries no reason")
        document["shocked_lender_irr"] = {
            "status": envelope(
                shock.irr_status, "status", provenance=_DERIVED_IRR, asof=asof
            ),
            "reason": shock.irr_reason,
        }
    else:
        document["shocked_lender_irr"] = _lender_irr_document(
            shock.shocked_irr, snapshot, asof
        )
    if shock.irr_delta_bp is None or shock.sensitivity_per_100bp is None:
        if shock.irr_status == "feasible":
            raise BuildError("feasible shocked IRR carries no published delta")
        if shock.delta_reason is None:
            raise BuildError("missing rate-shock delta carries no reason")
        for key in ("irr_delta_bp", "sensitivity_per_100bp"):
            document[key] = {
                "status": envelope(
                    shock.irr_status,
                    "status",
                    provenance=_DERIVED_SCENARIO_DELTA,
                    asof=asof,
                ),
                "reason": shock.delta_reason,
            }
        return document
    document["irr_delta_bp"] = envelope(
        format_fixed(shock.irr_delta_bp, _BP_SCALE),
        "bp",
        scale=_BP_SCALE,
        **external,
    )
    document["sensitivity_per_100bp"] = envelope(
        format_fixed(shock.sensitivity_per_100bp, _BP_SCALE),
        "bp",
        scale=_BP_SCALE,
        **external,
    )
    return document


def _scenarios_document(
    borrower: Borrower,
    pricing: Pricing,
    result: SizingResult,
    covenants: _StructureCovenants | None,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
    asof: str,
) -> dict[str, object]:
    """Publish one borrower's scenarios block (P4.2 book.json shape).

    Feasible borrowers assemble both scenarios and their deltas through
    scenarios.assemble_scenarios and the rate-shock block through
    scenarios.build_rate_shock, all on the drawn structure's base schedule.
    Infeasible borrowers publish the not_applicable shape with the sizing
    reason, never fabricated values.
    """
    if covenants is None:
        return {
            "status": envelope(
                "not_applicable",
                "status",
                provenance=_sizing_provenance(cfg),
                asof=asof,
            ),
            "reason": INFEASIBLE_REASON,
        }
    pair = assemble_scenarios(borrower, pricing, result, covenants.base_schedule, cfg)
    shock = build_rate_shock(
        borrower, pricing, result, covenants.base_schedule, cfg
    )
    return {
        "base": _scenario_block_document(
            pair.base,
            snapshot,
            cfg,
            asof,
            pair.base.warrant_cents,
            downside=False,
        ),
        "downside": _scenario_block_document(
            pair.downside,
            snapshot,
            cfg,
            asof,
            pair.base.warrant_cents,
            downside=True,
        ),
        "deltas": _deltas_document(pair.deltas, asof),
        "rate_shock": _rate_shock_document(shock, snapshot, cfg, asof),
    }


def _borrower_document(
    borrower: Borrower,
    pricing: Pricing,
    result: SizingResult,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
    asof: str,
    covenants: _StructureCovenants | None,
) -> dict[str, object]:
    """Assemble one borrower entry of book.json per the P4.2 shape.

    The drawn structure's covenant evaluation carries the base schedule,
    so the schedule union, the returns block, and the covenants summary
    all read the same rows and can never disagree about dates or amounts.
    An infeasible borrower has no schedule (None) and publishes the
    not_applicable shapes.
    """
    schedule = None if covenants is None else covenants.base_schedule
    return {
        "borrower_id": borrower.borrower_id,
        "lender_type": borrower.lender_type,
        "structure": borrower.structure,
        "pre_revenue": borrower.pre_revenue,
        "inputs": _inputs_document(borrower, cfg, asof),
        "paths": _paths_document(borrower, asof),
        "pricing": _pricing_document(borrower, pricing, snapshot, cfg),
        "sizing": _sizing_document(borrower, result, cfg, asof),
        "schedule": _schedule_document(borrower, schedule, asof),
        "returns": _returns_document(borrower, schedule, snapshot, cfg, asof),
        "covenants": _covenants_document(covenants, cfg, asof),
        "scenarios": _scenarios_document(
            borrower, pricing, result, covenants, snapshot, cfg, asof
        ),
    }


def _period_row_document(
    row: CovenantPeriod, spec: CovenantSpec, asof: str
) -> dict[str, object]:
    """Publish one per-period covenant row in the P4.1 periods shape.

    period, payment_date, actual, headroom, and status, each an envelope:
    ratio covenants publish actual and headroom at ratio scale 4 and
    min_cash keeps exact integer cents; the headroom provenance names the
    pinned type rule (limit minus actual for max, actual minus limit for
    min).
    """
    if row.actual is None or row.headroom is None:
        raise BuildError(
            f"{spec.name} period {row.period}: tested row carries no actual or headroom"
        )
    if spec.unit == "ratio":
        if not isinstance(row.actual, Decimal) or not isinstance(row.headroom, Decimal):
            raise BuildError(f"{spec.name}: ratio covenant published non-Decimal rows")
        actual: object = envelope(
            format_fixed(row.actual, _RATIO_SCALE),
            "ratio",
            scale=_RATIO_SCALE,
            provenance=_DERIVED_PERIOD_ACTUAL,
            asof=asof,
        )
        headroom: object = envelope(
            format_fixed(row.headroom, _RATIO_SCALE),
            "ratio",
            scale=_RATIO_SCALE,
            provenance=(
                _DERIVED_PERIOD_HEADROOM_MAX
                if spec.kind == "max"
                else _DERIVED_PERIOD_HEADROOM_MIN
            ),
            asof=asof,
        )
    else:
        if not isinstance(row.actual, int) or not isinstance(row.headroom, int):
            raise BuildError(f"{spec.name}: min_cash published non-integer rows")
        actual = envelope(
            row.actual,
            "usd_minor_units",
            provenance=_DERIVED_PERIOD_ACTUAL,
            asof=asof,
        )
        headroom = envelope(
            row.headroom,
            "usd_minor_units",
            provenance=_DERIVED_PERIOD_HEADROOM_MIN,
            asof=asof,
        )
    return {
        "period": envelope(
            row.period, "count", provenance=_DERIVED_SCHEDULE_PERIOD, asof=asof
        ),
        "payment_date": envelope(
            row.payment_date.isoformat(),
            "date",
            provenance=_DERIVED_SCHEDULE_DATE,
            asof=asof,
        ),
        "actual": actual,
        "headroom": headroom,
        "status": envelope(
            row.status, "status", provenance=_DERIVED_COVENANT_STATUS, asof=asof
        ),
    }


def _period_covenant_block(
    result: CovenantResult, cfg: ScenarioConfig, asof: str
) -> dict[str, object]:
    """Publish one covenant's per-period block for scenarios.json (P4.1).

    The limit publishes once per block (never per row); an applicable
    covenant publishes its tested rows in schedule order and an
    not_applicable covenant publishes the stated reason with no rows.
    """
    block: dict[str, object] = {
        "name": result.spec.name,
        "kind": result.spec.kind,
        "limit": _limit_envelope(result.spec, cfg, asof),
        "status": envelope(
            result.status, "status", provenance=_DERIVED_COVENANT_STATUS, asof=asof
        ),
    }
    if result.status == "not_applicable":
        if result.reason is None:
            raise BuildError(f"{result.spec.name}: not_applicable covenant carries no reason")
        block["reason"] = result.reason
        return block
    block["periods"] = [
        _period_row_document(row, result.spec, asof) for row in result.periods
    ]
    return block


def _period_covenants_document(
    package: tuple[CovenantResult, ...], cfg: ScenarioConfig, asof: str
) -> dict[str, object]:
    """Publish the four per-period covenant blocks of one scenario."""
    return {
        result.spec.name: _period_covenant_block(result, cfg, asof)
        for result in package
    }


def _cash_path_document(cash: tuple[int, ...], asof: str) -> list[object]:
    """Publish the per-period cash path as integer cent envelope leaves."""
    return [
        envelope(amount, "usd_minor_units", provenance=_DERIVED_CASH_PATH, asof=asof)
        for amount in cash
    ]


def _schedule_rows_document(
    borrower: Borrower, schedule: Schedule | RbfSchedule, asof: str
) -> list[object]:
    """Publish one scenario's schedule rows (amortizing or RBF, P4.2)."""
    if isinstance(schedule, RbfSchedule):
        return [_rbf_row_document(row, asof) for row in schedule.rows]
    return [_schedule_row_document(row, borrower, asof) for row in schedule.rows]


def _scenarios_payload(
    book: BorrowerBook,
    covenants: dict[str, dict[str, _StructureCovenants]],
    cfg: ScenarioConfig,
    asof: str,
) -> dict[str, object]:
    """Assemble scenarios.json: meta plus per-borrower per-period detail (P4.2).

    Borrowers sort by id; each feasible borrower publishes both scenarios
    over the drawn structure plus the three comparison structures, each
    structure carrying its four per-period covenant blocks, the scenario
    cash path, and the scenario schedule rows (the downside rbf_capped
    remittance rows come from the rebuilt schedule). Infeasible borrowers
    publish the not_applicable shape with the sizing reason per scenario.
    """
    borrowers_out: list[dict[str, object]] = []
    for borrower in sorted(book.borrowers, key=lambda item: item.borrower_id):
        structure_map = covenants.get(borrower.borrower_id)
        if structure_map is None:
            borrowers_out.append(
                {
                    "borrower_id": borrower.borrower_id,
                    "scenarios": {
                        "base": _na_cell(
                            _sizing_provenance(cfg), INFEASIBLE_REASON, asof
                        ),
                        "downside": _na_cell(
                            _sizing_provenance(cfg), INFEASIBLE_REASON, asof
                        ),
                    },
                }
            )
            continue
        scenarios_out: dict[str, object] = {}
        for scenario in ("base", "downside"):
            structures_out: dict[str, object] = {}
            for structure, evaluated in structure_map.items():
                if scenario == "base":
                    schedule = evaluated.base_schedule
                    package = evaluated.base_package
                    cash = evaluated.base_cash
                else:
                    schedule = evaluated.downside_schedule
                    package = evaluated.downside_package
                    cash = evaluated.downside_cash
                structures_out[structure] = {
                    "covenants": _period_covenants_document(package, cfg, asof),
                    "cash_path": _cash_path_document(cash, asof),
                    "schedule": _schedule_rows_document(borrower, schedule, asof),
                }
            scenarios_out[scenario] = {"structures": structures_out}
        borrowers_out.append(
            {"borrower_id": borrower.borrower_id, "scenarios": scenarios_out}
        )
    return {
        "meta": {
            "asof": asof,
            "book_sha256": envelope(
                book.book_sha256, "sha256_hex", provenance=_DERIVED_BOOK_HASH, asof=asof
            ),
        },
        "borrowers": borrowers_out,
    }


def _na_cell(provenance: str, reason: str, asof: str) -> dict[str, object]:
    """Publish a not_applicable status cell with its reason companion (6.4).

    Comparison rows carry several independently undefined cells, so each
    cell owns a small {status, reason} object instead of fighting over one
    sibling reason key in the row.
    """
    return {
        "status": envelope(
            "not_applicable", "status", provenance=provenance, asof=asof
        ),
        "reason": reason,
    }


def _comparison_shared_document(
    shared: SharedInputs,
    cfg: ScenarioConfig,
    asof: str,
) -> dict[str, object]:
    """Publish the shared block every comparison row repeats (P3.6).

    close, base rate, and all-in rate come from the committed snapshot with
    the external rate provenance; the facility and closing fee repeat the
    canonical sized capacity (or the not_applicable shape when sizing is
    infeasible, never a fabricated zero); warrant coverage is the drawn
    band value with its citation and band label.
    """
    generated = _generated_provenance(cfg)
    external = {
        "source": shared.rate_source,
        "source_timestamp": shared.rate_source_timestamp,
    }
    if shared.facility_cents is None:
        facility: object = _na_cell(_sizing_provenance(cfg), INFEASIBLE_REASON, asof)
        fee: object = _na_cell(_DERIVED_CLOSING_FEE, INFEASIBLE_REASON, asof)
    else:
        facility = envelope(
            shared.facility_cents,
            "usd_minor_units",
            provenance=_sizing_provenance(cfg),
            asof=asof,
        )
        fee = envelope(
            shared.closing_fee_cents,
            "usd_minor_units",
            provenance=_DERIVED_CLOSING_FEE,
            asof=asof,
        )
    return {
        "close": envelope(
            shared.close.isoformat(), "date", provenance=_DERIVED_CLOSE, asof=asof
        ),
        "base_rate": envelope(
            format_fixed(shared.base_rate, PERCENT_ANNUAL_SCALE),
            "percent_annual",
            scale=PERCENT_ANNUAL_SCALE,
            **external,
        ),
        "all_in_rate": envelope(
            format_fixed(shared.all_in_rate, PERCENT_ANNUAL_SCALE),
            "percent_annual",
            scale=PERCENT_ANNUAL_SCALE,
            **external,
        ),
        "facility": facility,
        "closing_fee": fee,
        "warrant_coverage": envelope(
            format_fixed(shared.warrant_coverage, 2),
            "percent",
            scale=2,
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "warrant_coverage")),
        ),
    }


def _comparison_row_document(
    row: ComparisonRow,
    borrower: Borrower,
    cfg: ScenarioConfig,
    snapshot: RateSnapshot,
    asof: str,
    first_breach: dict[str, object],
) -> dict[str, object]:
    """Publish one comparison row as envelope cells (architecture P3.5, P3.6).

    A value cell publishes its envelope; a cell compare left None publishes
    the not_applicable status with its own reason companion. The RBF total
    interest zero is a contractual zero with the pinned provenance "derived:
    RBF carries no interest term". first_breach_month is the row's own
    downside first-breach leaf computed on the row's structure schedule
    (P4.2: the phase 4 placeholder is replaced in place, so the column
    shape never moves), and every band-sourced cell carries its citation
    and band label while band_names lists every band the row draws from.
    """
    generated = _generated_provenance(cfg)
    external = {"source": snapshot.source, "source_timestamp": snapshot.source_timestamp}
    sizing_provenance = _sizing_provenance(cfg)

    if row.facility_cents is None:
        facility: object = _na_cell(sizing_provenance, INFEASIBLE_REASON, asof)
        fee: object = _na_cell(_DERIVED_CLOSING_FEE, INFEASIBLE_REASON, asof)
        receipts: object = _na_cell(_DERIVED_RECEIPTS, INFEASIBLE_REASON, asof)
        row_moic: object = _na_cell(_DERIVED_MOIC, INFEASIBLE_REASON, asof)
        total_interest: object = _na_cell(
            _DERIVED_TOTAL_INTEREST, INFEASIBLE_REASON, asof
        )
    else:
        facility = envelope(
            row.facility_cents,
            "usd_minor_units",
            provenance=sizing_provenance,
            asof=asof,
        )
        fee = envelope(
            row.closing_fee_cents,
            "usd_minor_units",
            provenance=_DERIVED_CLOSING_FEE,
            asof=asof,
        )
        receipts = envelope(
            row.total_receipts_cents,
            "usd_minor_units",
            provenance=_DERIVED_RECEIPTS,
            asof=asof,
        )
        row_moic = envelope(
            format_fixed(row.moic, 2),
            "multiple",
            scale=2,
            provenance=_DERIVED_MOIC,
            asof=asof,
        )
        interest_provenance = (
            _DERIVED_RBF_TOTAL_INTEREST
            if row.structure == STRUCTURE_RBF
            else _DERIVED_TOTAL_INTEREST
        )
        total_interest = envelope(
            row.total_interest_cents,
            "usd_minor_units",
            provenance=interest_provenance,
            asof=asof,
        )

    if row.io_months is not None:
        io_months: object = envelope(
            row.io_months,
            "count",
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "interest_only")),
        )
    else:
        io_months = _na_cell(_DERIVED_IO_NA, row.io_months_reason, asof)

    if row.min_dscr is not None:
        min_dscr: object = envelope(
            format_fixed(row.min_dscr, _DSCR_SCALE),
            "ratio",
            scale=_DSCR_SCALE,
            provenance=_DERIVED_MIN_DSCR,
            asof=asof,
        )
    else:
        min_dscr = _na_cell(_DERIVED_MIN_DSCR, row.min_dscr_reason, asof)

    irr_status = envelope(
        row.irr_status, "status", provenance=_DERIVED_IRR, asof=asof
    )
    if row.irr_status == "feasible":
        if row.irr_percent is None:
            raise BuildError(
                f"{borrower.borrower_id}: feasible comparison IRR carries no value"
            )
        # Same shape note as build._lender_irr_document: the cell is the
        # percent_annual value envelope with the status envelope attached as
        # a child, because a container holding a nested "value" envelope
        # would be read as an envelope missing its unit.
        value_envelope = envelope(
            format_fixed(row.irr_percent, PERCENT_ANNUAL_SCALE),
            "percent_annual",
            scale=PERCENT_ANNUAL_SCALE,
            **external,
        )
        value_envelope["status"] = irr_status
        lender_irr: dict[str, object] = value_envelope
    else:
        lender_irr = {"status": irr_status, "reason": row.irr_reason}

    if row.structure == "bridge":
        term_provenance = _DERIVED_TERM_BRIDGE
    elif row.structure == STRUCTURE_RBF:
        term_provenance = _DERIVED_TERM_RBF
    else:
        term_provenance = _DERIVED_TERM_AMORTIZING

    return {
        "structure": row.structure,
        "facility": facility,
        "all_in_rate": envelope(
            format_fixed(row.all_in_rate, PERCENT_ANNUAL_SCALE),
            "percent_annual",
            scale=PERCENT_ANNUAL_SCALE,
            **external,
        ),
        "term_months": envelope(
            row.term_months, "count", provenance=term_provenance, asof=asof
        ),
        "io_months": io_months,
        "total_interest": total_interest,
        "closing_fee": fee,
        "lender_irr": lender_irr,
        "moic": row_moic,
        "min_dscr": min_dscr,
        "binding_constraint": row.binding_constraint,
        "first_breach_month": first_breach,
        "warrant_coverage": envelope(
            format_fixed(row.warrant_coverage, 2),
            "percent",
            scale=2,
            provenance=generated,
            asof=asof,
            **_band_kwargs(_require_band(cfg, "warrant_coverage")),
        ),
        "total_receipts": receipts,
        "band_names": list(row.band_names),
    }


def _comparison_payload(
    book: BorrowerBook,
    pricings: dict[str, Pricing],
    sizings: dict[str, SizingResult],
    covenants: dict[str, dict[str, _StructureCovenants]],
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
    asof: str,
) -> dict[str, object]:
    """Assemble comparison.json: meta plus one three-row table per borrower.

    All 24 tables rebuild through compare.build_comparison on identical
    borrower inputs (the disclosed serviceability proxy repeats the
    canonical facility across the three rows), sorted by borrower_id, with
    meta.book_sha256 binding the document to the same generator document
    book.json carries and featured_borrower_id echoed from scenario.toml
    [site] (decisions entry "comparison.json and featured borrower").

    Each row's first_breach_month cell is that row's own downside covenant
    result on the row's own structure schedule (P4.2 in-place replacement);
    a row of an infeasible borrower keeps the sizing not_applicable shape.
    """
    tables: list[dict[str, object]] = []
    for borrower in sorted(book.borrowers, key=lambda item: item.borrower_id):
        table = build_comparison(
            borrower,
            pricings[borrower.borrower_id],
            sizings[borrower.borrower_id],
            snapshot,
            cfg,
        )
        structure_map = covenants.get(borrower.borrower_id)
        first_breach_cells: list[dict[str, object]] = []
        for row in table.rows:
            if structure_map is None:
                first_breach_cells.append(
                    _na_cell(_sizing_provenance(cfg), INFEASIBLE_REASON, asof)
                )
                continue
            evaluated = structure_map.get(row.structure)
            if evaluated is None:
                raise BuildError(
                    f"{borrower.borrower_id}: missing covenant evaluation for "
                    f"comparison structure {row.structure}"
                )
            first_breach_cells.append(
                _package_first_breach_leaf(evaluated.downside_package, asof)
            )
        tables.append(
            {
                "borrower_id": table.borrower_id,
                "shared": _comparison_shared_document(table.shared, cfg, asof),
                "rows": [
                    _comparison_row_document(
                        row, borrower, cfg, snapshot, asof, first_breach_cell
                    )
                    for row, first_breach_cell in zip(
                        table.rows, first_breach_cells, strict=True
                    )
                ],
            }
        )
    return {
        "meta": {
            "asof": asof,
            "book_sha256": envelope(
                book.book_sha256, "sha256_hex", provenance=_DERIVED_BOOK_HASH, asof=asof
            ),
            "featured_borrower_id": cfg.site.featured_borrower_id,
        },
        "tables": tables,
    }


def _strata_sizing_results(
    results: dict[str, SizingResult],
) -> dict[str, StrataSizingResult]:
    """Adapt full sizing.SizingResult records to the generator's lightweight one.

    vdebt.generator's strata predicates consume a small NamedTuple keyed by
    borrower_id (status, binding_constraint, facility_cents); the sizing
    module returns the full dataclass (plus figures and iteration counts).
    The adaptation is explicit so neither type silently drifts.
    """
    return {
        borrower_id: StrataSizingResult(
            status=result.status,
            binding_constraint=result.binding_constraint,
            facility_cents=result.facility_cents,
        )
        for borrower_id, result in results.items()
    }


def _assert_strata_coverage(manifest: dict[str, dict[str, object]]) -> None:
    """Fail the build loudly when any required stratum has no members."""
    for name in STRATA_NAMES:
        count = manifest.get(name, {}).get("count")
        if not isinstance(count, int) or count < 1:
            message = (
                f"strata manifest stratum {name!r} has count {count}; every "
                "required stratum needs at least one borrower"
            )
            print(message, file=sys.stderr)
            raise BuildError(message)


def _book_summary_document(
    sizings: dict[str, SizingResult],
    cfg: ScenarioConfig,
    asof: str,
) -> dict[str, object]:
    """Publish meta.book_summary: dashboard counts computed at build time (P4.6).

    capacity_distribution bins only feasible facilities over the configured
    capacity edges (low inclusive, high exclusive, last bin inclusive); the
    binding_constraint_mix counts every borrower's sizing binding
    constraint so the three bars sum to the committed book, beside the
    feasible and infeasible counts. Every count carries the pinned derived
    provenance with asof, and bin edges publish as usd_minor_units
    envelopes. The guards fail the build loudly if a facility ever lands
    outside the configured edges or a sizing publishes an unknown binding
    constraint, so no count can silently drop a borrower.
    """
    count_provenance = _DERIVED_BOOK_COUNT
    feasible = [
        result for result in sizings.values() if result.facility_cents is not None
    ]
    infeasible_count = len(sizings) - len(feasible)
    edges = cfg.dashboard.capacity_bin_edges
    last_index = len(edges) - 2
    distribution: list[dict[str, object]] = []
    binned = 0
    for index in range(last_index + 1):
        low = edges[index]
        high = edges[index + 1]
        count = 0
        for result in feasible:
            facility = result.facility_cents
            if facility is None:
                continue
            in_bin = low <= facility <= high if index == last_index else low <= facility < high
            if in_bin:
                count += 1
        binned += count
        distribution.append(
            {
                "bin_low": envelope(
                    low, "usd_minor_units", provenance=_DERIVED_BIN_EDGES, asof=asof
                ),
                "bin_high": envelope(
                    high, "usd_minor_units", provenance=_DERIVED_BIN_EDGES, asof=asof
                ),
                "count": envelope(
                    count, "count", provenance=count_provenance, asof=asof
                ),
            }
        )
    if binned != len(feasible):
        raise BuildError(
            f"book_summary capacity bins captured {binned} of "
            f"{len(feasible)} feasible facilities; the configured edges "
            f"{list(edges)} do not cover every sized facility"
        )
    mix = {name: 0 for name in _BINDING_CONSTRAINTS}
    for result in sizings.values():
        constraint = result.binding_constraint
        if constraint not in mix:
            raise BuildError(
                f"book_summary cannot bin unknown binding constraint {constraint!r}"
            )
        mix[constraint] += 1
    return {
        "capacity_distribution": distribution,
        "binding_constraint_mix": {
            **{
                name: envelope(
                    mix[name], "count", provenance=count_provenance, asof=asof
                )
                for name in _BINDING_CONSTRAINTS
            },
            "feasible_count": envelope(
                len(feasible), "count", provenance=count_provenance, asof=asof
            ),
            "infeasible_count": envelope(
                infeasible_count, "count", provenance=count_provenance, asof=asof
            ),
        },
    }


def _meta_document(
    book_sha256: str,
    manifest: dict[str, dict[str, object]],
    sizings: dict[str, SizingResult],
    cfg: ScenarioConfig,
    asof: str,
) -> dict[str, object]:
    """Assemble the meta block: generator pins, book hash, strata, book summary."""
    generated = _generated_provenance(cfg)
    strata_manifest: dict[str, dict[str, object]] = {}
    for name in STRATA_NAMES:
        entry = manifest[name]
        strata_manifest[name] = {
            "count": envelope(
                int(entry["count"]), "count", provenance=_DERIVED_STRATA, asof=asof
            ),
            "members": list(entry["members"]),
        }
    return {
        "generator_version": cfg.generator_version,
        "seed": envelope(cfg.seed, "count", provenance=generated, asof=asof),
        "currency": cfg.currency,
        "rounding_mode": cfg.rounding_mode,
        "book_sha256": envelope(
            book_sha256, "sha256_hex", provenance=_DERIVED_BOOK_HASH, asof=asof
        ),
        "strata_manifest": strata_manifest,
        "book_summary": _book_summary_document(sizings, cfg, asof),
    }


def _rates_document(snapshot: RateSnapshot) -> dict[str, object]:
    """Assemble the rates block with the base-rate envelope's external provenance."""
    external = {"source": snapshot.source, "source_timestamp": snapshot.source_timestamp}
    return {
        "series": snapshot.series,
        "label": snapshot.label,
        "observation_date": snapshot.observation_date.isoformat(),
        "base_rate": envelope(
            format_fixed(snapshot.base_rate, PERCENT_ANNUAL_SCALE),
            "percent_annual",
            scale=PERCENT_ANNUAL_SCALE,
            **external,
        ),
        "source": snapshot.source,
        "source_timestamp": snapshot.source_timestamp,
        "manifest_hashes": dict(sorted(snapshot.manifest_hashes.items())),
    }


def _git_sha(repo_root: Path) -> str:
    """Return HEAD's sha hex, or "unknown" when git is unavailable."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _write_build_meta(output_root: Path, repo_root: Path) -> Path:
    """Write payloads/build_meta.json with plain json (the only clock in the build).

    This file lives outside the asof directory and is excluded from byte
    identity by definition (architecture section 11): it is the only build
    output allowed to carry the wall clock and environment data.
    """
    build_meta = {
        "built_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(repo_root),
        "python_version": platform.python_version(),
    }
    path = output_root / BUILD_META_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(build_meta, indent=2, sort_keys=True, allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def run_build(
    output_root: Path | None = None,
    cfg_path: Path | None = None,
) -> int:
    """Build the canonical book, comparison, and scenarios payloads, exit 0.

    Steps in order: load scenario.toml, load the committed rate snapshot
    (manifest, label, and shape failures propagate loudly), generate the
    book, price and size every borrower, evaluate the covenant packages for
    the drawn structure plus the three comparison structures under base and
    downside, compute and assert the strata manifest, assemble the P4.2
    book.json shape (schedule kinds, returns blocks, covenants and
    scenarios summaries, meta.book_summary), validate it with
    snapshot.validate_payload (any error prints and fails the build), write
    it through snapshot.write_payload to <output_root>/<asof>/book.json,
    then assemble and validate comparison.json and scenarios.json the same
    way and write them beside it, and finally write
    <output_root>/build_meta.json with the wall clock, git SHA, and Python
    version. No payload reads a clock, so two runs into two trees are byte
    identical over everything except build_meta.json.
    """
    root = DEFAULT_OUTPUT_ROOT if output_root is None else Path(output_root)
    config_path = DEFAULT_CONFIG_PATH if cfg_path is None else Path(cfg_path)

    cfg = load_scenario(config_path)
    snapshot = load_rate_snapshot(DEFAULT_RATES_DIR, cfg.rates)
    asof = snapshot.observation_date.isoformat()

    print(
        f"base rate {format_fixed(snapshot.base_rate, PERCENT_ANNUAL_SCALE)} "
        f"percent_annual asof {asof} series {snapshot.series} "
        f"label {snapshot.label}"
    )

    book = generate_book(cfg)
    pricings: dict[str, Pricing] = {}
    sizings: dict[str, SizingResult] = {}
    for borrower in book.borrowers:
        pricing = price(borrower.spread, snapshot, cfg, borrower.lender_type)
        result = size_facility(_sizing_inputs(borrower, pricing, snapshot, cfg), cfg)
        pricings[borrower.borrower_id] = pricing
        sizings[borrower.borrower_id] = result
        answer = result.facility_cents if result.facility_cents is not None else "none"
        print(
            f"sizing {borrower.borrower_id} iterations={result.iterations} "
            f"bracket=[{result.final_lo} {result.final_hi}] "
            f"answer={answer} binding={result.binding_constraint}"
        )

    manifest = compute_strata_manifest(book, _strata_sizing_results(sizings))
    _assert_strata_coverage(manifest)

    excursion_ids = [
        borrower.borrower_id
        for borrower in book.borrowers
        if pricings[borrower.borrower_id].band_excursion == STATUS_EXCURSION
    ]
    within_count = len(book.borrowers) - len(excursion_ids)
    printed_ids = " ".join(excursion_ids) if excursion_ids else "none"
    print(
        f"band excursion: {within_count} within_band, {len(excursion_ids)} "
        f"excursion; excursion ids: {printed_ids}"
    )
    print(
        "strata manifest: "
        + " ".join(f"{name}={manifest[name]['count']}" for name in STRATA_NAMES)
    )

    # Phase 4 covenant evaluation: the drawn structure plus the three
    # comparison structures, each under base and downside on its own
    # schedule (P4.1, P4.2). Infeasible borrowers have no facility to
    # amortize, so they carry no evaluation and publish the
    # not_applicable shapes.
    covenants: dict[str, dict[str, _StructureCovenants]] = {}
    for borrower in book.borrowers:
        result = sizings[borrower.borrower_id]
        if result.facility_cents is None:
            continue
        names: list[str] = list(ROW_STRUCTURES)
        if borrower.structure not in ROW_STRUCTURES:
            names.insert(0, borrower.structure)
        covenants[borrower.borrower_id] = {
            name: _evaluate_structure_covenants(
                borrower,
                name,
                result.facility_cents,
                snapshot.observation_date,
                pricings[borrower.borrower_id],
                cfg,
            )
            for name in names
        }

    payload: dict[str, object] = {
        "meta": _meta_document(book.book_sha256, manifest, sizings, cfg, asof),
        "rates": _rates_document(snapshot),
        "borrowers": [
            _borrower_document(
                borrower,
                pricings[borrower.borrower_id],
                sizings[borrower.borrower_id],
                snapshot,
                cfg,
                asof,
                covenants.get(borrower.borrower_id, {}).get(borrower.structure),
            )
            for borrower in book.borrowers
        ],
    }

    errors = validate_payload(payload)
    if errors:
        for line in errors:
            print(f"payload validation: {line}", file=sys.stderr)
        raise BuildError(
            f"book.json failed payload validation with {len(errors)} error(s)"
        )
    print("payload validation: clean")

    book_path = root / asof / BOOK_FILE_NAME
    digest = write_payload(book_path, payload)
    print(f"wrote {book_path} sha256={digest}")

    comparison = _comparison_payload(
        book, pricings, sizings, covenants, snapshot, cfg, asof
    )
    comparison_errors = validate_payload(comparison)
    if comparison_errors:
        for line in comparison_errors:
            print(f"comparison payload validation: {line}", file=sys.stderr)
        raise BuildError(
            "comparison.json failed payload validation with "
            f"{len(comparison_errors)} error(s)"
        )
    print("comparison payload validation: clean")
    comparison_path = root / asof / COMPARISON_FILE_NAME
    comparison_digest = write_payload(comparison_path, comparison)
    print(f"wrote {comparison_path} sha256={comparison_digest}")
    print(
        f"comparison: {len(book.borrowers)} tables "
        f"featured_borrower_id={cfg.site.featured_borrower_id}"
    )

    scenarios = _scenarios_payload(book, covenants, cfg, asof)
    scenario_errors = validate_payload(scenarios)
    if scenario_errors:
        for line in scenario_errors:
            print(f"scenarios payload validation: {line}", file=sys.stderr)
        raise BuildError(
            "scenarios.json failed payload validation with "
            f"{len(scenario_errors)} error(s)"
        )
    print("scenarios payload validation: clean")
    scenarios_path = root / asof / SCENARIOS_FILE_NAME
    scenarios_digest = write_payload(scenarios_path, scenarios)
    print(f"wrote {scenarios_path} sha256={scenarios_digest}")

    meta_path = _write_build_meta(root, REPO_ROOT)
    print(f"wrote {meta_path}")
    return 0
