/**
 * Dashboard route: the committed book summary rendered verbatim
 * (architecture P4.6).
 *
 * When meta.book_summary is present, every figure on this page is a
 * published count printed through lib/format.ts: feasible and infeasible
 * solves, the binding constraint mix, and the capacity bins as a ledger
 * table with their published range edges. Nothing is counted, summed, or
 * binned in this file, and no total appears unless the payload publishes
 * it as an envelope. When the block is absent, the honest placeholder
 * already in dashboard.html stays on screen. The two charts are build
 * time SVG assets mounted through the shared chart pattern, each with a
 * one line provenance footer. No arithmetic runs anywhere in this file.
 */
import "../styles/main.css";
import { mountShell } from "../components/page";
import { readAsofLink, renderAsofNote } from "../lib/asof";
import { chartAsset } from "../lib/chart";
import { constraintLabel, dollars, envelopeText } from "../lib/cell";
import { formatFixed } from "../lib/format";
import {
  type BindingConstraintMix,
  type BookSummary,
  type CapacityBin,
  type CountValue,
} from "../lib/payload";

const { main, book } = mountShell();

function mount(selector: string): Element {
  const node = main.querySelector(selector);
  if (node === null) {
    throw new Error(`dashboard route: missing ${selector} mount point in dashboard.html`);
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

/** The honest placeholder notice, in the pattern the skeleton established. */
function placeholder(host: Element, head: string, body: string): void {
  const notice = element("div", "notice");
  notice.setAttribute("role", "note");
  notice.append(element("p", "notice-head", head), element("p", undefined, body));
  host.append(notice);
}

/**
 * One committed count as display text. Grouping and point placement
 * only: the digits that leave here are the digits the payload published,
 * whether it published a bare integer or a count envelope.
 */
function countText(value: CountValue): string {
  if (typeof value === "number") {
    return formatFixed(String(value), 0);
  }
  if (value.unit === "count") {
    return formatFixed(String(value.value), 0);
  }
  return envelopeText(value);
}

/** A published bin edge in display dollars: point placement on cents. */
function edgeText(edge: CountValue): string {
  const cents = typeof edge === "number" ? String(edge) : String(edge.value);
  return dollars(cents);
}

/** Read one key from a flat record only when the key is published. */
function lookupCount(
  node: Readonly<Record<string, CountValue>>,
  key: string,
): CountValue | undefined {
  return key in node ? node[key] : undefined;
}

/**
 * One mix count from the published block: the top level key first, then
 * the nested counts object. Both published shapes read the same way.
 */
function mixCount(mix: BindingConstraintMix, key: string): CountValue | undefined {
  const direct = topCount(mix, key);
  if (direct !== undefined) {
    return direct;
  }
  const counts = mix.counts;
  if (counts === undefined) {
    return undefined;
  }
  return lookupCount(counts, key);
}

function topCount(mix: BindingConstraintMix, key: string): CountValue | undefined {
  switch (key) {
    case "feasible_count":
      return mix.feasible_count;
    case "infeasible_count":
      return mix.infeasible_count;
    case "runway":
      return mix.runway;
    case "dscr":
      return mix.dscr;
    case "band_cap":
      return mix.band_cap;
    default:
      return undefined;
  }
}

/** Solve rows: the two feasibility counts of the committed book. */
const SOLVE_ROWS: ReadonlyArray<{ readonly key: string; readonly label: string }> = [
  { key: "feasible_count", label: "Feasible solves" },
  { key: "infeasible_count", label: "Infeasible solves" },
];

/** Mix rows: the three published binding constraint counts. */
const MIX_ROWS: ReadonlyArray<{ readonly key: string; readonly token: string }> = [
  { key: "runway", token: "runway" },
  { key: "dscr", token: "dscr" },
  { key: "band_cap", token: "band_cap" },
];

/** One count cell: the published count, or the honest absence note. */
function countCell(value: CountValue | undefined, note?: string): HTMLTableCellElement {
  const td = element("td", "num") as HTMLTableCellElement;
  if (value === undefined) {
    td.append(element("span", "cell-note", "not published in the payload"));
    return td;
  }
  td.append(element("span", "cell-value", countText(value)));
  if (note !== undefined) {
    td.append(element("span", "cell-note", note));
  }
  return td;
}

/** The committed counts ledger: solves and mix, verbatim from the block. */
function countsTable(mix: BindingConstraintMix): HTMLElement {
  const scroll = element("div", "table-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", "Committed book counts ledger, scrolls horizontally");

  const table = element("table", "ledger-table");
  table.append(
    element(
      "caption",
      undefined,
      "Committed book counts, verbatim from meta.book_summary",
    ),
  );

  const headRow = element("tr");
  for (const header of ["Measure", "Published count"]) {
    const th = element("th", header === "Measure" ? undefined : "num", header);
    th.setAttribute("scope", "col");
    headRow.append(th);
  }
  const thead = element("thead");
  thead.append(headRow);
  table.append(thead);

  const tbody = element("tbody");
  for (const row of SOLVE_ROWS) {
    const tr = element("tr");
    const th = element("th", "row-head", row.label) as HTMLTableCellElement;
    th.setAttribute("scope", "row");
    tr.append(th, countCell(mixCount(mix, row.key)));
    tbody.append(tr);
  }
  for (const row of MIX_ROWS) {
    const tr = element("tr");
    const th = element(
      "th",
      "row-head",
      `${constraintLabel(row.token)} binding`,
    ) as HTMLTableCellElement;
    th.setAttribute("scope", "row");
    tr.append(th, countCell(mixCount(mix, row.key), row.token));
    tbody.append(tr);
  }
  table.append(tbody);
  scroll.append(table);
  return scroll;
}

/** The capacity bin ledger: published range text and published count. */
function binsTable(bins: readonly CapacityBin[]): HTMLElement {
  const scroll = element("div", "table-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", "Capacity bin ledger, scrolls horizontally");

  const table = element("table", "ledger-table");
  table.append(
    element(
      "caption",
      undefined,
      "Capacity bins, low edge inclusive and high edge exclusive, last bin inclusive",
    ),
  );

  const headRow = element("tr");
  for (const header of ["Bin range (US dollars)", "Borrowers (count)"]) {
    const th = element("th", header.startsWith("Bin range") ? undefined : "num", header);
    th.setAttribute("scope", "col");
    headRow.append(th);
  }
  const thead = element("thead");
  thead.append(headRow);
  table.append(thead);

  const tbody = element("tbody");
  for (const bin of bins) {
    const tr = element("tr");
    const th = element(
      "th",
      "row-head",
      `${edgeText(bin.bin_low)} to ${edgeText(bin.bin_high)}`,
    ) as HTMLTableCellElement;
    th.setAttribute("scope", "row");
    tr.append(th, countCell(bin.count));
    tbody.append(tr);
  }
  table.append(tbody);
  scroll.append(table);
  return scroll;
}

/** Render the committed counts, or keep the honest placeholder. */
function renderSummary(): void {
  const host = mount("[data-dashboard-summary]");
  host.textContent = "";
  const summary: BookSummary | undefined = book.meta.book_summary;

  if (summary === undefined) {
    placeholder(
      host,
      "Dashboard figures not published yet",
      "The committed book summary (meta.book_summary) has not landed in " +
        "book.json, so this page prints no dashboard number. The capacity " +
        "distribution, binding constraint mix, and feasible and infeasible " +
        "counts render verbatim from that block when the phase 4 payload " +
        "build publishes it. Until then the chart slots below keep their " +
        "honest placeholders.",
    );
    return;
  }

  const mix = summary.binding_constraint_mix;
  if (mix === undefined) {
    placeholder(
      host,
      "Binding constraint mix not published yet",
      "meta.book_summary is present in the committed payload, but publishes " +
        "no binding_constraint_mix, so this section prints no count. Nothing " +
        "is counted in the browser to fill the gap.",
    );
    return;
  }

  host.append(
    countsTable(mix),
    element(
      "p",
      "disclosure",
      "Counts print verbatim from meta.book_summary; nothing is counted, " +
        "summed, or binned in the browser.",
    ),
  );
}

/** Render the capacity bin ledger, or keep its section honest and empty. */
function renderBins(): void {
  const host = mount("[data-dashboard-bins]");
  host.textContent = "";
  const summary: BookSummary | undefined = book.meta.book_summary;

  if (summary === undefined) {
    // The summary placeholder above already states that nothing publishes
    // yet, so this slot stays empty rather than stacking a second notice.
    return;
  }
  const bins = summary.capacity_distribution;
  if (bins === undefined || bins.length === 0) {
    placeholder(
      host,
      "Capacity bins not published yet",
      "meta.book_summary is present in the committed payload, but publishes " +
        "no capacity_distribution, so this section prints no bin ledger. The " +
        "bin edges and counts come from the pipeline, never from the browser.",
    );
    return;
  }
  host.append(
    binsTable(bins),
    element(
      "p",
      "disclosure",
      "Bin edges and counts print verbatim from the committed payload; the " +
        "pipeline did the binning.",
    ),
  );
}

/** One static chart position: asset name, intrinsic size, alt text. */
interface ChartSlot {
  readonly selector: string;
  readonly asset: string;
  readonly regionLabel: string;
  readonly alt: string;
  readonly width: number;
  readonly height: number;
}

const CHARTS: readonly ChartSlot[] = [
  {
    selector: "[data-dashboard-capacity-chart]",
    asset: "dashboard-capacity",
    regionLabel: "Capacity distribution chart, scrolls horizontally",
    alt:
      "Histogram of the committed book: one bar per published capacity band " +
      "with the committed borrower count printed above it",
    width: 960,
    height: 580,
  },
  {
    selector: "[data-dashboard-mix-chart]",
    asset: "dashboard-mix",
    regionLabel: "Binding constraint mix chart, scrolls horizontally",
    alt:
      "Bar chart of the committed binding constraint counts for runway, DSCR, " +
      "and band cap, with the feasible and infeasible counts printed below",
    width: 960,
    height: 520,
  },
];

/** The provenance footer printed under every mounted chart. */
const CHART_PROVENANCE =
  "Counts computed by the pipeline and published in the committed payload; no figure is computed in the browser.";

/**
 * Mount one build time chart. When generation did not emit the asset
 * (the summary block is not published yet), the honest placeholder
 * already in dashboard.html stays on screen: no figure is invented.
 */
function renderChart(slot: ChartSlot): void {
  const host = mount(slot.selector);
  const url = chartAsset(slot.asset);
  if (url === null) {
    return;
  }
  host.textContent = "";

  const figure = element("figure", "chart-figure");
  const scroll = element("div", "chart-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", slot.regionLabel);

  const image = document.createElement("img");
  image.className = "chart-img";
  image.src = url;
  image.width = slot.width;
  image.height = slot.height;
  image.loading = "lazy";
  image.decoding = "async";
  image.alt = slot.alt;

  scroll.append(image);
  figure.append(scroll);
  figure.append(element("figcaption", "chart-note", CHART_PROVENANCE));
  host.append(figure);
}

renderAsofNote(mount("[data-asof]"), readAsofLink(book.rates.observation_date));
renderSummary();
renderBins();
for (const slot of CHARTS) {
  renderChart(slot);
}
