"""Tests for the committed comparison.json payload (architecture P3.5, P3.8).

Covers the binding test_compare entry in the P3.8 test plan against the
committed payloads/2026-11-10/comparison.json ground truth (24 tables,
featured borrower SYN-0005) plus the pinned reason strings in
vdebt.compare. The eleven assertion groups:

1. identical inputs (spec 10.6 acceptance): every table's three rows
   repeat the shared block cell for cell so the published value fields
   are identical across rows, and the base rate source and
   source_timestamp match across rows and bind to the committed book
   rates snapshot;
2. citation presence: every band-sourced cell in every row (an envelope
   carrying a band key) carries a citation with a YYYY-MM-DD access date
   and a non-empty band name, with warrant and IO-month spot checks plus
   the facility, spread, and closing fee band citations where those
   bands are published (book.json sizing.band_cap, pricing.spread_pct,
   and inputs.closing_fee);
3. binding_constraint populated on all three rows of every table and
   equal to the sizing answer in book.json, infeasible tables included;
4. facility repeated across the three rows, or the uniform infeasible
   status shape with no fabricated value;
5. IO months: bridge and RBF rows not_applicable with their pinned
   reasons, the IO row an integer;
6. first-breach cells: each row publishes its own downside first-breach
   leaf (a months scale 1 breach value, the feasible status leaf, or the
   sizing not_applicable shape), never the retired phase 4 placeholder
   reason;
7. drawn-row consistency: every io_then_straight_line borrower's IO row
   total interest equals the book.json schedule interest sum;
8. featured echo: meta.featured_borrower_id equals scenario.toml [site];
9. table census: exactly 24 tables sorted by borrower_id, three rows
   each in the pinned bridge, io_then_straight_line, rbf_capped order;
10. meta binding: comparison meta book_sha256 equals book meta;
11. infeasible shape: SYN-0003 and SYN-0011 publish status leaves with
    reasons and never numeric zeros for facility, IRR, MOIC, total
    interest, and total receipts.

Tests read the committed payloads directly and never rebuild them; the
freshness of those committed bytes lives in test_build.py.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from vdebt.compare import (
    INFEASIBLE_REASON,
    IO_BRIDGE_REASON,
    IO_RBF_REASON,
    ROW_STRUCTURES,
)
from vdebt.config import load_scenario

PIPELINE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PIPELINE_DIR.parent
PAYLOAD_DIR = REPO_ROOT / "payloads" / "2026-11-10"
SCENARIO_PATH = PIPELINE_DIR / "config" / "scenario.toml"

ASOF = "2026-11-10"
EXPECTED_BORROWER_IDS = [f"SYN-{index:04d}" for index in range(1, 25)]
INFEASIBLE_BORROWER_IDS = ("SYN-0003", "SYN-0011")

# Shared cells every row repeats verbatim (spec 10.6 identical inputs).
SHARED_ROW_CELLS = ("facility", "closing_fee", "warrant_coverage", "all_in_rate")

# The pinned shared block key set (architecture P3.6 comparison schema).
SHARED_KEYS = [
    "all_in_rate",
    "base_rate",
    "close",
    "closing_fee",
    "facility",
    "warrant_coverage",
]

# Cells that must never publish a fabricated value when sizing is infeasible.
INFEASIBLE_VALUE_CELLS = ("facility", "lender_irr", "moic", "total_interest", "total_receipts")

# Citation shape: source name plus the access date (spec section 2 rule 2).
ACCESS_DATE = re.compile(r"\(accessed \d{4}-\d{2}-\d{2}\)$")

COMPARISON: dict[str, Any] = json.loads(
    (PAYLOAD_DIR / "comparison.json").read_text(encoding="utf-8")
)
BOOK: dict[str, Any] = json.loads((PAYLOAD_DIR / "book.json").read_text(encoding="utf-8"))
BOOK_BY_ID = {borrower["borrower_id"]: borrower for borrower in BOOK["borrowers"]}
TABLES: list[dict[str, Any]] = COMPARISON["tables"]


def _table(borrower_id: str) -> dict[str, Any]:
    """Return the single comparison table published for borrower_id."""
    matches = [table for table in TABLES if table["borrower_id"] == borrower_id]
    assert len(matches) == 1, f"expected one table for {borrower_id}, got {len(matches)}"
    return matches[0]


def _row(table: dict[str, Any], structure: str) -> dict[str, Any]:
    """Return the row pinned to structure inside table."""
    matches = [row for row in table["rows"] if row["structure"] == structure]
    assert len(matches) == 1, (
        f"{table['borrower_id']}: expected one {structure} row, got {len(matches)}"
    )
    return matches[0]


def _walk_dicts(value: Any, path: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (path, dict) for every dict nested under value."""
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from _walk_dicts(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_dicts(item, f"{path}[{index}]")


def _numeric_leaves(value: Any) -> list[int | float]:
    """Return every numeric leaf nested under value, booleans excluded."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [value]
    if isinstance(value, dict):
        found: list[int | float] = []
        for item in value.values():
            found.extend(_numeric_leaves(item))
        return found
    if isinstance(value, list):
        listed: list[int | float] = []
        for item in value:
            listed.extend(_numeric_leaves(item))
        return listed
    return []


# 1. Identical inputs across the three rows (spec 10.6 acceptance).


def test_identical_inputs_repeat_across_three_rows() -> None:
    for table in TABLES:
        borrower_id = table["borrower_id"]
        shared = table["shared"]
        assert sorted(shared) == SHARED_KEYS, (
            f"{borrower_id}: shared block keys drifted: {sorted(shared)}"
        )
        assert len(table["rows"]) == 3, f"{borrower_id}: expected three rows"

        # The rate provenance binds to the committed book rates snapshot.
        base_rate = shared["base_rate"]
        base_source = base_rate["source"]
        base_timestamp = base_rate["source_timestamp"]
        assert base_source == BOOK["rates"]["source"], (
            f"{borrower_id}: shared base rate source {base_source!r} drifted from book"
        )
        assert base_timestamp == BOOK["rates"]["source_timestamp"], (
            f"{borrower_id}: shared base rate source_timestamp {base_timestamp!r} drifted"
        )

        for cell_name in SHARED_ROW_CELLS:
            # The three rows' published value fields are identical.
            values = [row[cell_name].get("value") for row in table["rows"]]
            assert len(set(values)) == 1, (
                f"{borrower_id}: {cell_name} values differ across rows: {values}"
            )
            # And each row repeats the shared envelope cell for cell.
            for row in table["rows"]:
                assert row[cell_name] == shared[cell_name], (
                    f"{borrower_id} {row['structure']}: {cell_name} does not "
                    f"repeat the shared block cell"
                )

        for row in table["rows"]:
            assert row["all_in_rate"]["source"] == base_source, (
                f"{borrower_id} {row['structure']}: all_in_rate source "
                f"{row['all_in_rate'].get('source')!r} != shared base rate source"
            )
            assert row["all_in_rate"]["source_timestamp"] == base_timestamp, (
                f"{borrower_id} {row['structure']}: all_in_rate source_timestamp "
                f"{row['all_in_rate'].get('source_timestamp')!r} != shared"
            )
            irr = row["lender_irr"]
            if "source" in irr:
                assert irr["source"] == base_source, (
                    f"{borrower_id} {row['structure']}: lender_irr source "
                    f"{irr['source']!r} != shared base rate source"
                )
                assert irr["source_timestamp"] == base_timestamp, (
                    f"{borrower_id} {row['structure']}: lender_irr source_timestamp "
                    f"{irr['source_timestamp']!r} != shared"
                )


# 2. Citation presence on every band-sourced cell.


def test_citation_presence_on_band_sourced_cells() -> None:
    band_cells: list[str] = []
    for table in TABLES:
        for path, cell in _walk_dicts(table, table["borrower_id"]):
            if "band" not in cell:
                continue
            band_cells.append(path)
            assert isinstance(cell["band"], str) and cell["band"], (
                f"{path}: band-sourced cell carries no band name"
            )
            citation = cell.get("citation")
            assert isinstance(citation, str) and citation, (
                f"{path}: band-sourced cell carries no citation"
            )
            assert ACCESS_DATE.search(citation), (
                f"{path}: citation carries no access date: {citation}"
            )

    # 72 row warrant cells plus 24 shared warrant cells plus 24 IO-month cells.
    assert len(band_cells) >= 120, (
        f"only {len(band_cells)} band-sourced cells published, expected at least 120"
    )

    # Spot check: the warrant cell on every row and in every shared block,
    # plus the IO-month cell on every IO row.
    for table in TABLES:
        borrower_id = table["borrower_id"]
        cells = [table["shared"]["warrant_coverage"]]
        cells.extend(row["warrant_coverage"] for row in table["rows"])
        cells.append(_row(table, "io_then_straight_line")["io_months"])
        for cell in cells:
            assert isinstance(cell.get("band"), str) and cell["band"], (
                f"{borrower_id}: warrant or IO-month cell lost its band name"
            )
            assert ACCESS_DATE.search(cell.get("citation", "")), (
                f"{borrower_id}: warrant or IO-month citation carries no access "
                f"date: {cell.get('citation')}"
            )

    # The facility, spread, and closing fee bands are published in book.json:
    # the comparison's facility and closing fee cells are derived products
    # (asof plus provenance, never a band key) and comparison rows carry no
    # spread cell, so those band citations live where the bands are drawn.
    for borrower in BOOK["borrowers"]:
        borrower_id = borrower["borrower_id"]
        for label, cell in (
            ("sizing.band_cap", borrower["sizing"].get("band_cap")),
            ("pricing.spread_pct", borrower["pricing"].get("spread_pct")),
            ("inputs.closing_fee", borrower["inputs"].get("closing_fee")),
        ):
            assert isinstance(cell, dict), f"{borrower_id}: {label} missing"
            assert isinstance(cell.get("band"), str) and cell["band"], (
                f"{borrower_id}: {label} carries no band name"
            )
            assert ACCESS_DATE.search(cell.get("citation", "")), (
                f"{borrower_id}: {label} citation carries no access date: "
                f"{cell.get('citation')}"
            )


# 3. binding_constraint populated on all three rows of every table.


def test_binding_constraint_populated_on_every_row() -> None:
    for table in TABLES:
        borrower_id = table["borrower_id"]
        sizing_binding = BOOK_BY_ID[borrower_id]["sizing"]["binding_constraint"]
        assert isinstance(sizing_binding, str) and sizing_binding, (
            f"{borrower_id}: book sizing carries no binding constraint"
        )
        for row in table["rows"]:
            binding = row["binding_constraint"]
            assert isinstance(binding, str) and binding, (
                f"{borrower_id} {row['structure']}: binding_constraint not populated"
            )
            assert binding == sizing_binding, (
                f"{borrower_id} {row['structure']}: binding_constraint {binding!r} "
                f"does not repeat the sizing answer {sizing_binding!r}"
            )


# 4. Facility repeated across the three rows (or the infeasible shape).


def test_facility_repeats_across_three_rows() -> None:
    for table in TABLES:
        borrower_id = table["borrower_id"]
        shared_facility = table["shared"]["facility"]
        facilities = [row["facility"] for row in table["rows"]]
        for row in table["rows"]:
            assert row["facility"] == facilities[0], (
                f"{borrower_id} {row['structure']}: facility cell differs from "
                f"the other rows"
            )
        if "value" in shared_facility:
            for cell in facilities:
                assert cell.get("value") == shared_facility["value"], (
                    f"{borrower_id}: row facility {cell.get('value')!r} != shared "
                    f"facility {shared_facility['value']!r}"
                )
        else:
            for cell in facilities:
                assert "value" not in cell, (
                    f"{borrower_id}: infeasible row fabricates a facility value "
                    f"{cell.get('value')!r}"
                )
                assert cell["status"]["value"] == "not_applicable", (
                    f"{borrower_id}: infeasible facility status drifted"
                )
                assert cell.get("reason") == INFEASIBLE_REASON, (
                    f"{borrower_id}: infeasible facility reason drifted: "
                    f"{cell.get('reason')!r}"
                )


# 5. IO-month cells: not_applicable on bridge and RBF, integer on IO.


def test_io_months_cells_per_structure() -> None:
    for table in TABLES:
        borrower_id = table["borrower_id"]
        cells = {
            "bridge": _row(table, "bridge")["io_months"],
            "rbf_capped": _row(table, "rbf_capped")["io_months"],
        }
        expected_reasons = {
            "bridge": IO_BRIDGE_REASON,
            "rbf_capped": IO_RBF_REASON,
        }
        for structure, cell in cells.items():
            assert "value" not in cell, (
                f"{borrower_id} {structure}: io_months publishes a value {cell.get('value')!r}"
            )
            assert cell["status"]["value"] == "not_applicable", (
                f"{borrower_id} {structure}: io_months status drifted"
            )
            reason = cell.get("reason")
            assert isinstance(reason, str) and reason, (
                f"{borrower_id} {structure}: io_months carries no reason"
            )
            assert reason == expected_reasons[structure], (
                f"{borrower_id} {structure}: io_months reason drifted: {reason!r}"
            )
        io_value = _row(table, "io_then_straight_line")["io_months"].get("value")
        assert isinstance(io_value, int) and not isinstance(io_value, bool), (
            f"{borrower_id}: IO row io_months must be an integer, got {io_value!r}"
        )
        assert io_value > 0, f"{borrower_id}: IO row io_months must be positive"


# 6. First-breach leaf on every row (real values, phase 4 replacement).


def test_first_breach_month_real_values_on_every_row() -> None:
    # Phase 4 replaced the placeholder in place: each row publishes its own
    # downside first-breach leaf, never the retired placeholder reason.
    assert "covenant engine lands in phase 4" not in json.dumps(COMPARISON), (
        "comparison.json still carries the retired phase 4 placeholder reason"
    )
    for table in TABLES:
        for row in table["rows"]:
            borrower_id = table["borrower_id"]
            cell = row["first_breach_month"]
            status = cell["status"]["value"]
            if status == "breach":
                # Breach publishes the months scale 1 value with no reason.
                assert cell["unit"] == "months", (
                    f"{borrower_id} {row['structure']}: breach cell unit "
                    f"{cell['unit']!r} is not months"
                )
                assert cell["scale"] == 1, (
                    f"{borrower_id} {row['structure']}: breach cell scale "
                    f"{cell['scale']!r} is not 1"
                )
                value = cell.get("value")
                assert isinstance(value, str) and re.fullmatch(r"\d+\.\d", value), (
                    f"{borrower_id} {row['structure']}: breach cell value "
                    f"{value!r} is not a months scale 1 string"
                )
                assert "reason" not in cell, (
                    f"{borrower_id} {row['structure']}: breach cell carries a reason"
                )
            elif status == "feasible":
                # Feasible: no breach through the horizon, no value, no reason.
                assert "value" not in cell, (
                    f"{borrower_id} {row['structure']}: feasible cell carries "
                    f"a value {cell.get('value')!r}"
                )
                assert "reason" not in cell, (
                    f"{borrower_id} {row['structure']}: feasible cell carries a reason"
                )
            else:
                assert status == "not_applicable", (
                    f"{borrower_id} {row['structure']}: unknown first-breach "
                    f"status {status!r}"
                )
                reason = cell.get("reason")
                assert reason == INFEASIBLE_REASON, (
                    f"{borrower_id} {row['structure']}: not_applicable first-breach "
                    f"reason {reason!r} is not the sizing reason"
                )


# 7. Drawn IO row equals the book schedule totals.


def test_drawn_io_row_matches_book_schedule_total_interest() -> None:
    checked = 0
    for borrower in BOOK["borrowers"]:
        if borrower["structure"] != "io_then_straight_line":
            continue
        checked += 1
        borrower_id = borrower["borrower_id"]
        schedule = borrower["schedule"]
        assert schedule.get("kind") == "amortizing", (
            f"{borrower_id}: drawn IO schedule is not amortizing: {schedule.get('kind')}"
        )
        book_total = sum(row["interest"]["value"] for row in schedule["rows"])
        published = _row(_table(borrower_id), "io_then_straight_line")[
            "total_interest"
        ].get("value")
        assert published == book_total, (
            f"{borrower_id}: IO row total interest {published} != book schedule "
            f"total interest {book_total}"
        )
    assert checked >= 1, "the committed book carries no io_then_straight_line borrower"


# 8. Featured borrower echoed from scenario.toml [site].


def test_featured_borrower_echoed_from_scenario_site() -> None:
    cfg = load_scenario(SCENARIO_PATH)
    featured = COMPARISON["meta"]["featured_borrower_id"]
    assert featured == cfg.site.featured_borrower_id, (
        f"meta.featured_borrower_id {featured!r} != scenario.toml [site] "
        f"{cfg.site.featured_borrower_id!r}"
    )
    published_ids = {table["borrower_id"] for table in TABLES}
    assert featured in published_ids, (
        f"featured borrower {featured!r} has no comparison table"
    )


# 9. Table census: 24 sorted tables, three rows in pinned order.


def test_table_census_and_pinned_row_order() -> None:
    borrower_ids = [table["borrower_id"] for table in TABLES]
    assert len(TABLES) == 24, f"expected 24 tables, got {len(TABLES)}"
    assert borrower_ids == sorted(borrower_ids), (
        f"tables are not sorted by borrower_id: {borrower_ids}"
    )
    assert borrower_ids == EXPECTED_BORROWER_IDS, (
        f"table census drifted: {borrower_ids}"
    )
    for table in TABLES:
        structures = [row["structure"] for row in table["rows"]]
        assert len(table["rows"]) == 3, (
            f"{table['borrower_id']}: expected three rows, got {len(table['rows'])}"
        )
        assert structures == list(ROW_STRUCTURES), (
            f"{table['borrower_id']}: row order {structures} != pinned "
            f"{list(ROW_STRUCTURES)}"
        )


# 10. Meta binding: comparison meta book_sha256 equals book meta.


def test_comparison_meta_binds_to_book_sha256() -> None:
    comparison_hash = COMPARISON["meta"]["book_sha256"]
    book_hash = BOOK["meta"]["book_sha256"]
    assert comparison_hash["value"] == book_hash["value"], (
        f"comparison meta book_sha256 {comparison_hash['value']!r} != book meta "
        f"{book_hash['value']!r}"
    )
    assert comparison_hash["unit"] == book_hash["unit"] == "sha256_hex"
    assert COMPARISON["meta"]["asof"] == ASOF, (
        f"comparison meta asof {COMPARISON['meta']['asof']!r} != pinned {ASOF!r}"
    )


# 11. Infeasible rows publish status leaves, never fabricated zeros.


def test_infeasible_rows_publish_status_leaves() -> None:
    infeasible_ids = tuple(
        sorted(
            borrower["borrower_id"]
            for borrower in BOOK["borrowers"]
            if borrower["sizing"]["status"]["value"] != "feasible"
        )
    )
    assert infeasible_ids == INFEASIBLE_BORROWER_IDS, (
        f"infeasible borrower set drifted: {infeasible_ids}"
    )

    for borrower_id in INFEASIBLE_BORROWER_IDS:
        table = _table(borrower_id)
        shared_facility = table["shared"]["facility"]
        assert "value" not in shared_facility, (
            f"{borrower_id}: shared facility publishes a value while sizing is "
            f"infeasible: {shared_facility.get('value')!r}"
        )
        for row in table["rows"]:
            for cell_name in INFEASIBLE_VALUE_CELLS:
                cell = row[cell_name]
                where = f"{borrower_id} {row['structure']}.{cell_name}"
                assert "value" not in cell, (
                    f"{where}: publishes a value {cell.get('value')!r} with no "
                    f"facility to fund"
                )
                numbers = _numeric_leaves(cell)
                assert numbers == [], f"{where}: publishes numeric leaves {numbers}"
                assert cell["status"]["value"] == "not_applicable", (
                    f"{where}: status leaf drifted from not_applicable"
                )
                assert cell.get("reason") == INFEASIBLE_REASON, (
                    f"{where}: status leaf reason drifted: {cell.get('reason')!r}"
                )
