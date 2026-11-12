/**
 * Build time generator for web/src/charts/capacity.svg.
 *
 * Reads the byte copied payloads in web/src/data and writes one static
 * SVG: two bars for the featured borrower, the published facility amount
 * against the published sizing band cap, each value printed as text. An
 * infeasible solve publishes no facility, so the facility slot stays
 * empty and says so instead of drawing a bar that does not exist.
 *
 * Every scaling and label decision runs here in Node at build time. The
 * browser receives an inert image, so no arithmetic ships in the route
 * modules. Output is byte stable: fixed ordering, fixed precision, no
 * timestamps.
 *
 * The .js extension is deliberate: the pinned dash scan has no .mjs
 * class, and .js sits in the source class of the exemption table.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(scriptDir, "..", "..");
const repoRoot = resolve(webRoot, "..");
const dataDir = join(webRoot, "src", "data");
const outDir = join(webRoot, "src", "charts");
const outFile = join(outDir, "capacity.svg");

/* Palette: the six named ink on ledger values from styles/main.css. */
const PAPER = "#f3f5f6";
const INK = "#16233b";
const RULE = "#c6cfd6";
const WASH = "#e7ecef";
const GREEN = "#0b6e4f";
const BREACH = "#9e2b25";

const MONO = "Menlo, Consolas, monospace";
const DISPLAY = "Charter, Georgia, serif";

const WIDTH = 960;
const HEIGHT = 580;
const EDGE = 48;
const PLOT_LEFT = 140;
const PLOT_RIGHT = 912;
const PLOT_TOP = 160;
const PLOT_BASE = 440;

/** XML text escaping, applied to every string that reaches the file. */
function escapeXml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Integer cents as display dollars: point placement, digits unchanged. */
function dollars(cents) {
  const negative = cents < 0;
  const digits = String(Math.abs(cents));
  const tail = digits.slice(-2).padStart(2, "0");
  const head = digits.slice(0, -2);
  const whole = (head.length > 0 ? head : "0").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}$${whole}.${tail}`;
}

/** One axis tick label, unit chosen once from the top of the axis. */
function tickUnit(topDollars) {
  if (topDollars >= 1000000) return { unit: 1000000, suffix: "M" };
  if (topDollars >= 1000) return { unit: 1000, suffix: "K" };
  return { unit: 1, suffix: "" };
}

function tickText(valueDollars, info) {
  const scaled = Number((valueDollars / info.unit).toFixed(2));
  if (scaled === 0) {
    return "$0";
  }
  const sign = scaled < 0 ? "-" : "";
  return `${sign}$${Math.abs(scaled)}${info.suffix}`;
}

/** Smallest 1, 2, 2.5, 5, 10 step that keeps the axis around target ticks. */
function niceStep(range, targetTicks) {
  const raw = range / targetTicks;
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
  const normalised = raw / magnitude;
  let factor = 10;
  if (normalised <= 1) factor = 1;
  else if (normalised <= 2) factor = 2;
  else if (normalised <= 2.5) factor = 2.5;
  else if (normalised <= 5) factor = 5;
  return factor * magnitude;
}

function main() {
  const book = JSON.parse(readFileSync(join(dataDir, "book.json"), "utf8"));
  const comparison = JSON.parse(readFileSync(join(dataDir, "comparison.json"), "utf8"));

  const featured = comparison.meta.featured_borrower_id;
  const asof = comparison.meta.asof;

  const borrower = book.borrowers.find((entry) => entry.borrower_id === featured);
  if (borrower === undefined) {
    throw new Error(`capacity-chart: book.json publishes no borrower ${featured}`);
  }

  const sizing = borrower.sizing;
  const feasible = sizing.status.value === "feasible";
  const facilityCents = feasible && typeof sizing.facility_cents.value === "number"
    ? sizing.facility_cents.value
    : null;
  const capEnvelope = sizing.band_cap;
  if (capEnvelope === undefined || typeof capEnvelope.value !== "number") {
    throw new Error(`capacity-chart: ${featured} publishes no sizing band cap`);
  }
  const capCents = capEnvelope.value;
  const binding = sizing.binding_constraint;
  const band = typeof capEnvelope.band === "string" ? capEnvelope.band : "facility_sizing band";
  const citation = typeof capEnvelope.citation === "string"
    ? capEnvelope.citation
    : "citation not published";

  /* Scale: the larger of the two published figures sets the axis top. */
  const topDollars = Math.max(capCents, facilityCents ?? 0) / 100;
  if (topDollars <= 0) {
    throw new Error(`capacity-chart: ${featured} publishes no positive sizing figure to plot`);
  }
  const step = niceStep(topDollars, 5);
  const axisMax = Math.ceil(topDollars / step) * step;
  const unit = tickUnit(axisMax);
  const steps = Math.round(axisMax / step);
  const yOf = (cents) => PLOT_BASE - (cents / 100 / axisMax) * (PLOT_BASE - PLOT_TOP);

  const bars = [
    {
      key: "facility",
      label: "Facility amount",
      sublabel: `binding constraint: ${binding}`,
      cents: facilityCents,
    },
    {
      key: "band_cap",
      label: "Sizing band cap",
      sublabel: `band: ${band}`,
      cents: capCents,
    },
  ];

  const out = [];
  const push = (line) => out.push(line);

  push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img" aria-labelledby="capacity-title capacity-desc" font-family="${MONO}">`,
  );
  push(`<title id="capacity-title">Facility against the sizing band cap</title>`);

  const descParts = [
    `Two bars in US dollars for the featured borrower ${featured}, asof ${asof}.`,
    feasible
      ? `Facility amount ${dollars(facilityCents)} with binding constraint ${binding}, and sizing band cap ${dollars(capCents)}. Both values are verbatim from the committed book.json sizing block; only the unit changes from cents to dollars for display.`
      : `The sizing solve for this borrower is infeasible, so no facility amount is published and the facility slot stays empty. The sizing band cap ${dollars(capCents)} is published regardless and is verbatim from the committed book.json sizing block.`,
    `The band cap carries band ${band} and citation ${citation}.`,
    `No figure is computed in the browser.`,
  ];
  push(`<desc id="capacity-desc">${escapeXml(descParts.join(" "))}</desc>`);

  push(`<rect width="${WIDTH}" height="${HEIGHT}" fill="${PAPER}"/>`);
  push(`<rect width="${WIDTH}" height="4" fill="${INK}"/>`);
  push(
    `<rect x="0.5" y="0.5" width="${WIDTH - 1}" height="${HEIGHT - 1}" fill="none" stroke="${RULE}" stroke-width="1"/>`,
  );

  /* Header */
  push(
    `<text x="${EDGE}" y="44" font-size="13" letter-spacing="2.5" fill="${INK}">SIZING, FEATURED BORROWER ${escapeXml(featured)}, ASOF ${escapeXml(asof)}</text>`,
  );
  push(
    `<text x="${EDGE}" y="88" font-family="${DISPLAY}" font-size="30" font-weight="700" fill="${INK}">Facility against the sizing band cap</text>`,
  );
  push(`<line x1="${EDGE}" y1="106" x2="${PLOT_RIGHT}" y2="106" stroke="${RULE}" stroke-width="1"/>`);
  push(
    `<text x="${EDGE}" y="130" font-size="14" fill="${INK}">Two committed figures in US dollars, read verbatim from book.json</text>`,
  );

  /* Grid */
  push(`<g stroke="${RULE}" stroke-width="1" shape-rendering="crispEdges">`);
  for (let i = 1; i <= steps; i += 1) {
    const y = (PLOT_BASE - (i * step) / axisMax * (PLOT_BASE - PLOT_TOP)).toFixed(1);
    push(`<line x1="${PLOT_LEFT}" y1="${y}" x2="${PLOT_RIGHT}" y2="${y}"/>`);
  }
  push(`</g>`);
  push(`<line x1="${PLOT_LEFT}" y1="${PLOT_BASE}" x2="${PLOT_RIGHT}" y2="${PLOT_BASE}" stroke="${INK}" stroke-width="1.5"/>`);
  push(`<line x1="${PLOT_LEFT}" y1="${PLOT_TOP}" x2="${PLOT_LEFT}" y2="${PLOT_BASE}" stroke="${RULE}" stroke-width="1"/>`);
  for (let i = 0; i <= steps; i += 1) {
    const value = i * step;
    const y = (PLOT_BASE - value / axisMax * (PLOT_BASE - PLOT_TOP) + 4).toFixed(1);
    push(
      `<text x="${PLOT_LEFT - 12}" y="${y}" text-anchor="end" font-size="13" fill="${INK}">${tickText(value, unit)}</text>`,
    );
  }
  push(
    `<text x="36" y="${(PLOT_TOP + PLOT_BASE) / 2}" transform="rotate(-90 36 ${(PLOT_TOP + PLOT_BASE) / 2})" text-anchor="middle" font-size="13" letter-spacing="1.5" fill="${INK}">US dollars</text>`,
  );

  /* Bars */
  const slotWidth = (PLOT_RIGHT - PLOT_LEFT) / bars.length;
  const barWidth = Math.min(170, slotWidth * 0.6);
  bars.forEach((bar, index) => {
    const centre = PLOT_LEFT + slotWidth * (index + 0.5);
    const barX = centre - barWidth / 2;
    if (bar.cents === null) {
      // No facility exists to fund, so no bar is drawn: an empty ruled slot.
      push(
        `<rect x="${barX.toFixed(1)}" y="${PLOT_TOP}" width="${barWidth.toFixed(1)}" height="${PLOT_BASE - PLOT_TOP}" fill="${WASH}" stroke="${INK}" stroke-width="1"/>`,
      );
      push(
        `<text x="${centre.toFixed(1)}" y="${(PLOT_TOP + (PLOT_BASE - PLOT_TOP) / 2 - 6).toFixed(1)}" text-anchor="middle" font-size="13" fill="${BREACH}">infeasible:</text>`,
      );
      push(
        `<text x="${centre.toFixed(1)}" y="${(PLOT_TOP + (PLOT_BASE - PLOT_TOP) / 2 + 16).toFixed(1)}" text-anchor="middle" font-size="13" fill="${BREACH}">no facility figure</text>`,
      );
    } else {
      const barTop = yOf(bar.cents);
      const fill = bar.key === "facility" ? GREEN : INK;
      push(
        `<rect x="${barX.toFixed(1)}" y="${barTop.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${(PLOT_BASE - barTop).toFixed(1)}" fill="${fill}"/>`,
      );
      push(
        `<text x="${centre.toFixed(1)}" y="${(barTop + 30).toFixed(1)}" text-anchor="middle" font-size="17" fill="${PAPER}">${dollars(bar.cents)}</text>`,
      );
    }
    push(
      `<text x="${centre.toFixed(1)}" y="${PLOT_BASE + 28}" text-anchor="middle" font-size="15" fill="${INK}">${escapeXml(bar.label)}</text>`,
    );
    push(
      `<text x="${centre.toFixed(1)}" y="${PLOT_BASE + 50}" text-anchor="middle" font-size="12" fill="${INK}">${escapeXml(bar.sublabel)}</text>`,
    );
  });

  /* Footer: band citation beside the band value, plus provenance */
  push(`<line x1="${EDGE}" y1="510" x2="${PLOT_RIGHT}" y2="510" stroke="${RULE}" stroke-width="1"/>`);
  push(
    `<text x="${EDGE}" y="534" font-size="13" fill="${INK}">citation: ${escapeXml(citation)}</text>`,
  );
  push(
    `<text x="${PLOT_RIGHT}" y="534" text-anchor="end" font-size="13" fill="${INK}">asof ${escapeXml(asof)}</text>`,
  );
  push(
    `<text x="${EDGE}" y="556" font-size="13" fill="${INK}">source: committed book.json sizing block, generated at build time</text>`,
  );
  push(`</svg>`);

  const svg = `${out.join("\n")}\n`;
  mkdirSync(outDir, { recursive: true });
  writeFileSync(outFile, svg, "utf8");
  console.log(
    `capacity-chart: wrote ${relative(repoRoot, outFile)} (${Buffer.byteLength(svg, "utf8")} bytes)`,
  );
}

main();
