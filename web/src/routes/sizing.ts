/**
 * Sizing route: the facility answer for one committed borrower.
 *
 * The borrower selector swaps committed records client side; every figure
 * is read from book.json and formatted for display. An infeasible solve
 * shows its named constraint and never a facility figure, because no
 * facility exists to fund. Nothing is recomputed anywhere in this file.
 */
import "../styles/main.css";
import comparisonJson from "../data/comparison.json";
import { mountShell } from "../components/page";
import { mountSelector } from "../components/selector";
import { FACILITY_SIZING_BAND } from "../content/bands";
import { readAsofLink, renderAsofNote } from "../lib/asof";
import { constraintLabel, dollars, envelopeText, statusLabel } from "../lib/cell";
import { chartAsset } from "../lib/chart";
import {
  loadComparison,
  type Borrower,
  type Envelope,
  type Sizing,
} from "../lib/payload";

const { main, book } = mountShell();
const comparison = loadComparison(comparisonJson);

function mount(selector: string): Element {
  const node = main.querySelector(selector);
  if (node === null) {
    throw new Error(`sizing route: missing ${selector} mount point in sizing.html`);
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

/** Note line under a figure: band name plus citation when the envelope carries one. */
function noteFor(env: Envelope): string {
  if (typeof env.citation === "string") {
    return typeof env.band === "string" ? `${env.band}: ${env.citation}` : env.citation;
  }
  if (typeof env.provenance === "string") {
    return env.provenance;
  }
  return "";
}

/** One ruled key/value row, with an optional small mono note under value. */
function specRow(label: string, value: string, note?: string): HTMLElement {
  const row = element("div", "spec-row");
  row.append(element("dt", undefined, label));
  const detail = element("dd");
  detail.append(element("span", undefined, value));
  if (note !== undefined && note.length > 0) {
    detail.append(element("span", "spec-note", note));
  }
  row.append(detail);
  return row;
}

/**
 * The band cap row. The payload publishes the cap for every borrower,
 * feasible or not, so both answer branches print it; only an absent
 * envelope falls back to the honest "not published" note.
 */
function bandCapRow(sizing: Sizing): HTMLElement {
  const envelope = sizing.band_cap;
  if (envelope === undefined) {
    return specRow(
      "Sizing band cap",
      "not published in the payload",
      `${FACILITY_SIZING_BAND.band}: ${FACILITY_SIZING_BAND.rule}. ${FACILITY_SIZING_BAND.citation}`,
    );
  }
  const atCap = sizing.binding_constraint === "band_cap";
  const lead = atCap ? "The facility sits at the band cap, so both figures match. " : "";
  const band = typeof envelope.band === "string" ? envelope.band : FACILITY_SIZING_BAND.band;
  const citation =
    typeof envelope.citation === "string" ? envelope.citation : FACILITY_SIZING_BAND.citation;
  return specRow(
    "Sizing band cap",
    envelopeText(envelope),
    `${lead}${FACILITY_SIZING_BAND.rule}. ${band}: ${citation}`,
  );
}

/** The sizing answer: figures when feasible, the named constraint when not. */
function renderAnswer(borrower: Borrower): void {
  const host = mount("[data-sizing-answer]");
  host.textContent = "";
  const sizing = borrower.sizing;
  const feasible = sizing.status.value === "feasible";

  const head = element("div", "answer-head");
  head.append(
    element(
      "p",
      "answer-id",
      `${borrower.borrower_id}, committed structure ${borrower.structure}, lender type ${borrower.lender_type}`,
    ),
    element("span", feasible ? "chip chip-pass" : "chip chip-flag", statusLabel(sizing.status.value)),
  );
  host.append(head);

  host.append(element("p", "stamp", "illustrative sizing, not credit approval"));

  if (!feasible) {
    const notice = element("div", "notice");
    notice.append(
      element(
        "p",
        "notice-head",
        `Named constraint: ${constraintLabel(sizing.binding_constraint)} (${sizing.binding_constraint})`,
      ),
    );
    notice.append(
      element(
        "p",
        undefined,
        "No facility amount is published for this borrower, because sizing is infeasible. " +
          "Runway and DSCR figures are withheld with it: no facility exists to fund. " +
          "The band cap below is still published, because the cap belongs to the drawn " +
          "last round rather than to feasibility.",
      ),
    );
    if (typeof sizing.reason === "string") {
      notice.append(element("p", undefined, sizing.reason));
    }
    host.append(notice);
    const capOnly = element("dl", "spec-grid");
    capOnly.append(bandCapRow(sizing));
    host.append(capOnly);
    return;
  }

  const figure = element("div", "answer-figure");
  figure.append(
    element("p", "figure-label", "Facility amount"),
    element("p", "big-figure", dollars(String(sizing.facility_cents.value))),
    element(
      "p",
      "figure-note",
      `Binding constraint: ${constraintLabel(sizing.binding_constraint)} (${sizing.binding_constraint})`,
    ),
  );
  host.append(figure);

  const rows = element("dl", "spec-grid");
  rows.append(
    specRow(
      "Post-draw runway (months)",
      envelopeText(sizing.post_draw_runway_months),
      noteFor(sizing.post_draw_runway_months),
    ),
    specRow("Minimum DSCR (x)", envelopeText(sizing.min_dscr), noteFor(sizing.min_dscr)),
    bandCapRow(sizing),
  );
  host.append(rows);
}

/** The configured inputs, printed as labeled figures with their citations. */
function renderInputs(borrower: Borrower): void {
  const host = mount("[data-sizing-inputs]");
  host.textContent = "";

  const wanted: ReadonlyArray<{ readonly key: string; readonly label: string }> = [
    { key: "last_round", label: "Last round" },
    { key: "arr", label: "ARR" },
    { key: "net_burn", label: "Monthly net burn" },
    { key: "cash", label: "Cash on hand" },
  ];

  const rows = element("dl", "spec-grid");
  for (const field of wanted) {
    const env = borrower.inputs[field.key];
    if (env === undefined) {
      throw new Error(`sizing route: borrower ${borrower.borrower_id} has no input ${field.key}`);
    }
    rows.append(specRow(field.label, envelopeText(env), noteFor(env)));
  }
  host.append(rows);
  host.append(
    element(
      "p",
      "section-copy",
      "These are configured inputs as committed. The site formats them and never " +
        "derives a facility, a runway, or a cap from them in the browser.",
    ),
  );
}

/**
 * Mount the build time capacity chart for the featured borrower. When
 * generation did not emit the asset the honest placeholder already in
 * sizing.html stays on screen: no figure is invented in its place.
 */
function renderCapacityChart(): void {
  const host = mount("[data-capacity-chart]");
  const url = chartAsset("capacity");
  if (url === null) {
    return;
  }
  host.textContent = "";

  const figure = element("figure", "chart-figure");
  const scroll = element("div", "chart-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", "Facility against the band cap chart, scrolls horizontally");

  const image = document.createElement("img");
  image.className = "chart-img";
  image.src = url;
  image.width = 960;
  image.height = 580;
  image.loading = "lazy";
  image.decoding = "async";
  image.alt =
    "Bar chart comparing the facility amount with the sizing band cap for the " +
    "featured borrower, both values printed in dollars";

  scroll.append(image);
  figure.append(scroll);
  figure.append(
    element(
      "figcaption",
      "chart-note",
      `Generated at build time from the committed payload for the featured borrower ${featured}. ` +
        "The bars are a static asset: no figure is computed in the browser.",
    ),
  );
  host.append(figure);
}

function show(borrowerId: string): void {
  const borrower = book.borrowers.find((entry) => entry.borrower_id === borrowerId);
  if (borrower === undefined) {
    throw new Error(`sizing route: no committed borrower ${borrowerId}`);
  }
  const answerTitle = document.getElementById("answer-title");
  const inputsTitle = document.getElementById("inputs-title");
  if (answerTitle !== null) {
    answerTitle.textContent = `Sizing answer for ${borrower.borrower_id}`;
  }
  if (inputsTitle !== null) {
    inputsTitle.textContent = `Configured inputs for ${borrower.borrower_id}`;
  }
  renderAnswer(borrower);
  renderInputs(borrower);
}

const options = book.borrowers.map((entry) => ({
  value: entry.borrower_id,
  label:
    entry.sizing.status.value === "feasible"
      ? entry.borrower_id
      : `${entry.borrower_id} (infeasible sizing)`,
}));
const featured = comparison.meta.featured_borrower_id;
const defaultId = options.some((option) => option.value === featured) ? featured : options[0].value;

mountSelector(mount("[data-selector]"), {
  selectId: "sizing-borrower",
  label: "Borrower",
  options,
  defaultValue: defaultId,
  onChange: show,
});

renderAsofNote(mount("[data-asof]"), readAsofLink(book.rates.observation_date));
show(defaultId);
renderCapacityChart();
