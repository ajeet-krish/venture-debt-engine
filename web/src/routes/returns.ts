/**
 * Returns route: base versus downside side by side for one committed
 * borrower, plus the rate shock column and the covenant summary strip
 * (architecture P4.5).
 *
 * Every figure prints from the borrower's scenarios and covenants blocks
 * in book.json through lib/format.ts: digit grouping, point placement,
 * and unit suffixes only. The delta column renders the published delta
 * envelope together with the label the pipeline published beside it, so
 * a zero delta reads "invariant by construction" from the payload and
 * the browser never subtracts. When a block is absent from the committed
 * payload, the honest placeholder already in returns.html stays on
 * screen. No arithmetic runs anywhere in this file.
 */
import "../styles/main.css";
import { mountShell } from "../components/page";
import { mountSelector } from "../components/selector";
import { readAsofLink, renderAsofNote } from "../lib/asof";
import { breachChip, breachView, envelopeText, statusLabel } from "../lib/cell";
import {
  isNotApplicableShape,
  isStatusCell,
  type Borrower,
  type CovenantsBlock,
  type Envelope,
  type RateShockBlock,
  type ScenarioDeltas,
  type ScenarioFigures,
  type ScenarioNotApplicable,
  type ScenariosBlock,
} from "../lib/payload";

const { main, book } = mountShell();

/** Featured borrower for this page (spec section 12 default). */
const DEFAULT_BORROWER = "SYN-0005";

/** Stated assumption label carried by both warrant rows (spec section 5). */
const WARRANT_STATED_ASSUMPTION =
  "stated assumption: warrant option-value model, not a market quote";

/** Headline figure keys this page renders, in publication vocabulary. */
type HeadlineKey =
  | "lender_irr"
  | "moic"
  | "total_interest"
  | "total_receipts"
  | "closing_fee"
  | "warrant";

/** One metric row: base, downside, and the matching published delta. */
interface ScenarioRow {
  readonly key: HeadlineKey;
  readonly label: string;
  /** Published delta key, or null when the payload publishes none. */
  readonly deltaKey: keyof ScenarioDeltas | null;
  readonly warrant: boolean;
}

const ROWS: readonly ScenarioRow[] = [
  { key: "lender_irr", label: "Lender IRR", deltaKey: "lender_irr_delta", warrant: false },
  { key: "moic", label: "MOIC", deltaKey: "moic_delta", warrant: false },
  { key: "total_interest", label: "Total interest", deltaKey: "total_interest_delta", warrant: false },
  { key: "total_receipts", label: "Total receipts", deltaKey: "total_receipts_delta", warrant: false },
  { key: "closing_fee", label: "Closing fee", deltaKey: null, warrant: false },
  { key: "warrant", label: "Warrant value", deltaKey: "warrant_delta", warrant: true },
];

/** Rate shock rows in the pinned P4.3 publication order. */
const SHOCK_ROWS: ReadonlyArray<{ readonly label: string; readonly key: keyof RateShockBlock }> = [
  { label: "Shocked lender IRR", key: "shocked_lender_irr" },
  { label: "Shocked total interest", key: "shocked_total_interest" },
  { label: "IRR delta", key: "irr_delta_bp" },
  { label: "Sensitivity per 100 bp", key: "sensitivity_per_100bp" },
];

function mount(selector: string): Element {
  const node = main.querySelector(selector);
  if (node === null) {
    throw new Error(`returns route: missing ${selector} mount point in returns.html`);
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

/** Published provenance or citation of an envelope, or the empty string. */
function provenanceOf(env: Envelope): string {
  if (typeof env.provenance === "string") {
    return env.provenance;
  }
  if (typeof env.citation === "string") {
    return env.citation;
  }
  return "";
}

/** One figure cell: the envelope's digits plus optional small notes. */
function figureCell(env: Envelope, notes: readonly string[]): HTMLTableCellElement {
  const td = element("td", "num") as HTMLTableCellElement;
  td.append(element("span", "cell-value", envelopeText(env)));
  for (const note of notes) {
    if (note.length > 0) {
      td.append(element("span", "cell-note", note));
    }
  }
  return td;
}

/**
 * The delta cell: the published delta envelope plus the label the
 * pipeline published beside it. A delta that publishes a status instead
 * of a figure shows that status and its reason. Nothing is subtracted
 * here; when the payload publishes no delta for a row, the cell says so.
 */
function deltaCell(deltas: ScenarioDeltas, key: keyof ScenarioDeltas | null): HTMLTableCellElement {
  const td = element("td", "num") as HTMLTableCellElement;
  if (key === null) {
    td.append(element("span", "cell-note", "no published delta for this row"));
    return td;
  }
  const leaf = deltas[key];
  if (isStatusCell(leaf)) {
    td.append(
      element("span", "chip chip-pending", statusLabel(leaf.status.value)),
      element("span", "cell-note", leaf.reason),
    );
    return td;
  }
  td.append(
    element("span", "cell-value", envelopeText(leaf)),
    element("span", "cell-note", leaf.status),
  );
  return td;
}

/** The base versus downside table for one borrower's scenarios block. */
function scenarioTable(
  borrower: Borrower,
  deltas: ScenarioDeltas,
  base: ScenarioFigures,
  downside: ScenarioFigures,
): HTMLElement {
  const scroll = element("div", "table-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute(
    "aria-label",
    `Base versus downside table for ${borrower.borrower_id}, scrolls horizontally`,
  );

  const table = element("table", "ledger-table");
  table.append(
    element(
      "caption",
      undefined,
      `Base versus downside, ${borrower.borrower_id}, drawn structure ${borrower.structure}`,
    ),
  );

  const headRow = element("tr");
  const headers: readonly string[] = [
    "Metric",
    "Base",
    "Downside",
    "Published delta (downside minus base)",
  ];
  headers.forEach((header, index) => {
    const th = element("th", index === 0 ? undefined : "num", header);
    th.setAttribute("scope", "col");
    headRow.append(th);
  });
  const thead = element("thead");
  thead.append(headRow);
  table.append(thead);

  const tbody = element("tbody");
  for (const row of ROWS) {
    const tr = element("tr");
    const th = element("th", "row-head", row.label) as HTMLTableCellElement;
    th.setAttribute("scope", "row");
    tr.append(th);

    const baseNotes = row.warrant ? [WARRANT_STATED_ASSUMPTION] : [];
    const downsideNotes = row.warrant
      ? [WARRANT_STATED_ASSUMPTION, provenanceOf(downside.warrant)]
      : [];
    tr.append(
      figureCell(base[row.key], baseNotes),
      figureCell(downside[row.key], downsideNotes),
      deltaCell(deltas, row.deltaKey),
    );
    tbody.append(tr);
  }
  table.append(tbody);
  scroll.append(table);
  return scroll;
}

/** An infeasible borrower's scenario block: status chip plus its reason. */
function renderBlocked(
  host: Element,
  borrower: Borrower,
  blocked: ScenarioNotApplicable,
): void {
  const notice = element("div", "notice");
  notice.setAttribute("role", "note");
  const head = element("p", "notice-head");
  head.append(element("span", "chip chip-pending", statusLabel(blocked.status.value)));
  notice.append(
    head,
    element(
      "p",
      undefined,
      `No scenario figures are published for ${borrower.borrower_id}: ${blocked.reason}`,
    ),
  );
  host.append(notice);
}

/** Render the base versus downside block, or its honest placeholder. */
function renderScenarios(borrower: Borrower): void {
  const host = mount("[data-returns-table]");
  host.textContent = "";
  const scenarios: ScenariosBlock | undefined = borrower.scenarios;

  if (scenarios === undefined) {
    placeholder(
      host,
      "Scenario figures not published yet",
      `The committed book has not published a scenarios block for ${borrower.borrower_id}, ` +
        "so this page prints no base figure, no downside figure, and no delta. The block " +
        "lands with the phase 4 payload build; every figure here will print from that " +
        "committed block, never from a browser calculation.",
    );
    return;
  }

  if (isNotApplicableShape(scenarios)) {
    renderBlocked(host, borrower, scenarios);
    return;
  }

  if (isNotApplicableShape(scenarios.base)) {
    renderBlocked(host, borrower, scenarios.base);
    return;
  }
  if (isNotApplicableShape(scenarios.downside)) {
    renderBlocked(host, borrower, scenarios.downside);
    return;
  }

  host.append(
    scenarioTable(borrower, scenarios.deltas, scenarios.base, scenarios.downside),
    element(
      "p",
      "disclosure",
      "Every figure prints from the committed payload. The delta column prints the " +
        "pipeline's published delta envelope with its published label; the browser " +
        "never subtracts, and a row without a published delta says so.",
    ),
  );
}

/** Render the rate shock block, or its honest placeholder. */
function renderRateShock(borrower: Borrower): void {
  const host = mount("[data-rate-shock]");
  host.textContent = "";
  const scenarios: ScenariosBlock | undefined = borrower.scenarios;

  if (scenarios === undefined) {
    placeholder(
      host,
      "Rate shock figures not published yet",
      `The committed book has not published the rate shock block for ${borrower.borrower_id}, ` +
        "so this section prints no shocked IRR, no shocked interest, and no bp figure. " +
        "The block lands with the phase 4 payload build.",
    );
    return;
  }

  if (isNotApplicableShape(scenarios)) {
    renderBlocked(host, borrower, scenarios);
    return;
  }

  const shock = scenarios.rate_shock;
  const scroll = element("div", "table-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute(
    "aria-label",
    `Rate shock table for ${borrower.borrower_id}, scrolls horizontally`,
  );

  const table = element("table", "ledger-table");
  table.append(
    element(
      "caption",
      undefined,
      `Rate shock, ${borrower.borrower_id}, drawn structure ${borrower.structure}`,
    ),
  );

  const headRow = element("tr");
  for (const header of ["Rate shock figure", "Published value"]) {
    const th = element("th", header === "Rate shock figure" ? undefined : "num", header);
    th.setAttribute("scope", "col");
    headRow.append(th);
  }
  const thead = element("thead");
  thead.append(headRow);
  table.append(thead);

  const tbody = element("tbody");
  for (const row of SHOCK_ROWS) {
    const tr = element("tr");
    const th = element("th", "row-head", row.label) as HTMLTableCellElement;
    th.setAttribute("scope", "row");
    tr.append(th);

    const cell = shock[row.key];
    const td = element("td", "num") as HTMLTableCellElement;
    if (isStatusCell(cell)) {
      td.append(
        element("span", "chip chip-pending", statusLabel(cell.status.value)),
        element("span", "cell-note", cell.reason),
      );
    } else {
      td.append(element("span", "cell-value", envelopeText(cell)));
    }
    tr.append(td);
    tbody.append(tr);
  }
  table.append(tbody);
  scroll.append(table);

  host.append(
    scroll,
    element(
      "p",
      "disclosure",
      "The two delta figures print with unit bp from their published envelopes; a " +
        "shock that publishes a status instead of a figure shows that status and its " +
        "stated reason.",
    ),
  );
}

/** Render the covenant summary strip, or its honest placeholder. */
function renderCovenants(borrower: Borrower): void {
  const host = mount("[data-covenant-strip]");
  host.textContent = "";
  const covenants: CovenantsBlock | undefined = borrower.covenants;

  if (covenants === undefined) {
    placeholder(
      host,
      "Covenant summary not published yet",
      `The committed book has not published a covenants block for ${borrower.borrower_id}, ` +
        "so this strip prints no first breach month. The block lands with the phase 4 " +
        "payload build; the status chip then prints the published status verbatim.",
    );
    return;
  }

  const chip = breachChip(breachView(covenants.first_breach_month, covenants.reason));
  const wrap = element("div");
  wrap.append(
    element("p", "figure-label", "First breach, borrower level, under downside"),
  );

  const row = element("div", "chip-row");
  row.append(element("span", chip.className, chip.text));
  wrap.append(row);

  if (chip.reason !== undefined) {
    wrap.append(element("p", "cell-note", chip.reason));
  }

  wrap.append(
    element(
      "p",
      "disclosure",
      "Published by the pipeline: the minimum first breach month across the " +
        "applicable covenants under the pinned downside scenario. No period test " +
        "runs in the browser.",
    ),
  );
  host.append(wrap);
}

function show(borrowerId: string): void {
  const borrower = book.borrowers.find((entry) => entry.borrower_id === borrowerId);
  if (borrower === undefined) {
    throw new Error(`returns route: no committed borrower ${borrowerId}`);
  }
  renderScenarios(borrower);
  renderRateShock(borrower);
  renderCovenants(borrower);
}

const options = book.borrowers.map((entry) => ({
  value: entry.borrower_id,
  label:
    entry.sizing.status.value === "feasible"
      ? entry.borrower_id
      : `${entry.borrower_id} (infeasible sizing)`,
}));
const defaultId = options.some((option) => option.value === DEFAULT_BORROWER)
  ? DEFAULT_BORROWER
  : options[0].value;

mountSelector(mount("[data-selector]"), {
  selectId: "returns-borrower",
  label: "Borrower",
  options,
  defaultValue: defaultId,
  onChange: show,
});

renderAsofNote(mount("[data-asof]"), readAsofLink(book.rates.observation_date));
show(defaultId);
