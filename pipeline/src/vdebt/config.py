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
"""

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


def load_scenario(path: Path) -> ScenarioConfig:
    """Load scenario.toml at path into a frozen ScenarioConfig.

    Raises ValueError on any native TOML float, on any band entry missing its
    citation or band key, and on any malformed or absent required key.
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
    downside = _require_table(_require_key(data, "downside", "scenario"), "downside")
    warrant = _require_table(_require_key(data, "warrant", "scenario"), "warrant")
    bands_table = _require_table(_require_key(data, "bands", "scenario"), "bands")

    bands = {name: _parse_band(name, raw) for name, raw in bands_table.items()}

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
        downside_burn_uplift_pct=_to_decimal(
            _require_key(downside, "burn_uplift_pct", "downside"),
            "downside.burn_uplift_pct",
        ),
        downside_revenue_downlift_pct=_to_decimal(
            _require_key(downside, "revenue_downlift_pct", "downside"),
            "downside.revenue_downlift_pct",
        ),
        solver=solver,
    )
