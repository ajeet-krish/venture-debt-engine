"""Tests for vdebt.generator: seeded book, bands, paths, strata, book hash.

Covers architecture P2.2 and the binding test_generator entry in the P2.7
test plan: determinism across two generate_book calls (canonical document
bytes and book_sha256 identical, the digest reproducible from those bytes),
the committed book_sha256 fixture (drift fails printing both digests), draw
accounting (exactly 336 uniforms through a spy random.Random, borrower ids
SYN-0001 to SYN-0024 ascending), every emitted value inside its band with
inclusive money bounds and the per-structure term rules, the pre_revenue
consistency rule (flagged exactly when ARR is 0 cents, the pinned
four-member set, all-zero revenue and CFADS paths, unflagged ARR at or above
the band floor), the SYN-0002 zero-warrant bank slot, independent
recomputation of the revenue and CFADS path formulas for the first three
periods under a local Decimal context, the cash formula for the whole book,
the strata manifest over an empty sizing mapping (pre_revenue count 4,
zero_warrant_bank count 1, the four sizing-dependent strata present at
count 0, every entry carrying count and sorted members), and build_document
hygiene (no floats, no wall-clock strings in keys or values, integer money,
fixed-scale decimal strings).

Independent recomputations run under decimal.localcontext instead of the
engine money helpers, so a shared bug cannot hide behind the same code.
"""

import hashlib
import json
import random
import re
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path

import pytest

from vdebt import generator
from vdebt.config import load_scenario
from vdebt.generator import (
    BOOK_SIZE,
    DRAWS_PER_BORROWER,
    STRATA_NAMES,
    compute_strata_manifest,
    generate_book,
    predicate_pre_revenue,
    predicate_zero_warrant_bank,
)
from vdebt.snapshot import canonical_dumps

PIPELINE_DIR = Path(__file__).resolve().parent.parent
SCENARIO_PATH = PIPELINE_DIR / "config" / "scenario.toml"
BOOK_HASH_PATH = PIPELINE_DIR / "fixtures" / "generator" / "book_sha256.json"

# Loaded from the real committed scenario.toml, so every band, weight, and
# strata slot exercised below is exactly the pin the engine runs.
CFG = load_scenario(SCENARIO_PATH)

# Pinned pre_revenue stratum members for this seed (P2.2 slot SYN-0001 plus
# the three borrowers whose pre_revenue draw selects "yes").
EXPECTED_PRE_REVENUE = ["SYN-0001", "SYN-0003", "SYN-0013", "SYN-0018"]

# datetime.now() string form and isoformat both join a date to a clock
# time; no key or value in the hashed document may ever look like that.
WALL_CLOCK = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}|\d{2}:\d{2}:\d{2}")

# Storage scale per Decimal field of the hashed document, pinned here
# independently of generator._DECIMAL_SCALES (P2.2 draw table).
DECIMAL_SCALES: dict[str, int] = {
    "cash_coverage_months": 1,
    "monthly_growth": 4,
    "gross_margin": 2,
    "warrant_coverage": 2,
    "spread": 4,
    "closing_fee": 2,
    "rbf_cap": 2,
}

# Integer leaves of one borrower entry in build_document (money and counts).
MONEY_INT_FIELDS: tuple[str, ...] = (
    "arr_cents",
    "cash_cents",
    "last_round_cents",
    "net_burn_cents",
    "io_months",
    "term_months",
)

PATH_FIELDS: tuple[str, ...] = ("revenue_path", "cfads_path")

AMORTIZING_STRUCTURES: tuple[str, ...] = ("bullet", "io_then_straight_line")


def _assert_inclusive(value: Decimal, low: Decimal, high: Decimal, label: str) -> None:
    """Assert value lies inside the inclusive band bounds [low, high]."""
    assert low <= value <= high, (
        f"{label}: {value} outside inclusive band range [{low}, {high}]"
    )


def test_determinism_two_generations_byte_identical() -> None:
    first = generate_book(load_scenario(SCENARIO_PATH))
    second = generate_book(load_scenario(SCENARIO_PATH))

    first_bytes = canonical_dumps(first.build_document()).encode("utf-8")
    second_bytes = canonical_dumps(second.build_document()).encode("utf-8")

    assert first_bytes == second_bytes, "build_document canonical bytes drifted"
    assert first.book_sha256 == second.book_sha256, "book_sha256 drifted across calls"
    # The hash is defined as sha256 over the canonical document bytes, so it
    # must be reproducible from the bytes just compared.
    assert hashlib.sha256(first_bytes).hexdigest() == first.book_sha256


def test_book_hash_matches_committed_fixture() -> None:
    fixture = json.loads(BOOK_HASH_PATH.read_text(encoding="utf-8"))
    expected = fixture["book_sha256"]["value"]
    assert fixture["book_sha256"]["unit"] == "sha256_hex"
    assert fixture["seed"] == CFG.seed

    book = generate_book(CFG)

    assert book.book_sha256 == expected, (
        f"book hash drift: fixture digest {expected} "
        f"vs generated digest {book.book_sha256}"
    )


def test_draw_count_is_336_with_ascending_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    draws: list[float] = []

    class SpyRandom(random.Random):
        """random.Random subclass that records every .random() uniform."""

        def random(self) -> float:
            value = super().random()
            draws.append(value)
            return value

    monkeypatch.setattr(generator.random, "Random", SpyRandom)
    book = generate_book(CFG)

    assert BOOK_SIZE == 24
    assert DRAWS_PER_BORROWER == 14
    assert len(draws) == BOOK_SIZE * DRAWS_PER_BORROWER
    assert len(draws) == 336, f"expected 336 uniforms, counted {len(draws)}"
    assert all(0.0 <= uniform < 1.0 for uniform in draws)

    ids = [item.borrower_id for item in book.borrowers]
    assert ids == [f"SYN-{number:04d}" for number in range(1, BOOK_SIZE + 1)]


def test_every_value_inside_its_band() -> None:
    book = generate_book(CFG)
    bands = CFG.bands
    warrant_bounds = bands["warrant_coverage"].extras

    for item in book.borrowers:
        bid = item.borrower_id
        assert item.lender_type in ("bank", "non_bank"), (
            f"{bid}: unexpected lender_type {item.lender_type!r}"
        )
        assert item.structure in ("bridge", "bullet", "io_then_straight_line", "rbf_capped"), (
            f"{bid}: unexpected structure {item.structure!r}"
        )

        # Money bands are integer cents with inclusive bounds.
        _assert_inclusive(
            Decimal(item.last_round_cents),
            bands["last_round"].min,
            bands["last_round"].max,
            f"{bid} last_round_cents",
        )
        if item.arr_cents == 0:
            # Documented exception: ARR 0 cents only when pre_revenue holds
            # (full consistency check lives in the pre_revenue test).
            assert item.pre_revenue, f"{bid}: ARR 0 cents requires pre_revenue"
        else:
            _assert_inclusive(
                Decimal(item.arr_cents),
                bands["arr"].min,
                bands["arr"].max,
                f"{bid} arr_cents",
            )
        _assert_inclusive(
            Decimal(item.net_burn_cents),
            bands["net_burn"].min,
            bands["net_burn"].max,
            f"{bid} net_burn_cents",
        )

        _assert_inclusive(
            item.cash_coverage_months,
            bands["cash_coverage_months"].min,
            bands["cash_coverage_months"].max,
            f"{bid} cash_coverage_months",
        )
        _assert_inclusive(
            item.monthly_growth,
            bands["revenue_growth_monthly"].min,
            bands["revenue_growth_monthly"].max,
            f"{bid} monthly_growth",
        )
        _assert_inclusive(
            item.gross_margin,
            bands["gross_margin"].min,
            bands["gross_margin"].max,
            f"{bid} gross_margin",
        )
        _assert_inclusive(
            item.closing_fee,
            bands["closing_fee"].min,
            bands["closing_fee"].max,
            f"{bid} closing_fee",
        )
        _assert_inclusive(
            item.rbf_cap,
            bands["rbf_cap"].min,
            bands["rbf_cap"].max,
            f"{bid} rbf_cap",
        )
        _assert_inclusive(
            Decimal(item.io_months),
            bands["interest_only"].min,
            bands["interest_only"].max,
            f"{bid} io_months",
        )

        # Warrant and spread sub-bands are lender specific.
        if item.lender_type == "bank":
            warrant_low = warrant_bounds["bank_min_pct"]
            warrant_high = warrant_bounds["bank_max_pct"]
            pricing = bands["pricing_bank"]
        else:
            warrant_low = warrant_bounds["nonbank_min_pct"]
            warrant_high = warrant_bounds["nonbank_max_pct"]
            pricing = bands["pricing_nonbank"]
        _assert_inclusive(
            item.warrant_coverage,
            warrant_low,
            warrant_high,
            f"{bid} warrant_coverage ({item.lender_type})",
        )
        _assert_inclusive(
            item.spread,
            pricing.min,
            pricing.max,
            f"{bid} spread ({item.lender_type})",
        )

        # Term rules per structure (P2.2 derived values).
        if item.structure in AMORTIZING_STRUCTURES:
            assert item.term_months == item.io_months + CFG.generator.term_amortizing_periods, (
                f"{bid}: term {item.term_months} != io {item.io_months} "
                f"+ {CFG.generator.term_amortizing_periods}"
            )
            _assert_inclusive(
                Decimal(item.term_months),
                bands["term"].min,
                bands["term"].max,
                f"{bid} term_months",
            )
        elif item.structure == "rbf_capped":
            assert item.term_months == CFG.generator.rbf_horizon_periods, (
                f"{bid}: rbf term {item.term_months} != "
                f"{CFG.generator.rbf_horizon_periods}"
            )
        else:
            assert item.term_months == CFG.generator.bridge_tenor_months, (
                f"{bid}: bridge tenor {item.term_months} != "
                f"{CFG.generator.bridge_tenor_months}"
            )


def test_pre_revenue_flagged_iff_arr_zero() -> None:
    book = generate_book(CFG)
    flagged: list[str] = []

    for item in book.borrowers:
        assert item.pre_revenue == (item.arr_cents == 0), (
            f"{item.borrower_id}: pre_revenue {item.pre_revenue} disagrees "
            f"with arr_cents {item.arr_cents}"
        )
        if item.arr_cents == 0:
            flagged.append(item.borrower_id)
            assert all(value == 0 for value in item.revenue_path), (
                f"{item.borrower_id}: flagged revenue path not all zero"
            )
            assert all(value == 0 for value in item.cfads_path), (
                f"{item.borrower_id}: flagged cfads path not all zero"
            )
            assert len(item.revenue_path) == item.term_months
            assert len(item.cfads_path) == item.term_months
        else:
            assert item.arr_cents >= CFG.bands["arr"].min, (
                f"{item.borrower_id}: unflagged arr_cents {item.arr_cents} "
                f"below band floor {CFG.bands['arr'].min}"
            )

    assert sorted(flagged) == EXPECTED_PRE_REVENUE
    assert predicate_pre_revenue(book) == EXPECTED_PRE_REVENUE


def test_zero_warrant_bank_slot_syn0002() -> None:
    book = generate_book(CFG)
    slot = next(item for item in book.borrowers if item.borrower_id == "SYN-0002")

    assert slot.lender_type == "bank"
    assert slot.warrant_coverage == Decimal(0)
    # The pinned slot recipe also pins the spread to the shared band edge.
    assert slot.spread == Decimal("3.0000")
    assert "SYN-0002" in predicate_zero_warrant_bank(book)


def test_revenue_and_cfads_path_recompute() -> None:
    book = generate_book(CFG)
    item = next(entry for entry in book.borrowers if not entry.pre_revenue)

    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_UP
        base = Decimal(item.arr_cents) / Decimal(12)
        factor = Decimal(1) + item.monthly_growth / Decimal(100)
        for period in (1, 2, 3):
            expected_revenue = (base * factor ** (period - 1)).quantize(Decimal(1))
            assert int(expected_revenue) == item.revenue_path[period - 1], (
                f"{item.borrower_id} period {period}: revenue "
                f"{item.revenue_path[period - 1]} != recomputed {expected_revenue}"
            )
            expected_cfads = (
                expected_revenue * item.gross_margin / Decimal(100)
            ).quantize(Decimal(1))
            assert int(expected_cfads) == item.cfads_path[period - 1], (
                f"{item.borrower_id} period {period}: cfads "
                f"{item.cfads_path[period - 1]} != recomputed {expected_cfads}"
            )


def test_cash_formula_recompute() -> None:
    book = generate_book(CFG)

    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_UP
        for item in book.borrowers:
            expected = (Decimal(item.net_burn_cents) * item.cash_coverage_months).quantize(
                Decimal(1)
            )
            assert int(expected) == item.cash_cents, (
                f"{item.borrower_id}: cash_cents {item.cash_cents} "
                f"!= recomputed {expected}"
            )


def test_strata_manifest_empty_sizing() -> None:
    book = generate_book(CFG)
    manifest = compute_strata_manifest(book, {})

    assert list(manifest) == list(STRATA_NAMES)
    for name in STRATA_NAMES:
        entry = manifest[name]
        assert set(entry) == {"count", "members"}, f"{name}: bad entry keys {set(entry)}"
        assert entry["count"] == len(entry["members"]), f"{name}: count mismatch"
        assert entry["members"] == sorted(entry["members"]), (
            f"{name}: members not sorted {entry['members']}"
        )

    assert manifest["pre_revenue"]["count"] == 4
    assert manifest["pre_revenue"]["members"] == EXPECTED_PRE_REVENUE
    assert manifest["zero_warrant_bank"]["count"] == 1
    assert manifest["zero_warrant_bank"]["members"] == ["SYN-0002"]

    # Sizing-dependent strata stay present but empty until sizing feeds in.
    for name in ("infeasible_at_zero_draw", "runway_bound", "dscr_bound", "rbf_shortfall"):
        assert manifest[name]["count"] == 0, f"{name}: expected count 0"
        assert manifest[name]["members"] == [], f"{name}: expected empty members"


def test_build_document_hygiene() -> None:
    book = generate_book(CFG)
    doc = book.build_document()

    assert set(doc) == {"borrowers"}
    assert len(doc["borrowers"]) == BOOK_SIZE

    floats: list[str] = []
    walls: list[str] = []
    others: list[str] = []

    def visit(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and WALL_CLOCK.search(key):
                    walls.append(f"{path} key {key!r}")
                visit(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, f"{path}[{index}]")
        elif isinstance(node, float):
            floats.append(f"{path} = {node!r}")
        elif isinstance(node, (bool, int, str)):
            if isinstance(node, str) and WALL_CLOCK.search(node):
                walls.append(f"{path} = {node!r}")
        else:
            others.append(f"{path} type {type(node).__name__}")

    visit(doc, "$")
    assert not floats, f"float values banned in build_document: {floats}"
    assert not others, f"non JSON scalar leaves banned: {others}"
    assert not walls, f"wall-clock strings banned in build_document: {walls}"

    for entry in doc["borrowers"]:
        bid = entry["borrower_id"]
        for field in MONEY_INT_FIELDS:
            value = entry[field]
            assert type(value) is int, (
                f"{bid}.{field} must be a plain int, got {type(value).__name__}"
            )
        for field in PATH_FIELDS:
            for index, value in enumerate(entry[field]):
                assert type(value) is int, (
                    f"{bid}.{field}[{index}] must be a plain int, "
                    f"got {type(value).__name__}"
                )
        for field, scale in DECIMAL_SCALES.items():
            text = entry[field]
            assert isinstance(text, str), (
                f"{bid}.{field} must be a fixed-scale string, got {type(text).__name__}"
            )
            pattern = rf"-?[0-9]+\.[0-9]{{{scale}}}"
            assert re.fullmatch(pattern, text), (
                f"{bid}.{field} = {text!r} must carry exactly {scale} fraction digits"
            )
