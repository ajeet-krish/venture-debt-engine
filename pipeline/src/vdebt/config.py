"""Scenario configuration loader for scenario.toml.

The loader reads the pinned scenario.toml with tomllib and returns a frozen
ScenarioConfig. Native TOML floats are banned: every non-integer numeric value
must be a quoted decimal string, and any float-typed value raises ValueError.
Every numeric value is returned as an exact Decimal (ints convert exactly).
Every band entry must carry citation and band keys, and the loader raises
ValueError when either is missing. Band.min and Band.max hold the primary
sampling bounds of a band; every bound key not consumed by min, max, or the
citation, band, unit, and scale meta keys is preserved in Band.extras, so no
TOML value is ever lost.

Phase 2 sections: [rates] pins the Valet series codes and label regexes (both
patterns are compiled at load, so a bad pattern raises ValueError), and
[generator] pins the weight tables (each category must sum to exactly 1), the
derived term parameters, and the [[generator.strata_slots]] recipes (borrower
ids unique and within SYN-0001 to SYN-0024).

The required [site] table pins the published site presentation: its
featured_borrower_id must be a slot id in the same SYN-0001 to SYN-0024 book,
validated with SLOT_ID_PATTERN.

Phase 4 tables: the required [covenants] table pins the three covenant limits
(net_leverage_max, interest_cover_min, min_cash_floor_months) as exact Decimal
scalar keys with no citation (the publication layer attaches the stated
assumption citation); the required [downside] table gains warrant_factor,
validated to the open-closed interval (0, 1]; and the required [dashboard]
table pins capacity_bin_edges_usd as a list of ints with at least 2 edges,
first edge 0, strictly ascending.
"""

import re
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# Keys that describe a band but are not numeric bounds.
BAND_META_KEYS: frozenset[str] = frozenset({"unit", "scale", "band", "citation"})

# Primary bound keys per band: Band.min and Band.max are read from these keys
# instead of the smallest-low and largest-high reduction. pricing bands sample
# the spread bounds and keep the total-rate bounds in extras; warrant_coverage
# samples the bank floor and nonbank ceiling as its overall bounds.
PRIMARY_BOUND_KEYS: dict[str, tuple[str, str]] = {
    "pricing_bank": ("spread_min_pct", "spread_max_pct"),
    "pricing_nonbank": ("spread_min_pct", "spread_max_pct"),
    "warrant_coverage": ("bank_min_pct", "nonbank_max_pct"),
}

# Bands whose extras keep every bound key, primary keys included, so each
# sub-bound stays readable next to the overall Band.min and Band.max.
EXTRAS_KEEP_ALL_BOUNDS: frozenset[str] = frozenset({"warrant_coverage"})

# Weight categories under [generator.weights]; each table must sum to exactly 1.
WEIGHT_CATEGORIES: tuple[str, ...] = ("lender_type", "structure", "pre_revenue")

# Strata slot borrower id shape and inclusive range (the SYN-0001 to SYN-0024
# book). ASCII digits only, so a fullwidth digit id fails the pattern.
SLOT_ID_PATTERN: re.Pattern[str] = re.compile(r"SYN-([0-9]{4})")
SLOT_ID_MIN: int = 1
SLOT_ID_MAX: int = 24


@dataclass(frozen=True)
class Band:
    """A rule-of-thumb band with primary bounds, unit, scale, citation, label.

    extras holds every numeric bound key that min and max did not consume (or
    all bound keys for bands in EXTRAS_KEEP_ALL_BOUNDS), keyed by raw TOML key.
    """

    min: Decimal
    max: Decimal
    unit: str
    scale: int
    citation: str
    band: str
    extras: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class SolverParams:
    """IRR bracket parameters plus the sizing bisection iteration cap."""

    bracket_low: Decimal
    bracket_high: Decimal
    tolerance_decimal: Decimal
    max_iterations: int
    bisection_max_iterations: int


@dataclass(frozen=True)
class RatesConfig:
    """Pinned Valet series selection and expected series label patterns.

    The patterns are stored as strings; load_scenario compiles both with
    re.compile so an unpinnable pattern raises ValueError at load time.
    """

    series: str
    fallback_series: str
    label_pattern: str
    fallback_label_pattern: str


@dataclass(frozen=True)
class SlotOverride:
    """One strata slot recipe: borrower id, stratum label, override values.

    overrides maps a generator value name to its pinned post-draw replacement,
    kept as the raw quoted string so no scale or label is lost at the loader
    boundary; the generator interprets each key in its own target unit.
    """

    borrower_id: str
    stratum: str
    overrides: dict[str, str]


@dataclass(frozen=True)
class GeneratorConfig:
    """Seeded borrower generator parameters from [generator].

    weights maps each WEIGHT_CATEGORIES name to its label-to-weight table in
    exact Decimal, every table summing to exactly 1. strata_slots preserves
    the pinned slot order from scenario.toml.
    """

    weights: dict[str, dict[str, Decimal]]
    term_amortizing_periods: int
    rbf_horizon_periods: int
    rbf_remittance_rate_pct: Decimal
    bridge_tenor_months: int
    strata_slots: tuple[SlotOverride, ...]


@dataclass(frozen=True)
class SiteConfig:
    """Published site presentation settings from the required [site] table.

    featured_borrower_id names the borrower featured on the site; it must be
    a slot id in the SYN-0001 to SYN-0024 book, so SLOT_ID_PATTERN and the
    SLOT_ID_MIN to SLOT_ID_MAX range apply unchanged.
    """

    featured_borrower_id: str


@dataclass(frozen=True)
class CovenantsConfig:
    """Covenant limit pins from the required [covenants] table (P4.1).

    net_leverage_max is the maximum net leverage ratio, interest_cover_min the
    minimum interest cover ratio, and min_cash_floor_months the minimum cash
    floor in months of net burn. All three are plain scalar keys: no citation
    travels here because the publication layer attaches the stated assumption
    citation at build time.
    """

    net_leverage_max: Decimal
    interest_cover_min: Decimal
    min_cash_floor_months: Decimal


@dataclass(frozen=True)
class DownsideConfig:
    """Configured downside shocks from the required [downside] table (P4.2).

    burn_uplift_pct and revenue_downlift_pct keep their phase 1 meaning, and
    warrant_factor is the pinned downside warrant revaluation factor in
    (0, 1], so the revaluation can never zero out or amplify the factor.
    """

    burn_uplift_pct: Decimal
    revenue_downlift_pct: Decimal
    warrant_factor: Decimal


@dataclass(frozen=True)
class DashboardConfig:
    """Dashboard publication settings from the required [dashboard] table (P4.6).

    capacity_bin_edges holds the capacity_bin_edges_usd integer cent edges in
    pinned order: at least 2 edges, first edge 0, strictly ascending, so the
    capacity histogram bins are contiguous and never inverted.
    """

    capacity_bin_edges: tuple[int, ...]


@dataclass(frozen=True)
class ScenarioConfig:
    """Fully parsed, pinned scenario configuration."""

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
    rates: RatesConfig
    generator: GeneratorConfig
    site: SiteConfig
    covenants: CovenantsConfig
    downside: DownsideConfig
    dashboard: DashboardConfig


def _reject_floats(node: object, where: str) -> None:
    """Raise ValueError when a native TOML float appears anywhere under where."""
    is_float = isinstance(node, float)
    if is_float:
        raise ValueError(
            f"native TOML float banned at {where}: {node!r}, "
            "use a quoted decimal string instead"
        )
    if isinstance(node, dict):
        for key, value in node.items():
            _reject_floats(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_floats(value, f"{where}[{index}]")


def _to_decimal(value: object, where: str) -> Decimal:
    """Convert an int or a quoted decimal string to an exact finite Decimal."""
    if isinstance(value, bool):
        raise TypeError(f"{where} must be a number, got bool")
    if isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"{where} is not a valid decimal string: {value!r}") from exc
    else:
        raise TypeError(
            f"{where} must be an int or a quoted decimal string, "
            f"got {type(value).__name__}"
        )
    if not result.is_finite():
        raise ValueError(f"{where} must be finite, got {value!r}")
    return result


def _require_int(value: object, where: str) -> int:
    """Return value as an int, raising TypeError for any non-int."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{where} must be an int, got {type(value).__name__}")
    return value


def _require_str(value: object, where: str) -> str:
    """Return value as a str, raising TypeError for any non-string."""
    if isinstance(value, str):
        return value
    raise TypeError(f"{where} must be a string, got {type(value).__name__}")


def _require_table(value: object, where: str) -> dict[str, Any]:
    """Return value as a TOML table mapping, raising TypeError otherwise."""
    if isinstance(value, dict):
        return value
    raise TypeError(f"{where} must be a table, got {type(value).__name__}")


def _require_key(table: dict[str, Any], key: str, where: str) -> object:
    """Return table[key], raising ValueError when the key is absent."""
    if key not in table:
        raise ValueError(f"missing required key: {where}.{key}")
    return table[key]


def _parse_band(name: str, raw: object) -> Band:
    """Parse one [bands.<name>] table into a frozen Band.

    Bound keys vary by band: min_pct_of_last_round, min_months, bank_min_pct,
    spread_min_pct, and the single-value key value, among others. A key equal
    to value feeds both bounds, a key starting with min or containing _min
    feeds the low bound, and a key starting with max or containing _max feeds
    the high bound. citation and band are required and raise ValueError when
    missing.

    Bands listed in PRIMARY_BOUND_KEYS read Band.min and Band.max from their
    primary keys: pricing_bank and pricing_nonbank sample spread_min_pct and
    spread_max_pct, and warrant_coverage samples bank_min_pct and
    nonbank_max_pct as its overall bounds. Every other band sets Band.min to
    the smallest low bound and Band.max to the largest high bound, so a
    multi-part band spans its full range and a single value key such as
    dscr_floor_reference feeds both bounds equally. Bound keys that min and
    max do not consume are stored in Band.extras so no TOML value is lost
    (pricing total-rate bounds), and bands in EXTRAS_KEEP_ALL_BOUNDS keep all
    of their bound keys in extras alongside the overall bounds.
    """
    where = f"bands.{name}"
    table = _require_table(raw, where)
    unit = _require_str(_require_key(table, "unit", where), f"{where}.unit")
    scale = _require_int(_require_key(table, "scale", where), f"{where}.scale")
    band = _require_str(_require_key(table, "band", where), f"{where}.band")
    citation = _require_str(_require_key(table, "citation", where), f"{where}.citation")
    low_bounds: list[Decimal] = []
    high_bounds: list[Decimal] = []
    bound_values: dict[str, Decimal] = {}
    for key, value in table.items():
        if key in BAND_META_KEYS:
            continue
        number = _to_decimal(value, f"{where}.{key}")
        bound_values[key] = number
        if key == "value":
            low_bounds.append(number)
            high_bounds.append(number)
        elif key.startswith("min") or "_min" in key:
            low_bounds.append(number)
        elif key.startswith("max") or "_max" in key:
            high_bounds.append(number)
        else:
            raise ValueError(f"{where}.{key}: unrecognized band bound key")
    if not low_bounds or not high_bounds:
        raise ValueError(f"{where} needs at least one min bound and one max bound")
    extras: dict[str, Decimal]
    primary = PRIMARY_BOUND_KEYS.get(name)
    if primary is None:
        band_min = min(low_bounds)
        band_max = max(high_bounds)
        extras = {}
    else:
        low_key, high_key = primary
        for key in (low_key, high_key):
            if key not in bound_values:
                raise ValueError(f"{where} missing primary bound key: {key}")
        band_min = bound_values[low_key]
        band_max = bound_values[high_key]
        if name in EXTRAS_KEEP_ALL_BOUNDS:
            extras = dict(bound_values)
        else:
            extras = {
                key: value
                for key, value in bound_values.items()
                if key not in (low_key, high_key)
            }
    return Band(
        min=band_min,
        max=band_max,
        unit=unit,
        scale=scale,
        citation=citation,
        band=band,
        extras=extras,
    )


def _parse_rates(raw: object) -> RatesConfig:
    """Parse [rates] and compile both label patterns at load time.

    Both patterns are compiled with re.compile so an unpinnable pattern raises
    ValueError here instead of failing at the first series label assertion.
    """
    where = "rates"
    table = _require_table(raw, where)
    series = _require_str(_require_key(table, "series", where), "rates.series")
    fallback_series = _require_str(
        _require_key(table, "fallback_series", where), "rates.fallback_series"
    )
    label_pattern = _require_str(
        _require_key(table, "label_pattern", where), "rates.label_pattern"
    )
    fallback_label_pattern = _require_str(
        _require_key(table, "fallback_label_pattern", where),
        "rates.fallback_label_pattern",
    )
    for key, pattern in (
        ("label_pattern", label_pattern),
        ("fallback_label_pattern", fallback_label_pattern),
    ):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(
                f"rates.{key} is not a valid regex: {pattern!r} ({exc})"
            ) from exc
    return RatesConfig(
        series=series,
        fallback_series=fallback_series,
        label_pattern=label_pattern,
        fallback_label_pattern=fallback_label_pattern,
    )


def _parse_weights(raw: object) -> dict[str, dict[str, Decimal]]:
    """Parse [generator.weights] with each category summing to exactly 1.

    Unknown category tables raise ValueError so a mistyped category can never
    silently disappear from the weight validation.
    """
    where = "generator.weights"
    table = _require_table(raw, where)
    for category in table:
        if category not in WEIGHT_CATEGORIES:
            raise ValueError(f"{where}: unknown weight category: {category!r}")
    weights: dict[str, dict[str, Decimal]] = {}
    for category in WEIGHT_CATEGORIES:
        category_where = f"{where}.{category}"
        category_table = _require_table(
            _require_key(table, category, where), category_where
        )
        if not category_table:
            raise ValueError(f"{category_where} must define at least one weight")
        parsed: dict[str, Decimal] = {}
        for label, value in category_table.items():
            weight = _to_decimal(value, f"{category_where}.{label}")
            if weight < 0:
                raise ValueError(
                    f"{category_where}.{label} must be non-negative, got {weight}"
                )
            parsed[label] = weight
        total = sum(parsed.values(), Decimal(0))
        if total != Decimal(1):
            raise ValueError(
                f"{category_where} weights sum to {total}, expected exactly 1"
            )
        weights[category] = parsed
    return weights


def _parse_strata_slots(raw: object) -> tuple[SlotOverride, ...]:
    """Parse [[generator.strata_slots]] recipes in pinned file order.

    Borrower ids must match SYN plus four ASCII digits within SYN-0001 to
    SYN-0024 and must be unique; stratum must be a non-empty string; every
    override value must be a quoted string (the float ban already rejected
    native floats anywhere in the file).
    """
    where = "generator.strata_slots"
    if not isinstance(raw, list):
        raise TypeError(
            f"{where} must be an array of tables, got {type(raw).__name__}"
        )
    slots: list[SlotOverride] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw):
        entry_where = f"{where}[{index}]"
        table = _require_table(entry, entry_where)
        borrower_id = _require_str(
            _require_key(table, "borrower_id", entry_where),
            f"{entry_where}.borrower_id",
        )
        match = SLOT_ID_PATTERN.fullmatch(borrower_id)
        number = int(match.group(1)) if match else None
        if number is None or not SLOT_ID_MIN <= number <= SLOT_ID_MAX:
            raise ValueError(
                f"{entry_where}.borrower_id must be within "
                f"SYN-0001 to SYN-0024, got {borrower_id!r}"
            )
        if borrower_id in seen_ids:
            raise ValueError(f"{entry_where}.borrower_id duplicates {borrower_id}")
        seen_ids.add(borrower_id)
        stratum = _require_str(
            _require_key(table, "stratum", entry_where), f"{entry_where}.stratum"
        )
        if not stratum:
            raise ValueError(f"{entry_where}.stratum must be non-empty")
        overrides_table = _require_table(
            _require_key(table, "overrides", entry_where), f"{entry_where}.overrides"
        )
        overrides = {
            key: _require_str(value, f"{entry_where}.overrides.{key}")
            for key, value in overrides_table.items()
        }
        slots.append(
            SlotOverride(borrower_id=borrower_id, stratum=stratum, overrides=overrides)
        )
    return tuple(slots)


def _parse_generator(raw: object) -> GeneratorConfig:
    """Parse [generator] plus its weight tables and strata slot recipes."""
    where = "generator"
    table = _require_table(raw, where)
    weights = _parse_weights(_require_key(table, "weights", where))
    strata_slots = _parse_strata_slots(table.get("strata_slots", []))
    return GeneratorConfig(
        weights=weights,
        term_amortizing_periods=_require_int(
            _require_key(table, "term_amortizing_periods", where),
            "generator.term_amortizing_periods",
        ),
        rbf_horizon_periods=_require_int(
            _require_key(table, "rbf_horizon_periods", where),
            "generator.rbf_horizon_periods",
        ),
        rbf_remittance_rate_pct=_to_decimal(
            _require_key(table, "rbf_remittance_rate_pct", where),
            "generator.rbf_remittance_rate_pct",
        ),
        bridge_tenor_months=_require_int(
            _require_key(table, "bridge_tenor_months", where),
            "generator.bridge_tenor_months",
        ),
        strata_slots=strata_slots,
    )


def _parse_site(raw: object) -> SiteConfig:
    """Parse the required [site] table into a frozen SiteConfig.

    featured_borrower_id must be a string matching SYN plus four ASCII digits
    (SLOT_ID_PATTERN, fullmatch so no surrounding text slips through) with a
    numeric value inside SLOT_ID_MIN to SLOT_ID_MAX. A missing key raises
    ValueError from _require_key, a wrong type raises TypeError from
    _require_str, and an out-of-book id raises ValueError here.
    """
    where = "site"
    table = _require_table(raw, where)
    featured = _require_str(
        _require_key(table, "featured_borrower_id", where),
        "site.featured_borrower_id",
    )
    match = SLOT_ID_PATTERN.fullmatch(featured)
    number = int(match.group(1)) if match else None
    if number is None or not SLOT_ID_MIN <= number <= SLOT_ID_MAX:
        raise ValueError(
            "site.featured_borrower_id must be within "
            f"SYN-0001 to SYN-0024, got {featured!r}"
        )
    return SiteConfig(featured_borrower_id=featured)


def _parse_covenants(raw: object) -> CovenantsConfig:
    """Parse the required [covenants] table into a frozen CovenantsConfig.

    All three limits are required and converted with _to_decimal, so a quoted
    decimal string or an int passes, a native float is already rejected by the
    file-wide float ban, and a missing key raises ValueError from _require_key.
    No range check runs here: the covenant module applies the limits and the
    publication layer attaches the stated assumption citation.
    """
    where = "covenants"
    table = _require_table(raw, where)
    return CovenantsConfig(
        net_leverage_max=_to_decimal(
            _require_key(table, "net_leverage_max", where),
            "covenants.net_leverage_max",
        ),
        interest_cover_min=_to_decimal(
            _require_key(table, "interest_cover_min", where),
            "covenants.interest_cover_min",
        ),
        min_cash_floor_months=_to_decimal(
            _require_key(table, "min_cash_floor_months", where),
            "covenants.min_cash_floor_months",
        ),
    )


def _parse_downside(raw: object) -> DownsideConfig:
    """Parse the required [downside] table into a frozen DownsideConfig.

    warrant_factor is required and must lie in (0, 1]: a value of 0 would zero
    the downside warrant and a value above 1 would amplify it, so either
    raises ValueError with the offending value in the message. burn_uplift_pct
    and revenue_downlift_pct are required decimals as in phase 1.
    """
    where = "downside"
    table = _require_table(raw, where)
    burn_uplift_pct = _to_decimal(
        _require_key(table, "burn_uplift_pct", where), "downside.burn_uplift_pct"
    )
    revenue_downlift_pct = _to_decimal(
        _require_key(table, "revenue_downlift_pct", where),
        "downside.revenue_downlift_pct",
    )
    warrant_factor = _to_decimal(
        _require_key(table, "warrant_factor", where), "downside.warrant_factor"
    )
    if not (Decimal(0) < warrant_factor <= Decimal(1)):
        raise ValueError(
            "downside.warrant_factor must be greater than 0 and at most 1, "
            f"got {warrant_factor}"
        )
    return DownsideConfig(
        burn_uplift_pct=burn_uplift_pct,
        revenue_downlift_pct=revenue_downlift_pct,
        warrant_factor=warrant_factor,
    )


def _parse_dashboard(raw: object) -> DashboardConfig:
    """Parse the required [dashboard] table into a frozen DashboardConfig.

    capacity_bin_edges_usd must be a list of ints with at least 2 edges, a
    first edge of 0, and strictly ascending values, so the capacity histogram
    bins are contiguous, non-empty, and never inverted. A missing key raises
    ValueError from _require_key, a non-list or non-int edge raises TypeError
    with the offending path, and a range violation raises ValueError naming
    the bad edge.
    """
    where = "dashboard"
    table = _require_table(raw, where)
    edges_raw = _require_key(table, "capacity_bin_edges_usd", where)
    edges_where = "dashboard.capacity_bin_edges_usd"
    if not isinstance(edges_raw, list):
        raise TypeError(
            f"{edges_where} must be a list of ints, got {type(edges_raw).__name__}"
        )
    edges: list[int] = []
    for index, value in enumerate(edges_raw):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(
                f"{edges_where}[{index}] must be an int, "
                f"got {type(value).__name__}"
            )
        edges.append(value)
    if len(edges) < 2:
        raise ValueError(f"{edges_where} needs at least 2 edges, got {len(edges)}")
    if edges[0] != 0:
        raise ValueError(f"{edges_where} first edge must be 0, got {edges[0]}")
    for index in range(1, len(edges)):
        if edges[index] <= edges[index - 1]:
            raise ValueError(
                f"{edges_where} must be strictly ascending, "
                f"got {edges[index]} at index {index} "
                f"after {edges[index - 1]} at index {index - 1}"
            )
    return DashboardConfig(capacity_bin_edges=tuple(edges))


def load_scenario(path: Path) -> ScenarioConfig:
    """Load scenario.toml at path into a frozen ScenarioConfig.

    Raises ValueError on any native TOML float, on any band entry missing its
    citation or band key, on a weight table that does not sum to exactly 1, on
    a duplicate or out-of-range strata slot borrower id, on a label pattern
    that does not compile, on a missing [site], [covenants], [downside], or
    [dashboard] table, on an out-of-range site.featured_borrower_id or
    downside.warrant_factor, on a malformed dashboard capacity bin edge list,
    and on any malformed or absent required key.
    """
    if not isinstance(path, Path):
        raise TypeError(f"path must be a Path, got {type(path).__name__}")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid TOML in {path}: {exc}") from exc
    _reject_floats(data, "scenario")

    meta = _require_table(_require_key(data, "meta", "scenario"), "meta")
    sizing = _require_table(_require_key(data, "sizing", "scenario"), "sizing")
    solver_table = _require_table(_require_key(data, "solver", "scenario"), "solver")
    irr = _require_table(_require_key(solver_table, "irr", "solver"), "solver.irr")
    scenario_table = _require_table(
        _require_key(data, "scenario", "scenario"), "scenario"
    )
    downside_table = _require_table(
        _require_key(data, "downside", "scenario"), "downside"
    )
    warrant = _require_table(_require_key(data, "warrant", "scenario"), "warrant")
    rates_table = _require_table(_require_key(data, "rates", "scenario"), "rates")
    generator_table = _require_table(
        _require_key(data, "generator", "scenario"), "generator"
    )
    bands_table = _require_table(_require_key(data, "bands", "scenario"), "bands")
    covenants_table = _require_table(
        _require_key(data, "covenants", "scenario"), "covenants"
    )
    dashboard_table = _require_table(
        _require_key(data, "dashboard", "scenario"), "dashboard"
    )
    site_table = _require_table(_require_key(data, "site", "scenario"), "site")

    bands = {name: _parse_band(name, raw) for name, raw in bands_table.items()}
    rates = _parse_rates(rates_table)
    generator = _parse_generator(generator_table)
    site = _parse_site(site_table)
    covenants = _parse_covenants(covenants_table)
    downside = _parse_downside(downside_table)
    dashboard = _parse_dashboard(dashboard_table)

    solver = SolverParams(
        bracket_low=_to_decimal(
            _require_key(irr, "bracket_low", "solver.irr"), "solver.irr.bracket_low"
        ),
        bracket_high=_to_decimal(
            _require_key(irr, "bracket_high", "solver.irr"), "solver.irr.bracket_high"
        ),
        tolerance_decimal=_to_decimal(
            _require_key(irr, "tolerance_decimal", "solver.irr"),
            "solver.irr.tolerance_decimal",
        ),
        max_iterations=_require_int(
            _require_key(irr, "max_iterations", "solver.irr"), "solver.irr.max_iterations"
        ),
        bisection_max_iterations=_require_int(
            _require_key(sizing, "bisection_max_iterations", "sizing"),
            "sizing.bisection_max_iterations",
        ),
    )

    return ScenarioConfig(
        seed=_require_int(_require_key(meta, "seed", "meta"), "meta.seed"),
        generator_version=_require_str(
            _require_key(meta, "generator_version", "meta"), "meta.generator_version"
        ),
        currency=_require_str(_require_key(meta, "currency", "meta"), "meta.currency"),
        rounding_mode=_require_str(
            _require_key(meta, "rounding_mode", "meta"), "meta.rounding_mode"
        ),
        bands=bands,
        runway_floor_months=_to_decimal(
            _require_key(sizing, "runway_floor_months", "sizing"),
            "sizing.runway_floor_months",
        ),
        dscr_floor=_to_decimal(
            _require_key(sizing, "dscr_floor", "sizing"), "sizing.dscr_floor"
        ),
        rate_shock_bp=_to_decimal(
            _require_key(scenario_table, "rate_shock_bp", "scenario"),
            "scenario.rate_shock_bp",
        ),
        warrant_valuation_factor=_to_decimal(
            _require_key(warrant, "valuation_factor", "warrant"),
            "warrant.valuation_factor",
        ),
        downside_burn_uplift_pct=downside.burn_uplift_pct,
        downside_revenue_downlift_pct=downside.revenue_downlift_pct,
        solver=solver,
        rates=rates,
        generator=generator,
        site=site,
        covenants=covenants,
        downside=downside,
        dashboard=dashboard,
    )
