"""Canonical payload build orchestration (architecture P2.6, P3.6).

run_build assembles payloads/<asof>/book.json and
payloads/<asof>/comparison.json: the committed Valet rate snapshot, the
seeded 24-borrower book, per-borrower pricing, sizing, structure schedules,
returns blocks, and the three-row structure comparison tables, all
validated with snapshot.validate_payload and written through
snapshot.write_payload. asof is the rate snapshot observation date, never
the wall clock, so both payload files are byte identical across runs;
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
comparison rows for each of the 24 borrowers. The strata manifest is
computed with every sizing result wired in (adapting sizing.SizingResult to
the lightweight generator SizingResult the predicates consume), and any
stratum count below one fails the build with the stratum named.

Every numeric leaf in book.json and comparison.json is a metric envelope
per architecture section 6.4: drawn generator inputs carry the generated
provenance class plus their band citation and band label, derived values
carry a derived provenance and asof, and every rate figure in the pricing,
rates, and returns blocks carries the snapshot source and source_timestamp
(external provenance).

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
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from vdebt.compare import (
    INFEASIBLE_REASON,
    STRUCTURE_RBF,
    ComparisonRow,
    SharedInputs,
    build_comparison,
)
from vdebt.config import Band, ScenarioConfig, load_scenario
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

# Canonical artifact names (architecture P2.6 layout decision, P3.6 second file).
BOOK_FILE_NAME: str = "book.json"
COMPARISON_FILE_NAME: str = "comparison.json"
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
_DERIVED_FIRST_BREACH: str = "derived: covenant first-breach placeholder, phase 4 replaces it"

# Published ratio scale for DSCR cells (section 6.4 metric scales).
_DSCR_SCALE: int = 4


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


def _structure_schedule(
    borrower: Borrower,
    result: SizingResult,
    pricing: Pricing,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
) -> Schedule | RbfSchedule | None:
    """Build the borrower's structure schedule, or None when sizing is infeasible.

    bullet and io_then_straight_line go through schedule.build_schedule on
    the borrower's own structure, term, io months, and priced all-in rate;
    bridge goes through schedule.build_bridge_schedule at the pinned tenor;
    rbf_capped goes through schedule.build_rbf_schedule on the sized
    advance (the disclosed advance proxy) over the pinned horizon with the
    drawn cap multiple and the generator revenue path (P3.1, P3.2). An
    infeasible borrower has no facility to amortize, so there is no
    schedule at all (P3.6).
    """
    if result.facility_cents is None:
        return None
    facility = result.facility_cents
    close = snapshot.observation_date
    if borrower.structure == "rbf_capped":
        horizon = cfg.generator.rbf_horizon_periods
        return build_rbf_schedule(
            facility,
            close,
            horizon,
            cfg.generator.rbf_remittance_rate_pct,
            borrower.rbf_cap,
            _rbf_revenue_path(borrower, horizon),
        )
    if borrower.structure == "bridge":
        return build_bridge_schedule(
            facility,
            close,
            cfg.generator.bridge_tenor_months,
            pricing.all_in_rate,
        )
    return build_schedule(
        borrower.structure,
        facility,
        close,
        borrower.term_months,
        borrower.io_months,
        pricing.all_in_rate,
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


def _borrower_document(
    borrower: Borrower,
    pricing: Pricing,
    result: SizingResult,
    snapshot: RateSnapshot,
    cfg: ScenarioConfig,
    asof: str,
) -> dict[str, object]:
    """Assemble one borrower entry of book.json per the P3.6 shape.

    The structure schedule builds once and feeds both the schedule union
    member and the returns block, so the published rows and the published
    cash flows can never disagree about dates or amounts.
    """
    schedule = _structure_schedule(borrower, result, pricing, snapshot, cfg)
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
) -> dict[str, object]:
    """Publish one comparison row as envelope cells (architecture P3.5, P3.6).

    A value cell publishes its envelope; a cell compare left None publishes
    the not_applicable status with its own reason companion. The RBF total
    interest zero is a contractual zero with the pinned provenance "derived:
    RBF carries no interest term", first_breach_month is always the phase 4
    placeholder, and every band-sourced cell carries its citation and band
    label while band_names lists every band the row draws from.
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
        "first_breach_month": {
            "status": envelope(
                row.first_breach_status,
                "status",
                provenance=_DERIVED_FIRST_BREACH,
                asof=asof,
            ),
            "reason": row.first_breach_reason,
        },
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
        tables.append(
            {
                "borrower_id": table.borrower_id,
                "shared": _comparison_shared_document(table.shared, cfg, asof),
                "rows": [
                    _comparison_row_document(row, borrower, cfg, snapshot, asof)
                    for row in table.rows
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


def _meta_document(
    book_sha256: str,
    manifest: dict[str, dict[str, object]],
    cfg: ScenarioConfig,
    asof: str,
) -> dict[str, object]:
    """Assemble the meta block: generator pins, book hash, strata manifest."""
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
    """Build the canonical book.json and comparison.json payloads, exit status 0.

    Steps in order: load scenario.toml, load the committed rate snapshot
    (manifest, label, and shape failures propagate loudly), generate the
    book, price and size every borrower, compute and assert the strata
    manifest, assemble the P3.6 book.json shape (schedule kinds plus
    returns blocks), validate it with snapshot.validate_payload (any error
    prints and fails the build), write it through snapshot.write_payload to
    <output_root>/<asof>/book.json, then assemble and validate
    comparison.json the same way and write it to
    <output_root>/<asof>/comparison.json, and finally write
    <output_root>/build_meta.json with the wall clock, git SHA, and Python
    version. Neither payload reads a clock, so two runs into two trees are
    byte identical over everything except build_meta.json.
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

    payload: dict[str, object] = {
        "meta": _meta_document(book.book_sha256, manifest, cfg, asof),
        "rates": _rates_document(snapshot),
        "borrowers": [
            _borrower_document(
                borrower,
                pricings[borrower.borrower_id],
                sizings[borrower.borrower_id],
                snapshot,
                cfg,
                asof,
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

    comparison = _comparison_payload(book, pricings, sizings, snapshot, cfg, asof)
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

    meta_path = _write_build_meta(root, REPO_ROOT)
    print(f"wrote {meta_path}")
    return 0
