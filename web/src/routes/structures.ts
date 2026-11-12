/**
 * Structures route: the three-row comparison table for one committed
 * borrower, plus illustrative rationale notes.
 *
 * Every cell prints a committed envelope from comparison.json. Band
 * sourced cells carry their citation as a small mono note beside the
 * value, the first breach column prints its published leaf as a covenant
 * stamp chip, and the facility repeats disclosure sits under the
 * table. Nothing is recomputed.
 */
import "../styles/main.css";
import comparisonJson from "../data/comparison.json";
import { mountShell } from "../components/page";
import { mountSelector } from "../components/selector";
import { RATIONALE_NOTES, STRUCTURE_LABELS } from "../content/rationale";
import { readAsofLink, renderAsofNote } from "../lib/asof";
import { breachChip, breachView, constraintLabel, envelopeText, statusLabel } from "../lib/cell";
import { chartAsset } from "../lib/chart";
import {
  isStatusCell,
  loadComparison,
  type Comparison,
  type ComparisonCell,
  type ComparisonRow,
  type ComparisonTable,
} from "../lib/payload";

const { main } = mountShell();
const comparison: Comparison = loadComparison(comparisonJson);

/** Column header plus whether the column holds formatted figures. */
interface Column {
  readonly header: string;
  readonly numeric: boolean;
}

const COLUMNS: readonly Column[] = [
  { header: "Structure", numeric: false },
  { header: "Facility", numeric: true },
  { header: "All-in rate", numeric: true },
  { header: "Term (months)", numeric: true },
  { header: "IO (months)", numeric: true },
  { header: "Total interest", numeric: true },
  { header: "Closing fee", numeric: true },
  { header: "Lender IRR", numeric: true },
  { header: "MOIC", numeric: true },
  { header: "Min DSCR", numeric: true },
  { header: "Binding constraint", numeric: false },
  { header: "First breach", numeric: false },
  { header: "Warrant coverage", numeric: true },
  { header: "Total receipts", numeric: true },
  { header: "Citations", numeric: false },
];

function mount(selector: string): Element {
  const node = main.querySelector(selector);
  if (node === null) {
    throw new Error(`structures route: missing ${selector} mount point in structures.html`);
  }
  return node;
}

function element(tag: string, className?: string, text?: string): HTMLElement {
  const node = document.createElement(tag);
  if (className !== undefined) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

/** Display text for any comparison cell, figures and statuses alike. */
function cellText(cell: ComparisonCell): string {
  return isStatusCell(cell) ? statusLabel(cell.status.value) : envelopeText(cell);
}

/** Small mono note carrying the band name and citation of a band sourced envelope. */
function citationNote(env: { readonly citation?: string; readonly band?: string }): HTMLElement | null {
  if (typeof env.citation !== "string") {
    return null;
  }
  const text =
    typeof env.band === "string" ? `${env.band}: ${env.citation}` : env.citation;
  return element("span", "cell-note cite", text);
}

/** A figure cell, or an honest status cell with its reason companion. */
function figureCell(cell: ComparisonCell, numeric: boolean): HTMLTableCellElement {
  const td = element("td", numeric ? "num" : undefined) as HTMLTableCellElement;
  if (isStatusCell(cell)) {
    td.append(
      element("span", "chip chip-pending", statusLabel(cell.status.value)),
      element("span", "cell-note", cell.reason),
    );
    return td;
  }
  td.append(element("span", "cell-value", envelopeText(cell)));
  const note = citationNote(cell);
  if (note !== null) {
    td.append(note);
  }
  return td;
}
/** The first breach column: the published leaf as a covenant stamp chip. */
function firstBreachCell(row: ComparisonRow): HTMLTableCellElement {
  const chip = breachChip(breachView(row.first_breach_month));
  const td = element("td") as HTMLTableCellElement;
  td.append(element("span", chip.className, chip.text));
  if (chip.reason !== undefined) {
    td.append(element("span", "cell-note", chip.reason));
  }
  return td;
}

/** Distinct citations carried by the envelopes of one row. */
function rowCitations(row: ComparisonRow): readonly string[] {
  const found = new Set<string>();
  for (const value of Object.values(row)) {
    if (value !== null && typeof value === "object" && "citation" in value) {
      const citation = (value as { readonly citation?: unknown }).citation;
      if (typeof citation === "string") {
        found.add(citation);
      }
    }
  }
  return [...found];
}

/** The citations column: band names in use plus their citation strings. */
function citationsCell(row: ComparisonRow): HTMLTableCellElement {
  const td = element("td", "citations") as HTMLTableCellElement;
  const tokens = element("ul", "band-tokens");
  for (const band of row.band_names) {
    tokens.append(element("li", "band-token", band));
  }
  td.append(tokens);
  for (const citation of rowCitations(row)) {
    td.append(element("span", "cell-note cite", citation));
  }
  return td;
}

/** One table row: structure header plus the fourteen data cells. */
function tableRow(row: ComparisonRow): HTMLTableRowElement {
  const tr = element("tr") as HTMLTableRowElement;
  const head = element(
    "th",
    "row-head",
    STRUCTURE_LABELS[row.structure] ?? row.structure,
  ) as HTMLTableCellElement;
  head.setAttribute("scope", "row");
  tr.append(head);

  const cells: readonly ComparisonCell[] = [
    row.facility,
    row.all_in_rate,
    row.term_months,
    row.io_months,
    row.total_interest,
    row.closing_fee,
    row.lender_irr,
    row.moic,
    row.min_dscr,
  ];
  cells.forEach((cell, index) => {
    tr.append(figureCell(cell, COLUMNS[index + 1].numeric));
  });

  const binding = element("td", "constraint");
  binding.append(
    element("span", "cell-value", constraintLabel(row.binding_constraint)),
    element("span", "cell-note", row.binding_constraint),
  );
  tr.append(binding, firstBreachCell(row));

  tr.append(figureCell(row.warrant_coverage, true), figureCell(row.total_receipts, true));
  tr.append(citationsCell(row));
  return tr;
}

/** The shared terms block: identical inputs behind all three rows. */
function sharedTerms(table: ComparisonTable): HTMLElement {
  const shared = table.shared;
  const rows = element("dl", "spec-grid");
  const entries: ReadonlyArray<{ readonly label: string; readonly cell: ComparisonCell }> = [
    { label: "Close", cell: shared.close },
    { label: "Base rate", cell: shared.base_rate },
    { label: "All-in rate", cell: shared.all_in_rate },
    { label: "Facility", cell: shared.facility },
    { label: "Closing fee", cell: shared.closing_fee },
    { label: "Warrant coverage", cell: shared.warrant_coverage },
  ];
  for (const entry of entries) {
    const row = element("div", "spec-row");
    row.append(element("dt", undefined, entry.label));
    const detail = element("dd");
    detail.append(element("span", undefined, cellText(entry.cell)));
    if (entry.label === "Facility" && !isStatusCell(entry.cell)) {
      detail.append(element("span", "spec-note", "Repeats in all three rows, disclosed under the table."));
    } else if (isStatusCell(entry.cell)) {
      detail.append(element("span", "spec-note", entry.cell.reason));
    } else if (typeof entry.cell.citation === "string") {
      const band = typeof entry.cell.band === "string" ? `${entry.cell.band}: ` : "";
      detail.append(element("span", "spec-note", `${band}${entry.cell.citation}`));
    }
    row.append(detail);
    rows.append(row);
  }
  return rows;
}

/** The comparison table inside its horizontal scroll container. */
function comparisonTable(table: ComparisonTable): HTMLElement {
  const scroll = element("div", "table-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", `Comparison table for ${table.borrower_id}, scrolls horizontally`);

  const html = element("table", "ledger-table");
  const caption = element(
    "caption",
    undefined,
    `Three structures on identical committed inputs, ${table.borrower_id}, asof ${envelopeText(table.shared.close)}`,
  );
  html.append(caption);

  const thead = element("thead");
  const headRow = element("tr");
  for (const column of COLUMNS) {
    const th = element("th", column.numeric ? "num" : undefined, column.header);
    th.setAttribute("scope", "col");
    headRow.append(th);
  }
  thead.append(headRow);
  html.append(thead);

  const tbody = element("tbody");
  for (const row of table.rows) {
    tbody.append(tableRow(row));
  }
  html.append(tbody);
  scroll.append(html);
  return scroll;
}

/** Observed row values under each note, printed verbatim as formatted. */
function observedLine(row: ComparisonRow): string {
  const parts = [
    `term ${cellText(row.term_months)} months`,
    `total interest ${cellText(row.total_interest)}`,
    `lender IRR ${cellText(row.lender_irr)}`,
    `MOIC ${cellText(row.moic)}`,
    `minimum DSCR ${cellText(row.min_dscr)}`,
  ];
  return `Observed in this table: ${parts.join(", ")}.`;
}

/**
 * Mount the build time cash flow chart for the featured borrower. When
 * generation did not emit the asset the honest placeholder already in
 * structures.html stays on screen: no path is drawn in its place.
 */
function renderCashflowChart(): void {
  const host = mount("[data-cashflow-chart]");
  const url = chartAsset("cashflow");
  if (url === null) {
    return;
  }
  host.textContent = "";

  const figure = element("figure", "chart-figure");
  const scroll = element("div", "chart-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", "Cumulative lender receipts chart, scrolls horizontally");

  const image = document.createElement("img");
  image.className = "chart-img";
  image.src = url;
  image.width = 960;
  image.height = 840;
  image.loading = "lazy";
  image.decoding = "async";
  image.alt =
    "Two panel chart: the cumulative lender receipts path for the featured " +
    "borrower's committed structure, and bars of the published total receipts " +
    "for bridge, io_then_straight_line, and rbf_capped";

  scroll.append(image);
  figure.append(scroll);
  figure.append(
    element(
      "figcaption",
      "chart-note",
      `Generated at build time from the committed payload for the featured borrower ${featured}. ` +
        "Per period lists exist only for a borrower's own structure, so the other " +
        "structures appear as published totals. No figure is computed in the browser.",
    ),
  );
  host.append(figure);
}

function show(borrowerId: string): void {
  const table = comparison.tables.find((entry) => entry.borrower_id === borrowerId);
  const host = mount("[data-comparison]");
  host.textContent = "";
  const title = document.getElementById("comparison-title");
  if (title !== null) {
    title.textContent = `Comparison for ${borrowerId}`;
  }

  const rationaleHost = mount("[data-rationale]");
  rationaleHost.textContent = "";

  if (table === undefined) {
    const notice = element("div", "notice");
    notice.append(
      element("p", "notice-head", "No committed comparison table"),
      element(
        "p",
        undefined,
        `The committed comparison payload carries no table for ${borrowerId}. ` +
          "Nothing is rendered for it, because every figure on this page prints from a committed payload.",
      ),
    );
    host.append(notice);
    return;
  }

  host.append(
    element("p", "stamp", "illustrative sizing, not credit approval"),
    element("h3", undefined, "Shared terms"),
    sharedTerms(table),
    comparisonTable(table),
    element(
      "p",
      "disclosure",
      "the facility is the borrower's serviceable capacity on the canonical " +
        "amortizing basis; structures differ in schedule and returns at that capacity",
    ),
  );
  for (const note of RATIONALE_NOTES) {
    const row = table.rows.find((entry) => entry.structure === note.structure);
    const article = element("article", "rationale");
    article.append(element("h3", undefined, note.title), element("p", "section-copy", note.body));
    if (row !== undefined) {
      article.append(element("p", "observed", observedLine(row)));
    }
    rationaleHost.append(article);
  }
}

const options = comparison.tables.map((entry) => ({
  value: entry.borrower_id,
  label: `${entry.borrower_id} (${STRUCTURE_LABELS[entry.rows[0].structure] ?? entry.rows[0].structure})`,
}));
const featured = comparison.meta.featured_borrower_id;
const defaultId = options.some((option) => option.value === featured) ? featured : options[0].value;

mountSelector(mount("[data-selector]"), {
  selectId: "structures-borrower",
  label: "Borrower",
  options,
  defaultValue: defaultId,
  onChange: show,
});

renderAsofNote(mount("[data-asof]"), readAsofLink(comparison.meta.asof));
show(defaultId);
renderCashflowChart();
