"""Seeded borrower book generator (architecture P2.2).

One instance of random.Random(seed) per book consumes exactly 14 .random()
uniforms per borrower in the frozen section 11 order (lender_type, structure,
pre_revenue, last_round, arr, net_burn, cash_coverage_months, monthly_growth,
gross_margin, warrant_coverage, spread, closing_fee, rbf_cap, io_months) over
SYN-0001 through SYN-0024 in ascending id: 336 uniforms in total. A strata slot
override never skips a draw: it replaces the computed value after the draw, so
the stream stays aligned across the whole book.

Continuous draws follow x = lo + Decimal(repr(u)) * (hi - lo) in MONEY_CONTEXT
with band bounds already in the storage unit (money bands are integer cents per
the decisions log) and are quantized exactly once at the storage boundary at
the pinned scale. Discrete draws sort labels by label text and take the first
label whose cumulative Decimal weight exceeds Decimal(repr(u)); the loader has
already asserted each weight table sums to exactly 1.

Derived values are never drawn. cash_cents is net burn times cash coverage
quantized once to cents. term_months is io_months plus the pinned amortizing
count for bullet and io_then_straight_line, the pinned RBF horizon for
rbf_capped, and the pinned bridge tenor for bridge. One documented
consistency step (not a skipped draw) runs after the 14 draws and before
strata overrides: when pre_revenue holds, arr_cents is forced to 0
(spec 10.1), so the consumed arr uniform's value is discarded by the
rule; when pre_revenue does not hold, the drawn ARR stands (its band
minimum is positive, so it is never 0). The revenue path grows ARR / 12
by the monthly growth rate per period with one quantize per period
(all zero cents for pre-revenue borrowers), and CFADS applies the gross margin
to the quantized revenue. Strata slot overrides replace named raw values after
all 14 draws, derived values recompute from the final values, and every final
value is then asserted inside its band with one documented exception: ARR 0
cents is legal exactly when pre_revenue holds. Overrides touching lender_type
must pin the full consistent set (warrant_coverage and spread), because those
values were drawn under the original lender band.

book_sha256 is the sha256 hex digest of canonical_dumps over build_document(),
the generator's direct output document (borrower inputs and paths only, before
pricing, sizing, and payload envelopes), so a rate refresh never invalidates
it. Strata predicates split by data source: pre_revenue and zero_warrant_bank
are book-only; infeasible_at_zero_draw, runway_bound, and dscr_bound read
sizing status; rbf_shortfall reads the sized facility from the same mapping.
size_facility lands in a later slice, so compute_strata_manifest takes
sizing_results keyed by borrower_id and skips borrowers absent from it.
"""

import hashlib
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import NamedTuple

from vdebt.config import Band, ScenarioConfig, SlotOverride
from vdebt.money import MONEY_CONTEXT, format_fixed, quantize
from vdebt.snapshot import canonical_dumps

# Pinned book shape: SYN-0001 to SYN-0024, 14 uniforms each (section 11).
BOOK_SIZE: int = 24
DRAWS_PER_BORROWER: int = 14
MONTHS_PER_YEAR: int = 12

# Label vocabularies from the architecture P2.2 draw table, in sorted label
# text order. The discrete draw consumes weight-table keys, and the band
# assertion re-checks every final label against these pinned tuples so a
# mistyped weight label fails loudly.
_LENDER_TYPES: tuple[str, ...] = ("bank", "non_bank")
_STRUCTURES: tuple[str, ...] = ("bridge", "bullet", "io_then_straight_line", "rbf_capped")
_PRE_REVENUE_LABELS: tuple[str, ...] = ("no", "yes")
_AMORTIZING_STRUCTURES: frozenset[str] = frozenset({"bullet", "io_then_straight_line"})

# Overrides touching lender_type must pin the full consistent set: warrant
# coverage and spread were drawn under the original lender band.
_LENDER_CONSISTENT_KEYS: tuple[str, ...] = ("warrant_coverage_pct", "spread_pct")

# Storage scale per Decimal field of Borrower, for the canonical document.
_DECIMAL_SCALES: dict[str, int] = {
    "cash_coverage_months": 1,
    "monthly_growth": 4,
    "gross_margin": 2,
    "warrant_coverage": 2,
    "spread": 4,
    "closing_fee": 2,
    "rbf_cap": 2,
}


class SizingResult(NamedTuple):
    """Upstream sizing outcome consumed by the strata predicates.

    status uses the snapshot status vocabulary; phase 2 sizing emits
    "feasible" or "infeasible". binding_constraint is "runway", "dscr", or
    "band_cap" when feasible, and the failing constraint when infeasible.
    facility_cents is the sized facility in integer cents; rbf_shortfall
    requires it and skips borrowers where it is None (sizing not yet run or
    no solution). Because SizingResult is a NamedTuple, plain tuples
    ("feasible", "runway") or ("feasible", "runway", 12345678) are accepted
    by the predicates and coerced on read.
    """

    status: str
    binding_constraint: str | None = None
    facility_cents: int | None = None


@dataclass(frozen=True)
class Borrower:
    """One synthetic borrower: drawn inputs plus derived cash, term, paths.

    Money fields are integer cents. Decimal fields are pre-quantized at their
    storage boundary: cash coverage at scale 1, monthly growth and spread at
    scale 4, gross margin, warrant coverage, closing fee, and rbf cap at
    scale 2. revenue_path and cfads_path hold one integer cent per period for
    t = 1 to term_months.
    """

    borrower_id: str
    lender_type: str
    structure: str
    pre_revenue: bool
    last_round_cents: int
    arr_cents: int
    net_burn_cents: int
    cash_cents: int
    cash_coverage_months: Decimal
    monthly_growth: Decimal
    gross_margin: Decimal
    warrant_coverage: Decimal
    spread: Decimal
    closing_fee: Decimal
    rbf_cap: Decimal
    io_months: int
    term_months: int
    revenue_path: tuple[int, ...]
    cfads_path: tuple[int, ...]


@dataclass(frozen=True)
class BorrowerBook:
    """The full seeded book plus its pre-pricing hash.

    rbf_horizon_periods and rbf_remittance_rate_pct ride on the book (copied
    from [generator]) so the rbf_shortfall predicate keeps the two-argument
    signature compute_strata_manifest(book, sizing_results). They are
    scenario parameters, not borrower inputs, so build_document() excludes
    them and book_sha256 covers borrower inputs and paths only.
    """

    borrowers: tuple[Borrower, ...]
    book_sha256: str
    rbf_horizon_periods: int
    rbf_remittance_rate_pct: Decimal

    def build_document(self) -> dict[str, object]:
        """Return the exact dict hashed into book_sha256 (inputs and paths)."""
        return _book_document(self.borrowers)


@dataclass
class _RawDraws:
    """The 14 drawn (or overridden) values for one borrower, mutable."""

    lender_type: str
    structure: str
    pre_revenue: bool
    last_round_cents: int
    arr_cents: int
    net_burn_cents: int
    cash_coverage_months: Decimal
    monthly_growth: Decimal
    gross_margin: Decimal
    warrant_coverage: Decimal
    spread: Decimal
    closing_fee: Decimal
    rbf_cap: Decimal
    io_months: int


def _band(cfg: ScenarioConfig, name: str) -> Band:
    """Return the required band or raise ValueError naming the missing table."""
    band = cfg.bands.get(name)
    if band is None:
        raise ValueError(f"scenario.toml is missing required band [bands.{name}]")
    return band


def _bound(band: Band, key: str) -> Decimal:
    """Return one extra bound key of a band or raise ValueError naming it."""
    value = band.extras.get(key)
    if value is None:
        raise ValueError(f"band {band.band} is missing bound key {key!r}")
    return value


def _warrant_bounds(cfg: ScenarioConfig, lender_type: str) -> tuple[Decimal, Decimal]:
    """Return the lender-specific warrant coverage bounds (bank or non-bank)."""
    band = _band(cfg, "warrant_coverage")
    prefix = "bank" if lender_type == "bank" else "nonbank"
    return _bound(band, f"{prefix}_min_pct"), _bound(band, f"{prefix}_max_pct")


def _draw_label(u: float, weights: Mapping[str, Decimal]) -> str:
    """First label whose cumulative Decimal weight exceeds Decimal(repr(u)).

    Labels are consumed in sorted label text order (section 11). Weight
    tables sum to exactly 1 (asserted by the loader), so a uniform in [0, 1)
    always matches before the loop ends.
    """
    threshold = Decimal(repr(u))
    cumulative = Decimal(0)
    for label in sorted(weights):
        cumulative = MONEY_CONTEXT.add(cumulative, weights[label])
        if threshold < cumulative:
            return label
    raise ValueError(
        f"uniform {threshold} not below the cumulative weight sum {cumulative}; "
        "weight tables must sum to exactly 1"
    )


def _draw_continuous(u: float, low: Decimal, high: Decimal, scale: int) -> Decimal:
    """Linear inverse-CDF draw: low + Decimal(repr(u)) * (high - low).

    The arithmetic runs in MONEY_CONTEXT (prec 28, ROUND_HALF_UP) and the
    result is quantized exactly once at the storage boundary at scale.
    """
    if not 0.0 <= u < 1.0:
        raise ValueError(f"uniform must be in [0, 1), got {u!r}")
    if low > high:
        raise ValueError(f"band bounds inverted: low {low} > high {high}")
    span = MONEY_CONTEXT.subtract(high, low)
    offset = MONEY_CONTEXT.multiply(Decimal(repr(u)), span)
    drawn = MONEY_CONTEXT.add(low, offset)
    return quantize(drawn, scale)


def _draw_band_value(u: float, band: Band) -> Decimal:
    """Draw inside a band and quantize at the band's pinned storage scale."""
    return _draw_continuous(u, band.min, band.max, band.scale)


def _draw_band_cents(u: float, band: Band) -> int:
    """Draw inside a money band (bounds already integer cents, scale 0)."""
    return int(_draw_band_value(u, band))


def _draw_io_months(u: float, band: Band) -> int:
    """Draw integer months: quantize at scale 0, then clamp into the band.

    The interest-only band declares scale 1 for its bounds, but io_months
    stores whole months (architecture P2.2 draw 14), so the storage boundary
    here is scale 0 with a clamp into [min_months, max_months].
    """
    value = int(_draw_continuous(u, band.min, band.max, 0))
    low = int(quantize(band.min, 0))
    high = int(quantize(band.max, 0))
    return max(low, min(high, value))


def _parse_choice(text: str, allowed: tuple[str, ...]) -> str:
    """Parse an override label, rejecting anything outside the vocabulary."""
    if text not in allowed:
        raise ValueError(f"override label must be one of {allowed}, got {text!r}")
    return text


def _parse_pre_revenue(text: str) -> bool:
    """Parse the pre_revenue override label "yes" or "no" into a bool."""
    if text == "yes":
        return True
    if text == "no":
        return False
    raise ValueError(f"pre_revenue override must be 'yes' or 'no', got {text!r}")


def _parse_integral(text: str) -> int:
    """Parse an override to an exact integer (money cents or whole months)."""
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"override is not a valid decimal string: {text!r}") from exc
    if not value.is_finite():
        raise ValueError(f"override must be finite, got {text!r}")
    if value != value.to_integral_value():
        raise ValueError(f"override must be a whole integer, got {text!r}")
    return int(value)


def _parse_scaled(text: str, scale: int) -> Decimal:
    """Parse an override and quantize once at its storage scale."""
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"override is not a valid decimal string: {text!r}") from exc
    if not value.is_finite():
        raise ValueError(f"override must be finite, got {text!r}")
    return quantize(value, scale)


# Override key to (Borrower/_RawDraws field name, parser). Every pinned slot
# key in scenario.toml appears here; an unknown key fails loudly instead of
# silently doing nothing.
_OVERRIDE_SPECS: dict[str, tuple[str, Callable[[str], object]]] = {
    "lender_type": ("lender_type", partial(_parse_choice, allowed=_LENDER_TYPES)),
    "structure": ("structure", partial(_parse_choice, allowed=_STRUCTURES)),
    "pre_revenue": ("pre_revenue", _parse_pre_revenue),
    "last_round": ("last_round_cents", _parse_integral),
    "arr": ("arr_cents", _parse_integral),
    "net_burn": ("net_burn_cents", _parse_integral),
    "cash_coverage_months": ("cash_coverage_months", partial(_parse_scaled, scale=1)),
    "monthly_growth_pct": ("monthly_growth", partial(_parse_scaled, scale=4)),
    "gross_margin_pct": ("gross_margin", partial(_parse_scaled, scale=2)),
    "warrant_coverage_pct": ("warrant_coverage", partial(_parse_scaled, scale=2)),
    "spread_pct": ("spread", partial(_parse_scaled, scale=4)),
    "closing_fee_pct": ("closing_fee", partial(_parse_scaled, scale=2)),
    "rbf_cap": ("rbf_cap", partial(_parse_scaled, scale=2)),
    "io_months": ("io_months", _parse_integral),
}


def _apply_overrides(borrower_id: str, raw: _RawDraws, slot: SlotOverride) -> None:
    """Replace named raw values with the pinned slot recipe, in place.

    The 14 uniforms are already consumed before this runs; the override map
    replaces computed values only, never draws. Overrides touching
    lender_type must pin the full consistent set (warrant coverage and
    spread), because those were drawn under the original lender band.
    """
    overrides = slot.overrides
    if "lender_type" in overrides:
        missing = sorted(
            key for key in _LENDER_CONSISTENT_KEYS if key not in overrides
        )
        if missing:
            raise ValueError(
                f"{borrower_id}: overrides touching lender_type must also pin "
                f"{', '.join(missing)} (the full consistent set)"
            )
    for key, text in overrides.items():
        spec = _OVERRIDE_SPECS.get(key)
        if spec is None:
            raise ValueError(f"{borrower_id}: unknown strata override key {key!r}")
        field_name, parser = spec
        setattr(raw, field_name, parser(text))


def _term_months(structure: str, io_months: int, cfg: ScenarioConfig) -> int:
    """Derived term per architecture P2.2 (never a draw).

    bullet and io_then_straight_line get io plus the pinned amortizing
    periods (48 to 54 at any io in 12 to 18, inside the 36 to 60 term band);
    rbf_capped gets the pinned horizon; bridge gets the pinned tenor.
    """
    if structure == "rbf_capped":
        return cfg.generator.rbf_horizon_periods
    if structure == "bridge":
        return cfg.generator.bridge_tenor_months
    return io_months + cfg.generator.term_amortizing_periods


def _paths(raw: _RawDraws, term_months: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Build the revenue and CFADS paths, one quantize per period.

    revenue_t = quantize(base * (1 + monthly_growth)^(t - 1), 0) with
    base = arr / 12 in MONEY_CONTEXT; pre-revenue borrowers get an all-zero
    revenue path (with arr_cents forced to 0 by the consistency rule the
    generic branch would yield the same zeros, so the explicit branch is
    kept for identical behavior). cfads_t = quantize(revenue_t *
    gross_margin / 100, 0).
    """
    if raw.pre_revenue:
        revenue = [0] * term_months
    else:
        base = MONEY_CONTEXT.divide(Decimal(raw.arr_cents), Decimal(MONTHS_PER_YEAR))
        growth_fraction = MONEY_CONTEXT.divide(raw.monthly_growth, Decimal(100))
        factor = MONEY_CONTEXT.add(Decimal(1), growth_fraction)
        revenue = []
        for period in range(1, term_months + 1):
            grown = MONEY_CONTEXT.power(factor, Decimal(period - 1))
            revenue.append(int(quantize(MONEY_CONTEXT.multiply(base, grown), 0)))
    cfads = [
        int(
            quantize(
                MONEY_CONTEXT.divide(
                    MONEY_CONTEXT.multiply(Decimal(amount), raw.gross_margin),
                    Decimal(100),
                ),
                0,
            )
        )
        for amount in revenue
    ]
    return tuple(revenue), tuple(cfads)


def _require_choice(
    borrower_id: str, name: str, value: str, allowed: tuple[str, ...]
) -> None:
    """Raise ValueError when a label value falls outside its pinned set."""
    if value not in allowed:
        raise ValueError(f"{borrower_id}: {name} {value!r} must be one of {allowed}")


def _require_range(
    borrower_id: str,
    name: str,
    value: Decimal,
    low: Decimal,
    high: Decimal,
    band_name: str,
) -> None:
    """Raise ValueError when a value falls outside an inclusive band range."""
    if value < low or value > high:
        raise ValueError(
            f"{borrower_id}: {name} {value} outside band {band_name} "
            f"range [{low}, {high}]"
        )


def _require_band(borrower_id: str, name: str, value: Decimal, band: Band) -> None:
    """Raise ValueError when a value falls outside the band's own bounds."""
    _require_range(borrower_id, name, value, band.min, band.max, band.band)


def _assert_bands(borrower: Borrower, cfg: ScenarioConfig) -> None:
    """Assert every final value inside its band (architecture P2.2 step 4).

    One documented exception: arr_cents == 0 is legal exactly when
    pre_revenue holds. The term band covers amortizing structures only; the
    pinned RBF horizon and bridge tenor are tenors by design, not terms, and
    are asserted equal to their [generator] pins instead.
    """
    borrower_id = borrower.borrower_id
    _require_choice(borrower_id, "lender_type", borrower.lender_type, _LENDER_TYPES)
    _require_choice(borrower_id, "structure", borrower.structure, _STRUCTURES)

    _require_band(
        borrower_id,
        "last_round_cents",
        Decimal(borrower.last_round_cents),
        _band(cfg, "last_round"),
    )
    if borrower.arr_cents == 0:
        if not borrower.pre_revenue:
            raise ValueError(
                f"{borrower_id}: arr_cents 0 is legal only when pre_revenue "
                "holds (the documented exception)"
            )
    else:
        _require_band(
            borrower_id, "arr_cents", Decimal(borrower.arr_cents), _band(cfg, "arr")
        )
    _require_band(
        borrower_id,
        "net_burn_cents",
        Decimal(borrower.net_burn_cents),
        _band(cfg, "net_burn"),
    )
    _require_band(
        borrower_id,
        "cash_coverage_months",
        borrower.cash_coverage_months,
        _band(cfg, "cash_coverage_months"),
    )
    _require_band(
        borrower_id, "monthly_growth", borrower.monthly_growth, _band(cfg, "revenue_growth_monthly")
    )
    _require_band(
        borrower_id, "gross_margin", borrower.gross_margin, _band(cfg, "gross_margin")
    )

    warrant_band = _band(cfg, "warrant_coverage")
    warrant_low, warrant_high = _warrant_bounds(cfg, borrower.lender_type)
    _require_range(
        borrower_id,
        "warrant_coverage",
        borrower.warrant_coverage,
        warrant_low,
        warrant_high,
        warrant_band.band,
    )
    spread_band = _band(
        cfg, "pricing_bank" if borrower.lender_type == "bank" else "pricing_nonbank"
    )
    _require_range(
        borrower_id,
        "spread",
        borrower.spread,
        spread_band.min,
        spread_band.max,
        spread_band.band,
    )
    _require_band(borrower_id, "closing_fee", borrower.closing_fee, _band(cfg, "closing_fee"))
    _require_band(borrower_id, "rbf_cap", borrower.rbf_cap, _band(cfg, "rbf_cap"))
    interest_band = _band(cfg, "interest_only")
    _require_range(
        borrower_id,
        "io_months",
        Decimal(borrower.io_months),
        interest_band.min,
        interest_band.max,
        interest_band.band,
    )

    if borrower.structure in _AMORTIZING_STRUCTURES:
        term_band = _band(cfg, "term")
        _require_range(
            borrower_id,
            "term_months",
            Decimal(borrower.term_months),
            term_band.min,
            term_band.max,
            term_band.band,
        )
    elif borrower.structure == "rbf_capped":
        if borrower.term_months != cfg.generator.rbf_horizon_periods:
            raise ValueError(
                f"{borrower_id}: term_months {borrower.term_months} must equal "
                f"rbf_horizon_periods {cfg.generator.rbf_horizon_periods}"
            )
    elif borrower.term_months != cfg.generator.bridge_tenor_months:
        raise ValueError(
            f"{borrower_id}: term_months {borrower.term_months} must equal "
            f"bridge_tenor_months {cfg.generator.bridge_tenor_months}"
        )

    if len(borrower.revenue_path) != borrower.term_months:
        raise ValueError(
            f"{borrower_id}: revenue_path length {len(borrower.revenue_path)} "
            f"must equal term_months {borrower.term_months}"
        )
    if len(borrower.cfads_path) != borrower.term_months:
        raise ValueError(
            f"{borrower_id}: cfads_path length {len(borrower.cfads_path)} "
            f"must equal term_months {borrower.term_months}"
        )


def _build_borrower(borrower_id: str, raw: _RawDraws, cfg: ScenarioConfig) -> Borrower:
    """Assemble one Borrower: derived cash, term, and paths, then band checks."""
    cash_cents = int(
        quantize(
            MONEY_CONTEXT.multiply(Decimal(raw.net_burn_cents), raw.cash_coverage_months),
            0,
        )
    )
    term_months = _term_months(raw.structure, raw.io_months, cfg)
    revenue_path, cfads_path = _paths(raw, term_months)
    borrower = Borrower(
        borrower_id=borrower_id,
        lender_type=raw.lender_type,
        structure=raw.structure,
        pre_revenue=raw.pre_revenue,
        last_round_cents=raw.last_round_cents,
        arr_cents=raw.arr_cents,
        net_burn_cents=raw.net_burn_cents,
        cash_cents=cash_cents,
        cash_coverage_months=raw.cash_coverage_months,
        monthly_growth=raw.monthly_growth,
        gross_margin=raw.gross_margin,
        warrant_coverage=raw.warrant_coverage,
        spread=raw.spread,
        closing_fee=raw.closing_fee,
        rbf_cap=raw.rbf_cap,
        io_months=raw.io_months,
        term_months=term_months,
        revenue_path=revenue_path,
        cfads_path=cfads_path,
    )
    _assert_bands(borrower, cfg)
    return borrower


def generate_book(cfg: ScenarioConfig) -> BorrowerBook:
    """Generate the pinned 24-borrower book for cfg.seed.

    Consumes exactly DRAWS_PER_BORROWER .random() uniforms per borrower in
    the frozen section 11 order (BOOK_SIZE * 14 = 336 uniforms in total,
    even when a strata slot overrides the resulting value), applies each
    slot's override map after that borrower's 14 draws, recomputes derived
    values, asserts every final value inside its band, and hashes the
    pre-pricing document into book_sha256.
    """
    rng = random.Random(cfg.seed)
    slots = {slot.borrower_id: slot for slot in cfg.generator.strata_slots}

    last_round_band = _band(cfg, "last_round")
    arr_band = _band(cfg, "arr")
    net_burn_band = _band(cfg, "net_burn")
    cash_coverage_band = _band(cfg, "cash_coverage_months")
    growth_band = _band(cfg, "revenue_growth_monthly")
    margin_band = _band(cfg, "gross_margin")
    warrant_band = _band(cfg, "warrant_coverage")
    closing_fee_band = _band(cfg, "closing_fee")
    rbf_cap_band = _band(cfg, "rbf_cap")
    interest_band = _band(cfg, "interest_only")

    borrowers: list[Borrower] = []
    for number in range(1, BOOK_SIZE + 1):
        borrower_id = f"SYN-{number:04d}"

        # Frozen section 11 draw order: 14 uniforms per borrower, always.
        u_lender_type = rng.random()
        u_structure = rng.random()
        u_pre_revenue = rng.random()
        u_last_round = rng.random()
        u_arr = rng.random()
        u_net_burn = rng.random()
        u_cash_coverage_months = rng.random()
        u_monthly_growth = rng.random()
        u_gross_margin = rng.random()
        u_warrant_coverage = rng.random()
        u_spread = rng.random()
        u_closing_fee = rng.random()
        u_rbf_cap = rng.random()
        u_io_months = rng.random()

        lender_type = _draw_label(u_lender_type, cfg.generator.weights["lender_type"])
        _require_choice(borrower_id, "lender_type", lender_type, _LENDER_TYPES)
        structure = _draw_label(u_structure, cfg.generator.weights["structure"])
        _require_choice(borrower_id, "structure", structure, _STRUCTURES)
        pre_revenue_label = _draw_label(
            u_pre_revenue, cfg.generator.weights["pre_revenue"]
        )
        _require_choice(borrower_id, "pre_revenue", pre_revenue_label, _PRE_REVENUE_LABELS)

        warrant_low, warrant_high = _warrant_bounds(cfg, lender_type)
        spread_band = _band(
            cfg, "pricing_bank" if lender_type == "bank" else "pricing_nonbank"
        )

        raw = _RawDraws(
            lender_type=lender_type,
            structure=structure,
            pre_revenue=pre_revenue_label == "yes",
            last_round_cents=_draw_band_cents(u_last_round, last_round_band),
            arr_cents=_draw_band_cents(u_arr, arr_band),
            net_burn_cents=_draw_band_cents(u_net_burn, net_burn_band),
            cash_coverage_months=_draw_band_value(u_cash_coverage_months, cash_coverage_band),
            monthly_growth=_draw_band_value(u_monthly_growth, growth_band),
            gross_margin=_draw_band_value(u_gross_margin, margin_band),
            warrant_coverage=_draw_continuous(
                u_warrant_coverage, warrant_low, warrant_high, warrant_band.scale
            ),
            spread=_draw_continuous(
                u_spread, spread_band.min, spread_band.max, spread_band.scale
            ),
            closing_fee=_draw_band_value(u_closing_fee, closing_fee_band),
            rbf_cap=_draw_band_value(u_rbf_cap, rbf_cap_band),
            io_months=_draw_io_months(u_io_months, interest_band),
        )

        # Consistency step, not a skipped draw: all 14 uniforms (including
        # u_arr) are already consumed above, then spec 10.1 forces a
        # pre-revenue borrower's ARR to 0 cents, discarding the drawn band
        # value. A non-flagged borrower keeps the drawn ARR, whose band
        # minimum is positive so it can never be 0.
        if raw.pre_revenue:
            raw.arr_cents = 0

        slot = slots.get(borrower_id)
        if slot is not None:
            _apply_overrides(borrower_id, raw, slot)
        borrowers.append(_build_borrower(borrower_id, raw, cfg))

    frozen = tuple(borrowers)
    document = _book_document(frozen)
    book_sha256 = hashlib.sha256(canonical_dumps(document).encode("utf-8")).hexdigest()
    return BorrowerBook(
        borrowers=frozen,
        book_sha256=book_sha256,
        rbf_horizon_periods=cfg.generator.rbf_horizon_periods,
        rbf_remittance_rate_pct=cfg.generator.rbf_remittance_rate_pct,
    )


def _fixed(value: Decimal, field_name: str) -> str:
    """Render one Decimal field as a fixed-scale string at its storage scale."""
    return format_fixed(value, _DECIMAL_SCALES[field_name])


def _borrower_document(borrower: Borrower) -> dict[str, object]:
    """JSON-safe view of one borrower: inputs and paths only, no envelopes.

    Integers stay JSON numbers; non-integer Decimals become fixed-scale
    strings at their storage scale (section 6.4 canonical JSON), so floats
    never appear in the hashed document.
    """
    return {
        "arr_cents": borrower.arr_cents,
        "borrower_id": borrower.borrower_id,
        "cash_cents": borrower.cash_cents,
        "cash_coverage_months": _fixed(borrower.cash_coverage_months, "cash_coverage_months"),
        "cfads_path": list(borrower.cfads_path),
        "closing_fee": _fixed(borrower.closing_fee, "closing_fee"),
        "gross_margin": _fixed(borrower.gross_margin, "gross_margin"),
        "io_months": borrower.io_months,
        "last_round_cents": borrower.last_round_cents,
        "lender_type": borrower.lender_type,
        "monthly_growth": _fixed(borrower.monthly_growth, "monthly_growth"),
        "net_burn_cents": borrower.net_burn_cents,
        "pre_revenue": borrower.pre_revenue,
        "rbf_cap": _fixed(borrower.rbf_cap, "rbf_cap"),
        "revenue_path": list(borrower.revenue_path),
        "spread": _fixed(borrower.spread, "spread"),
        "structure": borrower.structure,
        "term_months": borrower.term_months,
        "warrant_coverage": _fixed(borrower.warrant_coverage, "warrant_coverage"),
    }


def _book_document(borrowers: tuple[Borrower, ...]) -> dict[str, object]:
    """The generator's direct output document: borrower inputs and paths."""
    return {"borrowers": [_borrower_document(item) for item in borrowers]}


# The six strata in pinned manifest order (no hash-order iteration anywhere).
STRATA_NAMES: tuple[str, ...] = (
    "pre_revenue",
    "zero_warrant_bank",
    "infeasible_at_zero_draw",
    "runway_bound",
    "dscr_bound",
    "rbf_shortfall",
)


def predicate_pre_revenue(book: BorrowerBook) -> list[str]:
    """Book-only stratum: members with ARR pinned to 0 cents (spec 10.1)."""
    return sorted(item.borrower_id for item in book.borrowers if item.arr_cents == 0)


def predicate_zero_warrant_bank(book: BorrowerBook) -> list[str]:
    """Book-only stratum: bank borrowers with warrant coverage 0.00 percent."""
    return sorted(
        item.borrower_id
        for item in book.borrowers
        if item.lender_type == "bank" and item.warrant_coverage == 0
    )


def _sizing_entry(
    sizing_results: Mapping[str, SizingResult], borrower_id: str
) -> SizingResult | None:
    """Return one sizing entry, coercing plain (status, constraint[, facility])."""
    entry = sizing_results.get(borrower_id)
    if entry is None:
        return None
    if isinstance(entry, SizingResult):
        return entry
    if isinstance(entry, tuple) and 2 <= len(entry) <= 3:
        return SizingResult(*entry)
    raise ValueError(
        f"sizing_results[{borrower_id!r}] must be a SizingResult or a "
        f"(status, binding_constraint[, facility_cents]) tuple, got {entry!r}"
    )


def predicate_infeasible_at_zero_draw(
    book: BorrowerBook, sizing_results: Mapping[str, SizingResult]
) -> list[str]:
    """Sizing-based stratum: status "infeasible" (zero-draw infeasibility).

    Borrowers absent from sizing_results are not counted, so this is empty
    until slice 5 sizing output is wired in.
    """
    members: list[str] = []
    for item in book.borrowers:
        entry = _sizing_entry(sizing_results, item.borrower_id)
        if entry is not None and entry.status == "infeasible":
            members.append(item.borrower_id)
    return sorted(members)


def predicate_runway_bound(
    book: BorrowerBook, sizing_results: Mapping[str, SizingResult]
) -> list[str]:
    """Sizing-based stratum: status "feasible" with binding "runway"."""
    members: list[str] = []
    for item in book.borrowers:
        entry = _sizing_entry(sizing_results, item.borrower_id)
        if (
            entry is not None
            and entry.status == "feasible"
            and entry.binding_constraint == "runway"
        ):
            members.append(item.borrower_id)
    return sorted(members)


def predicate_dscr_bound(
    book: BorrowerBook, sizing_results: Mapping[str, SizingResult]
) -> list[str]:
    """Sizing-based stratum: status "feasible" with binding "dscr"."""
    members: list[str] = []
    for item in book.borrowers:
        entry = _sizing_entry(sizing_results, item.borrower_id)
        if (
            entry is not None
            and entry.status == "feasible"
            and entry.binding_constraint == "dscr"
        ):
            members.append(item.borrower_id)
    return sorted(members)


def predicate_rbf_shortfall(
    book: BorrowerBook, sizing_results: Mapping[str, SizingResult]
) -> list[str]:
    """RBF remittance shortfall over the pinned horizon (architecture P2.2).

    Signature note for slices 7 and 8: this predicate consumes facility_cents
    from the same sizing_results mapping the status predicates use. A member
    is an rbf_capped borrower whose projected remittance, the sum of revenue
    over the first rbf_horizon_periods periods times rbf_remittance_rate_pct,
    is strictly below rbf_cap times the sized facility cents. Borrowers with
    no entry or no facility in sizing_results are not counted (sizing has not
    run yet in this slice). The sized facility is the disclosed phase 2
    advance proxy; the predicate migrates to the real RBF schedule in phase 3
    with a decisions entry.
    """
    members: list[str] = []
    for item in book.borrowers:
        if item.structure != "rbf_capped":
            continue
        entry = _sizing_entry(sizing_results, item.borrower_id)
        if entry is None or entry.facility_cents is None:
            continue
        revenue_sum = sum(item.revenue_path[: book.rbf_horizon_periods])
        remittance = MONEY_CONTEXT.divide(
            MONEY_CONTEXT.multiply(Decimal(revenue_sum), book.rbf_remittance_rate_pct),
            Decimal(100),
        )
        threshold = MONEY_CONTEXT.multiply(
            Decimal(entry.facility_cents), item.rbf_cap
        )
        if remittance < threshold:
            members.append(item.borrower_id)
    return sorted(members)


def compute_strata_manifest(
    book: BorrowerBook, sizing_results: Mapping[str, SizingResult]
) -> dict[str, dict[str, object]]:
    """Counts and sorted member ids for all six strata (architecture P2.2).

    Strata are independent: one borrower may count in several. The manifest
    splits by data source for the later manifest/build wiring: pre_revenue
    and zero_warrant_bank are book-only; infeasible_at_zero_draw,
    runway_bound, and dscr_bound read sizing status; rbf_shortfall reads the
    sized facility from the same mapping. Sizing-based members require an
    entry in sizing_results (keyed by borrower_id), so those counts are zero
    until slice 5 feeds real sizing output through this function.
    """
    members: dict[str, list[str]] = {
        "pre_revenue": predicate_pre_revenue(book),
        "zero_warrant_bank": predicate_zero_warrant_bank(book),
        "infeasible_at_zero_draw": predicate_infeasible_at_zero_draw(
            book, sizing_results
        ),
        "runway_bound": predicate_runway_bound(book, sizing_results),
        "dscr_bound": predicate_dscr_bound(book, sizing_results),
        "rbf_shortfall": predicate_rbf_shortfall(book, sizing_results),
    }
    return {
        name: {"count": len(members[name]), "members": members[name]}
        for name in STRATA_NAMES
    }
