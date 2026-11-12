/**
 * Landing route: the five spec section 12 tasks with links, plus the
 * hero grounded in the committed payload.
 *
 * og:image points at public/og.svg for now: the placeholder SVG carries
 * the product name and subtitle, and the spec's og.png with screenshots
 * lands in phase 5, at which point the meta tags in every HTML entry swap
 * to the png. No other head change is needed for that swap.
 */
import "../styles/main.css";
import { mountShell } from "../components/page";
import { percent } from "../lib/format";

const { main, book } = mountShell();
const rates = book.rates;
const baseRateValue =
  typeof rates.base_rate.value === "number" ? String(rates.base_rate.value) : rates.base_rate.value;
const baseRateScale = typeof rates.base_rate.scale === "number" ? rates.base_rate.scale : 0;
const seedValue =
  typeof book.meta.seed.value === "number" ? String(book.meta.seed.value) : book.meta.seed.value;

// The snapshot date and the seed print verbatim; the base rate keeps its
// committed digits and gains only the percent suffix. Formatting here is
// arrangement, never arithmetic (see lib/format.ts).
const fields: Readonly<Record<string, string>> = {
  snapshot: rates.observation_date,
  base_rate: percent(baseRateValue, baseRateScale),
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
