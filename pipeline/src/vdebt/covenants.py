"""Covenant engine: net leverage, interest cover, minimum cash, and DSCR floor.

Architecture P4.1 with the spec section 5 covenant table. Pure computation
like returns.py: dataclasses in, dataclasses out. Envelope assembly stays in
build.py, the single envelope owner, and this module reads no configuration:
the caller builds each CovenantSpec with its configured limit and citation.

The four covenants and their per-period actuals:
- net_leverage (max): actual_t = net_debt_t / ebitda_t, where EBITDA proxy is
  the period CFADS (pinned definition) and net_debt_t is closing balance
  minus cash in exact integer cents.
- interest_cover (min): actual_t = ebitda_t / cash_interest_t.
- min_cash (min): actual_t = cash_t from the cash path recurrence, with the
  burn floor in integer cents as the limit; always applicable.
- dscr_floor (min): actual_t = ebitda_t / debt_service_t over periods with
  positive debt service only (RBF remittance counts as debt service).

Headroom follows the pinned type rules: limit minus actual for max types and
actual minus limit for min types. It is computed in Decimal at MONEY_CONTEXT
on the unquantized ratio, compared unquantized against the limit with
equality feasible, and quantized exactly once at the publication boundary to
ratio scale 4 (min_cash keeps exact integer cents). A period whose headroom
is negative is a breach; first_breach_month is the 1-based index of the first
breaching period and None when no period breaches. min_headroom is the
minimum across tested periods, taken unquantized and quantized once.

not_applicable paths carry the pinned stated reasons and never fabricated
zeros: zero EBITDA for net_leverage and interest_cover, cash_interest None
for RBF interest cover, and no positive debt service for dscr_floor. A DSCR
period with zero debt service publishes no row at all. min_cash is always
applicable.

Status strings emitted here: "feasible", "breach", and "not_applicable".
"breach" joins the snapshot Status vocabulary in a later phase 4 slice; this
module only emits the strings.

Depends on the standard library plus vdebt.money only. No network access
anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from vdebt.money import MONEY_CONTEXT, quantize

# Published covenant statuses (section 6.4 vocabulary).
STATUS_FEASIBLE: str = "feasible"
STATUS_BREACH: str = "breach"
STATUS_NOT_APPLICABLE: str = "not_applicable"

# Pinned covenant names (P4.1 vocabulary).
NAME_NET_LEVERAGE: str = "net_leverage"
NAME_INTEREST_COVER: str = "interest_cover"
NAME_MIN_CASH: str = "min_cash"
NAME_DSCR_FLOOR: str = "dscr_floor"

# Package evaluation order pinned in P4.1: net_leverage, interest_cover,
# min_cash, dscr_floor.
PACKAGE_ORDER: tuple[str, ...] = (
    NAME_NET_LEVERAGE,
    NAME_INTEREST_COVER,
    NAME_MIN_CASH,
    NAME_DSCR_FLOOR,
)

# Headroom kinds (spec section 5).
KIND_MAX: str = "max"
KIND_MIN: str = "min"

# Metric units for the actual and headroom leaves (section 6.4).
UNIT_RATIO: str = "ratio"
UNIT_USD_MINOR_UNITS: str = "usd_minor_units"

# Published ratio scale for ratio actuals and headrooms (section 6.4).
RATIO_SCALE: int = 4

# Pinned not_applicable reasons (P4.1: stated reasons, never fabricated zeros).
REASON_PRE_REVENUE_NET_LEVERAGE: str = (
    "pre-revenue borrower, EBITDA proxy (CFADS) is zero so net leverage is undefined"
)
REASON_PRE_REVENUE_INTEREST_COVER: str = (
    "pre-revenue borrower, EBITDA proxy (CFADS) is zero so interest cover is undefined"
)
REASON_RBF_NO_INTEREST: str = "RBF carries no interest term so cash interest is undefined"
REASON_RBF_NO_REMITTANCE: str = "no period carries positive remittance so RBF DSCR is undefined"

# Pinned kind and unit per covenant name; evaluate_covenant enforces both so
# a caller cannot flip a max covenant to min (or a ratio limit to cents).
_KIND_BY_NAME: dict[str, str] = {
    NAME_NET_LEVERAGE: KIND_MAX,
    NAME_INTEREST_COVER: KIND_MIN,
    NAME_MIN_CASH: KIND_MIN,
    NAME_DSCR_FLOOR: KIND_MIN,
}
_UNIT_BY_NAME: dict[str, str] = {
    NAME_NET_LEVERAGE: UNIT_RATIO,
    NAME_INTEREST_COVER: UNIT_RATIO,
    NAME_MIN_CASH: UNIT_USD_MINOR_UNITS,
    NAME_DSCR_FLOOR: UNIT_RATIO,
}


@dataclass(frozen=True)
class CovenantSpec:
    """One covenant's published limit: name, type, limit, unit, citation.

    name is one of the four pinned names, kind is "max" or "min", limit is a
    Decimal ratio for ratio covenants and integer cents for min_cash, unit is
    "ratio" or "usd_minor_units", citation is the band citation or the
    stated-assumption citation, and band is the band label for dscr_floor or
    None otherwise.
    """

    name: str
    kind: str
    limit: Decimal | int
    unit: str
    citation: str
    band: str | None


@dataclass(frozen=True)
class CovenantPeriod:
    """One tested period row: index, payment date, actual, headroom, status.

    period is the 1-based schedule period (months since close) and
    payment_date is the schedule payment date for that period. actual and
    headroom are Decimal at ratio scale 4 for ratio covenants and exact
    integer cents for min_cash; both are None only in excluded rows, and an
    excluded period publishes no row at all (zero-service DSCR periods).
    status is "feasible" or "breach": a breach is a negative headroom.
    """

    period: int
    payment_date: date
    actual: Decimal | int | None
    headroom: Decimal | int | None
    status: str


@dataclass(frozen=True)
class CovenantResult:
    """One covenant's outcome: status, reason, rows, first breach, min headroom.

    status is "feasible", "breach", or "not_applicable". reason is the
    pinned stated reason exactly when status is not_applicable and None
    otherwise. periods holds the tested rows in schedule order and is empty
    when not applicable. first_breach_month is the 1-based period index of
    the first breaching row and None when no period breaches (or when not
    applicable). min_headroom is the minimum headroom across tested rows,
    quantized once, and None when not applicable or when no row exists.
    """

    spec: CovenantSpec
    status: str
    reason: str | None
    periods: tuple[CovenantPeriod, ...]
    first_breach_month: int | None
    min_headroom: Decimal | int | None


def _require_cents(value: object, name: str) -> int:
    """Return value as int, rejecting floats (the money.py float ban)."""
    if isinstance(value, float):
        raise TypeError(f"{name} must not be a float; money uses int minor units")
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def cash_path(
    cash_cents: int,
    net_burn_cents: int,
    debt_service_cents: tuple[int, ...],
) -> tuple[int, ...]:
    """Return the pinned cash path: cash_t = cash_(t-1) minus net burn minus service.

    cash_0 is cash_cents (the drawn cash) and facility proceeds never enter
    the recurrence. There is no zero clamp: a negative balance publishes as a
    signed integer, which is exactly what a breach shows. Every step is exact
    integer cents with no rounding point. The returned path has one entry per
    element of debt_service_cents.
    """
    _require_cents(cash_cents, "cash_cents")
    _require_cents(net_burn_cents, "net_burn_cents")
    if not isinstance(debt_service_cents, tuple):
        raise TypeError(
            f"debt_service_cents must be a tuple, got {type(debt_service_cents).__name__}"
        )
    balance = cash_cents
    path: list[int] = []
    for index, service in enumerate(debt_service_cents):
        _require_cents(service, f"debt_service_cents[{index}]")
        balance = balance - net_burn_cents - service
        path.append(balance)
    return tuple(path)


def burn_floor_cents(net_burn_cents: int, floor_months: Decimal) -> int:
    """Return floor_months times net_burn_cents quantized once to integer cents.

    The single ROUND_HALF_UP quantize runs in MONEY_CONTEXT, passed
    explicitly, so the burn floor carries no intermediate rounding (P4.1:
    quantize once at the metric boundary).
    """
    _require_cents(net_burn_cents, "net_burn_cents")
    if isinstance(floor_months, float):
        raise TypeError("floor_months must not be a float; use Decimal")
    if not isinstance(floor_months, Decimal):
        raise TypeError(f"floor_months must be a Decimal, got {type(floor_months).__name__}")
    raw = MONEY_CONTEXT.multiply(Decimal(net_burn_cents), floor_months)
    return int(quantize(raw, 0))


def _validate_spec(spec: CovenantSpec) -> None:
    """Enforce the pinned spec shape, raising TypeError or ValueError."""
    if not isinstance(spec, CovenantSpec):
        raise TypeError(f"spec must be a CovenantSpec, got {type(spec).__name__}")
    expected_kind = _KIND_BY_NAME.get(spec.name)
    if expected_kind is None:
        allowed = ", ".join(PACKAGE_ORDER)
        raise ValueError(f"unknown covenant name {spec.name!r}; expected one of: {allowed}")
    if spec.kind not in (KIND_MAX, KIND_MIN):
        raise ValueError(f"covenant kind must be {KIND_MAX!r} or {KIND_MIN!r}, got {spec.kind!r}")
    if spec.kind != expected_kind:
        raise ValueError(
            f"{spec.name} is pinned as a {expected_kind} covenant, got {spec.kind!r}"
        )
    expected_unit = _UNIT_BY_NAME[spec.name]
    if spec.unit != expected_unit:
        raise ValueError(f"{spec.name} is pinned as {expected_unit!r}, got {spec.unit!r}")
    if isinstance(spec.limit, float):
        raise TypeError("limit must not be a float; use a Decimal ratio or int cents")
    if expected_unit == UNIT_RATIO and not isinstance(spec.limit, Decimal):
        raise TypeError(
            f"{spec.name} limit must be a Decimal ratio, got {type(spec.limit).__name__}"
        )
    if expected_unit == UNIT_USD_MINOR_UNITS and not isinstance(spec.limit, int):
        raise TypeError(
            f"{spec.name} limit must be int cents, got {type(spec.limit).__name__}"
        )
    if not isinstance(spec.citation, str) or not spec.citation.strip():
        raise ValueError("citation must be a non-empty string")
    if spec.band is not None and not isinstance(spec.band, str):
        raise TypeError(f"band must be a str or None, got {type(spec.band).__name__}")


def _validate_paths(
    *,
    ebitda: tuple[int, ...],
    net_debt: tuple[int, ...],
    cash: tuple[int, ...],
    cash_interest: tuple[int, ...] | None,
    debt_service: tuple[int, ...] | None,
    dates: tuple[date, ...],
) -> None:
    """Raise TypeError or ValueError when any per-period path is malformed.

    Every provided path must be a tuple of integer cents whose length equals
    the dates length, and dates must be a tuple of date. Optional paths may
    be None (their covenant then takes its not_applicable path when pinned).
    """
    if not isinstance(dates, tuple):
        raise TypeError(f"dates must be a tuple, got {type(dates).__name__}")
    for index, value in enumerate(dates):
        if not isinstance(value, date):
            raise TypeError(f"dates[{index}] must be a date, got {type(value).__name__}")
    expected = len(dates)
    for label, values in (
        ("ebitda", ebitda),
        ("net_debt", net_debt),
        ("cash", cash),
        ("cash_interest", cash_interest),
        ("debt_service", debt_service),
    ):
        if values is None:
            continue
        if not isinstance(values, tuple):
            raise TypeError(f"{label} must be a tuple, got {type(values).__name__}")
        if len(values) != expected:
            raise ValueError(f"{label} length {len(values)} must equal dates length {expected}")
        for position, element in enumerate(values):
            _require_cents(element, f"{label}[{position}]")


def _not_applicable(spec: CovenantSpec, reason: str) -> CovenantResult:
    """Return the pinned not_applicable shape: stated reason, no rows, no figures."""
    return CovenantResult(
        spec=spec,
        status=STATUS_NOT_APPLICABLE,
        reason=reason,
        periods=(),
        first_breach_month=None,
        min_headroom=None,
    )


def _evaluate_rows(
    spec: CovenantSpec,
    *,
    actuals: list[tuple[int, Decimal | int]],
    dates: tuple[date, ...],
) -> CovenantResult:
    """Build the rows, first breach, and min headroom from computed actuals.

    actuals carries (zero-based index, unquantized actual) pairs for the
    tested periods only. The headroom follows the pinned type rule on the
    unquantized actual, the breach test compares it unquantized against zero
    with equality feasible, and the stored actual and headroom quantize
    exactly once at the publication boundary: ratio scale 4 for ratio
    covenants, exact integer cents for min_cash.
    """
    ratio = spec.unit == UNIT_RATIO
    rows: list[CovenantPeriod] = []
    first_breach: int | None = None
    minimum: Decimal | int | None = None
    for index, actual in actuals:
        if spec.kind == KIND_MAX:
            headroom = (
                MONEY_CONTEXT.subtract(spec.limit, actual)
                if ratio
                else spec.limit - actual
            )
        else:
            headroom = (
                MONEY_CONTEXT.subtract(actual, spec.limit)
                if ratio
                else actual - spec.limit
            )
        status = STATUS_BREACH if headroom < 0 else STATUS_FEASIBLE
        if minimum is None or headroom < minimum:
            minimum = headroom
        if status == STATUS_BREACH and first_breach is None:
            first_breach = index + 1
        rows.append(
            CovenantPeriod(
                period=index + 1,
                payment_date=dates[index],
                actual=quantize(actual, RATIO_SCALE) if ratio else actual,
                headroom=quantize(headroom, RATIO_SCALE) if ratio else headroom,
                status=status,
            )
        )
    if minimum is not None and ratio:
        minimum = quantize(minimum, RATIO_SCALE)
    return CovenantResult(
        spec=spec,
        status=STATUS_BREACH if first_breach is not None else STATUS_FEASIBLE,
        reason=None,
        periods=tuple(rows),
        first_breach_month=first_breach,
        min_headroom=minimum,
    )


def evaluate_covenant(
    spec: CovenantSpec,
    *,
    ebitda: tuple[int, ...],
    net_debt: tuple[int, ...],
    cash: tuple[int, ...],
    cash_interest: tuple[int, ...] | None,
    debt_service: tuple[int, ...] | None,
    dates: tuple[date, ...],
) -> CovenantResult:
    """Evaluate one covenant over the period paths and return its result.

    Dispatches on spec.name to the pinned actual definition and applicability
    rules (P4.1): net_leverage and interest_cover are not_applicable when any
    tested period's EBITDA proxy is not strictly positive; interest_cover is
    also not_applicable when cash_interest is None (RBF has no interest
    term) or carries no positive interest; dscr_floor is not_applicable when
    debt_service is None or no period carries positive debt service, and its
    zero-service periods publish no row. min_cash is always applicable.

    Headroom is limit minus actual for max types and actual minus limit for
    min types, computed in Decimal at MONEY_CONTEXT on the unquantized ratio
    and compared unquantized against the limit (equality feasible). A
    negative headroom is a breach; first_breach_month is the 1-based period
    index of the first breaching row, or None when no period breaches.
    Guard violations raise TypeError or ValueError.
    """
    _validate_spec(spec)
    _validate_paths(
        ebitda=ebitda,
        net_debt=net_debt,
        cash=cash,
        cash_interest=cash_interest,
        debt_service=debt_service,
        dates=dates,
    )
    if spec.name == NAME_NET_LEVERAGE:
        if any(value <= 0 for value in ebitda):
            return _not_applicable(spec, REASON_PRE_REVENUE_NET_LEVERAGE)
        actuals: list[tuple[int, Decimal | int]] = [
            (
                index,
                MONEY_CONTEXT.divide(Decimal(net_debt[index]), Decimal(ebitda[index])),
            )
            for index in range(len(dates))
        ]
    elif spec.name == NAME_INTEREST_COVER:
        if any(value <= 0 for value in ebitda):
            return _not_applicable(spec, REASON_PRE_REVENUE_INTEREST_COVER)
        if cash_interest is None:
            return _not_applicable(spec, REASON_RBF_NO_INTEREST)
        if any(value <= 0 for value in cash_interest):
            # A structure whose interest path carries no positive interest has
            # no interest term for cover purposes, same pinned reason.
            return _not_applicable(spec, REASON_RBF_NO_INTEREST)
        actuals = [
            (
                index,
                MONEY_CONTEXT.divide(Decimal(ebitda[index]), Decimal(cash_interest[index])),
            )
            for index in range(len(dates))
        ]
    elif spec.name == NAME_MIN_CASH:
        actuals = [(index, cash[index]) for index in range(len(dates))]
    else:
        if debt_service is None or not any(value > 0 for value in debt_service):
            return _not_applicable(spec, REASON_RBF_NO_REMITTANCE)
        actuals = [
            (
                index,
                MONEY_CONTEXT.divide(Decimal(ebitda[index]), Decimal(debt_service[index])),
            )
            for index in range(len(dates))
            if debt_service[index] > 0
        ]
    return _evaluate_rows(spec, actuals=actuals, dates=dates)


def evaluate_package(
    covenants: tuple[CovenantSpec, ...],
    *,
    ebitda: tuple[int, ...],
    net_debt: tuple[int, ...],
    cash: tuple[int, ...],
    cash_interest: tuple[int, ...] | None,
    debt_service: tuple[int, ...] | None,
    dates: tuple[date, ...],
) -> tuple[CovenantResult, ...]:
    """Evaluate the four pinned covenants and return them in pinned order.

    covenants must carry each of the four pinned names exactly once; the
    input order does not matter and the output always follows PACKAGE_ORDER:
    net_leverage, interest_cover, min_cash, dscr_floor. A non-tuple argument,
    a non-CovenantSpec element, a duplicate name, an unknown name, or a
    missing pinned name raises TypeError or ValueError.
    """
    if not isinstance(covenants, tuple):
        raise TypeError(f"covenants must be a tuple, got {type(covenants).__name__}")
    by_name: dict[str, CovenantSpec] = {}
    for spec in covenants:
        if not isinstance(spec, CovenantSpec):
            raise TypeError(f"specs must be CovenantSpec, got {type(spec).__name__}")
        if spec.name in by_name:
            raise ValueError(f"duplicate covenant spec for {spec.name!r}")
        by_name[spec.name] = spec
    unknown = [name for name in by_name if name not in PACKAGE_ORDER]
    if unknown:
        raise ValueError(f"unknown covenant specs: {', '.join(sorted(unknown))}")
    missing = [name for name in PACKAGE_ORDER if name not in by_name]
    if missing:
        raise ValueError(f"missing covenant specs: {', '.join(missing)}")
    return tuple(
        evaluate_covenant(
            by_name[name],
            ebitda=ebitda,
            net_debt=net_debt,
            cash=cash,
            cash_interest=cash_interest,
            debt_service=debt_service,
            dates=dates,
        )
        for name in PACKAGE_ORDER
    )
