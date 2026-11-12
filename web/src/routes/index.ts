/**
 * Landing route: the five spec section 12 tasks with one line answers
 * read from the committed payloads, plus the term sheet hero.
 *
 * The answers are assembled from committed values: dates, rates, and
 * figures are formatted, and counts are lengths of committed record
 * lists. Nothing is summed, ratioed, or derived.
 *
 * og:image points at public/og.svg for now: the placeholder SVG carries
 * the product name and subtitle, and the spec's og.png with screenshots
 * lands in phase 5, at which point the meta tags in every HTML entry swap
 * to the png. No other head change is needed for that swap.
 */
import "../styles/main.css";
import comparisonJson from "../data/comparison.json";
import { mountShell } from "../components/page";
import { STRUCTURE_LABELS } from "../content/rationale";
import { constraintLabel, dollars, envelopeText } from "../lib/cell";
import { isoDate, percent } from "../lib/format";
import { loadComparison, type Envelope } from "../lib/payload";
const { main, book } = mountShell();
const comparison = loadComparison(comparisonJson);
const rates = book.rates;

function baseRateText(env: Envelope): string {
  const value = typeof env.value === "number" ? String(env.value) : env.value;
  const scale = typeof env.scale === "number" ? env.scale : 0;
  return percent(value, scale);
}

// The snapshot date and the seed print verbatim; the base rate keeps its
// committed digits and gains only the percent suffix. Formatting here is
// arrangement, never arithmetic (see lib/format.ts).
const seedValue =
  typeof book.meta.seed.value === "number" ? String(book.meta.seed.value) : book.meta.seed.value;

const fields: Readonly<Record<string, string>> = {
  snapshot: rates.observation_date,
  base_rate: baseRateText(rates.base_rate),
  series: `${rates.label} (${rates.series})`,
  source: rates.source,
  seed: seedValue,
  currency: book.meta.currency,
};

for (const [name, text] of Object.entries(fields)) {
  const slot = main.querySelector(`[data-field="${name}"]`);
  if (slot === null) {
    throw new Error(`index route: missing hero field slot data-field="${name}"`);
  }
  slot.textContent = text;
}

// One line answers for the five tasks, all read from committed records.
const featuredId = comparison.meta.featured_borrower_id;
const featured = book.borrowers.find((entry) => entry.borrower_id === featuredId);
const featuredTable = comparison.tables.find((entry) => entry.borrower_id === featuredId);

function answerSlot(task: string): Element {
  const slot = main.querySelector(`[data-answer="${task}"]`);
  if (slot === null) {
    throw new Error(`index route: missing answer slot data-answer="${task}"`);
  }
  return slot;
}

function sizingAnswer(): string {
  if (featured === undefined) {
    return `No committed borrower ${featuredId}.`;
  }
  const sizing = featured.sizing;
  if (sizing.status.value !== "feasible") {
    return `${featuredId}: sizing infeasible, named constraint ${sizing.binding_constraint}.`;
  }
  const facility = dollars(String(sizing.facility_cents.value));
  return (
    `${featuredId}: facility ${facility}, binding constraint ` +
    `${constraintLabel(sizing.binding_constraint)} (${sizing.binding_constraint})`
  );
}

function structuresAnswer(): string {
  if (featuredTable === undefined) {
    return `No committed comparison table for ${featuredId}.`;
  }
  const labels = featuredTable.rows.map(
    (row) => STRUCTURE_LABELS[row.structure] ?? row.structure,
  );
  return (
    `${featuredTable.rows.length} structure rows on identical inputs for ${featuredId}: ` +
    labels.join(", ")
  );
}

function returnsAnswer(): string {
  if (featured === undefined) {
    return `No committed borrower ${featuredId}.`;
  }
  const block = featured.returns;
  if (!("lender_irr" in block)) {
    return `${featuredId}: no returns published, sizing infeasible so no facility exists to fund.`;
  }
  return (
    `${featuredId}, drawn structure ${featured.structure}: lender IRR ` +
    `${envelopeText(block.lender_irr)}, MOIC ${envelopeText(block.moic)}`
  );
}

function methodsAnswer(): string {
  return (
    `${isoDate(rates.observation_date)} snapshot, base rate ${baseRateText(rates.base_rate)}, ` +
    `source ${rates.source}`
  );
}

function verificationAnswer(): string {
  const sha = typeof book.meta.book_sha256.value === "string" ? book.meta.book_sha256.value : "";
  return (
    `${book.borrowers.length} committed borrower records, ` +
    `${comparison.tables.length} committed comparison tables, ` +
    `book SHA256 ${sha.slice(0, 12)}...`
  );
}

const answers: Readonly<Record<string, () => string>> = {
  sizing: sizingAnswer,
  structures: structuresAnswer,
  returns: returnsAnswer,
  methods: methodsAnswer,
  verification: verificationAnswer,
};

for (const [task, write] of Object.entries(answers)) {
  const slot = answerSlot(task);
  slot.textContent = write();
}

// The facility figure above is sizing output, so the section 2 rule 3
// stamp travels with it on this page too.
const sizingSlot = answerSlot("sizing");
const stamp = document.createElement("span");
stamp.className = "stamp stamp-inline";
stamp.textContent = "illustrative sizing, not credit approval";
sizingSlot.append(stamp);
