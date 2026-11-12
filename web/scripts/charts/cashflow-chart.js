/**
 * Build time generator for web/src/charts/cashflow.svg.
 *
 * Reads the byte copied payloads in web/src/data and writes one static
 * SVG with two ruled panels: the cumulative lender receipts path for the
 * featured borrower's own committed structure, and the total receipts
 * published for each comparison structure as a bar series.
 *
 * The comparison payload publishes per period cash flow lists only for a
 * borrower's drawn structure, so the other structures appear as published
 * totals rather than paths. The SVG desc states exactly what is plotted
 * and where each figure comes from.
 *
 * Every scaling, cumulative sum, and label decision runs here in Node at
 * build time. The browser receives an inert image, so no arithmetic
 * ships in the route modules. Output is byte stable: fixed ordering,
 * fixed precision, no timestamps.
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
const outFile = join(outDir, "cashflow.svg");

/* Palette: the six named ink on ledger values from styles/main.css. */
const PAPER = "#f3f5f6";
const INK = "#16233b";
const RULE = "#c6cfd6";
const GREEN = "#0b6e4f";

const MONO = "Menlo, Consolas, monospace";
const DISPLAY = "Charter, Georgia, serif";

const WIDTH = 960;
const HEIGHT = 840;
const EDGE = 48;
const PLOT_LEFT = 140;
const PLOT_RIGHT = 912;

const A_TOP = 176;
const A_BASE = 446;
const B_TOP = 610;
const B_BASE = 760;

const MS_PER_DAY = 86400000;

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

/** ISO date to a day number, so the x scale is integer day arithmetic. */
function dayNumber(isoDate) {
  const ms = Date.parse(`${isoDate}T00:00:00Z`);
  if (Number.isNaN(ms)) {
    throw new Error(`cashflow-chart: not an ISO date: ${isoDate}`);
  }
  return Math.round(ms / MS_PER_DAY);
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

/** Axis top in whole dollars: the next step above value with headroom. */
function axisTop(valueDollars, headroom, targetTicks) {
  const step = niceStep(valueDollars * headroom, targetTicks);
  const top = Math.ceil((valueDollars * headroom) / step) * step;
  return { step, top };
}

function main() {
  const book = JSON.parse(readFileSync(join(dataDir, "book.json"), "utf8"));
  const comparison = JSON.parse(readFileSync(join(dataDir, "comparison.json"), "utf8"));

  const featured = comparison.meta.featured_borrower_id;
  const asof = comparison.meta.asof;

  const borrower = book.borrowers.find((entry) => entry.borrower_id === featured);
  if (borrower === undefined) {
    throw new Error(`cashflow-chart: book.json publishes no borrower ${featured}`);
  }
  const table = comparison.tables.find((entry) => entry.borrower_id === featured);
  if (table === undefined) {
    throw new Error(`cashflow-chart: comparison.json publishes no table for ${featured}`);
  }

  const block = borrower.returns;
  const flows = block.cash_flows;
  if (block.status.value !== "feasible" || !Array.isArray(flows) || flows.length === 0) {
    throw new Error(`cashflow-chart: ${featured} publishes no dated lender cash flow list`);
  }

  // Cumulative receipts: the positive entries of the published list. The
  // close outflow is not a receipt, so the path starts at zero at close.
  const closeDate = flows[0].date.value;
  const points = [{ day: dayNumber(closeDate), cents: 0 }];
  let cumulative = 0;
  let lastReceiptDate = closeDate;
  let closeCents = 0;
  for (const flow of flows) {
    const value = flow.cash_flow.value;
    if (value < 0 && closeCents === 0) {
      closeCents = value;
    }
    if (value > 0) {
      cumulative += value;
      lastReceiptDate = flow.date.value;
      points.push({ day: dayNumber(flow.date.value), cents: cumulative });
    }
  }
  if (points.length < 2) {
    throw new Error(`cashflow-chart: ${featured} publishes no positive receipt entries`);
  }

  const publishedTotal = block.total_receipts.value;
  const warrantCents = typeof block.warrant.value === "number" ? block.warrant.value : 0;
  const warrantCarried =
    warrantCents > 0 && cumulative - publishedTotal === warrantCents;

  // Panel B rows: published totals, in the order the payload publishes them.
  const rows = table.rows.map((row) => {
    const cell = row.total_receipts;
    if (typeof cell.value !== "number") {
      throw new Error(
        `cashflow-chart: ${featured} row ${row.structure} publishes no numeric total receipts`,
      );
    }
    return { structure: row.structure, cents: cell.value };
  });

  /* Scales. All geometry happens here in Node, never in the browser. */
  const scaleA = axisTop(cumulative / 100, 1.12, 8);
  const topACents = scaleA.top * 100;
  const day0 = points[0].day;
  const day1 = points[points.length - 1].day;
  if (day1 <= day0) {
    throw new Error(`cashflow-chart: ${featured} receipts span no time`);
  }

  const xOf = (day) =>
    PLOT_LEFT + ((day - day0) / (day1 - day0)) * (PLOT_RIGHT - PLOT_LEFT);
  const yAOf = (cents) => A_BASE - (cents / topACents) * (A_BASE - A_TOP);

  const maxRow = rows.reduce((best, row) => Math.max(best, row.cents), 0);
  if (maxRow <= 0) {
    throw new Error(`cashflow-chart: ${featured} comparison rows publish no positive receipts`);
  }
  const scaleB = axisTop(maxRow / 100, 1.12, 4);
  const topBDollars = scaleB.top;
  const yBOf = (cents) => B_BASE - (cents / 100 / topBDollars) * (B_BASE - B_TOP);

  const unitA = tickUnit(scaleA.top);
  const unitB = tickUnit(scaleB.top);
  const stepsA = Math.round(scaleA.top / scaleA.step);
  const stepsB = Math.round(scaleB.top / scaleB.step);

  // January the first of every calendar year strictly inside the range.
  const startYear = new Date((day0 + 1) * MS_PER_DAY).getUTCFullYear() + 1;
  const endYear = new Date(day1 * MS_PER_DAY).getUTCFullYear();
  const yearTicks = [];
  for (let year = startYear; year <= endYear; year += 1) {
    const x = xOf(dayNumber(`${year}-01-01`));
    if (x > PLOT_LEFT + 4 && x < PLOT_RIGHT - 4) {
      yearTicks.push({ year, x });
    }
  }

  const out = [];
  const push = (line) => out.push(line);

  push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img" aria-labelledby="cashflow-title cashflow-desc" font-family="${MONO}">`,
  );
  push(`<title id="cashflow-title">Cumulative lender receipts and structure totals</title>`);

  const descParts = [
    `Panel A plots the cumulative sum of the ${points.length - 1} positive entries in the published lender cash flow list for ${featured}, committed structure ${borrower.structure}, from close on ${closeDate} to the final receipt on ${lastReceiptDate}.`,
    `The path starts at zero at close and ends at ${dollars(cumulative)}.`,
    warrantCarried
      ? `The committed payload publishes total receipts of ${dollars(publishedTotal)} and a warrant value of ${dollars(warrantCents)} for this borrower, and the final cash flow entry carries both.`
      : `The committed payload publishes total receipts of ${dollars(publishedTotal)} for this borrower.`,
    closeCents < 0
      ? `The close outflow of ${dollars(closeCents)} is not a receipt, so it is not part of the path.`
      : `No close outflow is published for this borrower.`,
    `Panel B plots the total receipts published in comparison.json for each comparison structure: ${rows
      .map((row) => `${row.structure} ${dollars(row.cents)}`)
      .join(", ")}.`,
    `Per period cash flow lists are published only for a borrower's own drawn structure, so the other structures appear as published totals rather than paths.`,
    `Every value is read from the committed payload; scaling to pixels happens at build time and no figure is computed in the browser.`,
  ];
  push(`<desc id="cashflow-desc">${escapeXml(descParts.join(" "))}</desc>`);

  push(`<rect width="${WIDTH}" height="${HEIGHT}" fill="${PAPER}"/>`);
  push(`<rect width="${WIDTH}" height="4" fill="${INK}"/>`);
  push(
    `<rect x="0.5" y="0.5" width="${WIDTH - 1}" height="${HEIGHT - 1}" fill="none" stroke="${RULE}" stroke-width="1"/>`,
  );

  /* Header */
  push(
    `<text x="${EDGE}" y="44" font-size="13" letter-spacing="2.5" fill="${INK}">STRUCTURES, FEATURED BORROWER ${escapeXml(featured)}, ASOF ${escapeXml(asof)}</text>`,
  );
  push(
    `<text x="${EDGE}" y="88" font-family="${DISPLAY}" font-size="30" font-weight="700" fill="${INK}">Cumulative lender receipts and structure totals</text>`,
  );
  push(`<line x1="${EDGE}" y1="106" x2="${PLOT_RIGHT}" y2="106" stroke="${RULE}" stroke-width="1"/>`);
  push(
    `<text x="${EDGE}" y="130" font-size="14" fill="${INK}">Committed structure ${escapeXml(borrower.structure)}, receipts path from the published cash flow list</text>`,
  );

  /* Panel A: cumulative receipts path */
  push(
    `<text x="${EDGE}" y="158" font-size="13" letter-spacing="2" fill="${INK}">PANEL A, CUMULATIVE LENDER RECEIPTS, US DOLLARS</text>`,
  );
  push(`<g stroke="${RULE}" stroke-width="1" shape-rendering="crispEdges">`);
  for (let i = 1; i <= stepsA; i += 1) {
    const y = yAOf(i * scaleA.step * 100).toFixed(1);
    push(`<line x1="${PLOT_LEFT}" y1="${y}" x2="${PLOT_RIGHT}" y2="${y}"/>`);
  }
  for (const tick of yearTicks) {
    push(`<line x1="${tick.x.toFixed(1)}" y1="${A_TOP}" x2="${tick.x.toFixed(1)}" y2="${A_BASE}"/>`);
  }
  push(`</g>`);
  push(`<line x1="${PLOT_LEFT}" y1="${A_BASE}" x2="${PLOT_RIGHT}" y2="${A_BASE}" stroke="${INK}" stroke-width="1.5"/>`);
  push(`<line x1="${PLOT_LEFT}" y1="${A_TOP}" x2="${PLOT_LEFT}" y2="${A_BASE}" stroke="${RULE}" stroke-width="1"/>`);
  for (let i = 0; i <= stepsA; i += 1) {
    const value = i * scaleA.step;
    const y = (yAOf(value * 100) + 4).toFixed(1);
    push(
      `<text x="${PLOT_LEFT - 12}" y="${y}" text-anchor="end" font-size="13" fill="${INK}">${tickText(value, unitA)}</text>`,
    );
  }
  push(
    `<text x="36" y="${(A_TOP + A_BASE) / 2}" transform="rotate(-90 36 ${(A_TOP + A_BASE) / 2})" text-anchor="middle" font-size="13" letter-spacing="1.5" fill="${INK}">US dollars</text>`,
  );
  for (const tick of yearTicks) {
    push(
      `<text x="${tick.x.toFixed(1)}" y="${A_BASE + 22}" text-anchor="middle" font-size="13" fill="${INK}">${tick.year}</text>`,
    );
  }
  push(`<text x="${PLOT_LEFT}" y="${A_BASE + 42}" font-size="12" fill="${INK}">close ${escapeXml(closeDate)}</text>`);
  push(
    `<text x="${PLOT_RIGHT}" y="${A_BASE + 42}" text-anchor="end" font-size="12" fill="${INK}">final receipt ${escapeXml(lastReceiptDate)}</text>`,
  );

  const pathPoints = points
    .map((point) => `${xOf(point.day).toFixed(1)},${yAOf(point.cents).toFixed(1)}`)
    .join(" ");
  push(
    `<polyline points="${pathPoints}" fill="none" stroke="${GREEN}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`,
  );
  const endX = xOf(day1).toFixed(1);
  const endY = yAOf(cumulative);
  const markerX = Math.min(Number(endX) - 5, PLOT_RIGHT - 10);
  push(`<rect x="${markerX.toFixed(1)}" y="${(endY - 5).toFixed(1)}" width="10" height="10" fill="${GREEN}"/>`);
  const endLabel = `ends at ${dollars(cumulative)}`;
  const endLabelWidth = endLabel.length * 8.4 + 8;
  const endLabelY = endY - 16;
  push(
    `<rect x="${(PLOT_RIGHT - endLabelWidth).toFixed(1)}" y="${(endLabelY - 13).toFixed(1)}" width="${endLabelWidth.toFixed(1)}" height="18" fill="${PAPER}"/>`,
  );
  push(
    `<text x="${PLOT_RIGHT}" y="${endLabelY.toFixed(1)}" text-anchor="end" font-size="14" fill="${INK}">${escapeXml(endLabel)}</text>`,
  );

  /* Legend */
  push(`<line x1="${PLOT_LEFT}" y1="514" x2="${PLOT_LEFT + 36}" y2="514" stroke="${GREEN}" stroke-width="2.5"/>`);
  push(
    `<text x="${PLOT_LEFT + 46}" y="518" font-size="13" fill="${INK}">cumulative lender receipts, ${escapeXml(featured)} committed structure ${escapeXml(borrower.structure)}</text>`,
  );
  push(`<rect x="${PLOT_LEFT}" y="532" width="36" height="14" fill="${INK}"/>`);
  push(
    `<text x="${PLOT_LEFT + 46}" y="546" font-size="13" fill="${INK}">total receipts published for each comparison structure (bars below)</text>`,
  );
  push(`<line x1="${EDGE}" y1="566" x2="${PLOT_RIGHT}" y2="566" stroke="${RULE}" stroke-width="1"/>`);

  /* Panel B: published totals by comparison structure */
  push(
    `<text x="${EDGE}" y="592" font-size="13" letter-spacing="2" fill="${INK}">PANEL B, TOTAL RECEIPTS BY COMPARISON STRUCTURE, US DOLLARS</text>`,
  );
  push(`<g stroke="${RULE}" stroke-width="1" shape-rendering="crispEdges">`);
  for (let i = 1; i <= stepsB; i += 1) {
    const y = yBOf(i * scaleB.step * 100).toFixed(1);
    push(`<line x1="${PLOT_LEFT}" y1="${y}" x2="${PLOT_RIGHT}" y2="${y}"/>`);
  }
  push(`</g>`);
  push(`<line x1="${PLOT_LEFT}" y1="${B_BASE}" x2="${PLOT_RIGHT}" y2="${B_BASE}" stroke="${INK}" stroke-width="1.5"/>`);
  push(`<line x1="${PLOT_LEFT}" y1="${B_TOP}" x2="${PLOT_LEFT}" y2="${B_BASE}" stroke="${RULE}" stroke-width="1"/>`);
  for (let i = 0; i <= stepsB; i += 1) {
    const value = i * scaleB.step;
    const y = (yBOf(value * 100) + 4).toFixed(1);
    push(
      `<text x="${PLOT_LEFT - 12}" y="${y}" text-anchor="end" font-size="13" fill="${INK}">${tickText(value, unitB)}</text>`,
    );
  }
  push(
    `<text x="36" y="${(B_TOP + B_BASE) / 2}" transform="rotate(-90 36 ${(B_TOP + B_BASE) / 2})" text-anchor="middle" font-size="13" letter-spacing="1.5" fill="${INK}">US dollars</text>`,
  );

  const slotWidth = (PLOT_RIGHT - PLOT_LEFT) / rows.length;
  const barWidth = Math.min(170, slotWidth * 0.6);
  rows.forEach((row, index) => {
    const centre = PLOT_LEFT + slotWidth * (index + 0.5);
    const barX = centre - barWidth / 2;
    const barTop = yBOf(row.cents);
    const barHeight = B_BASE - barTop;
    push(
      `<rect x="${barX.toFixed(1)}" y="${barTop.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${barHeight.toFixed(1)}" fill="${INK}"/>`,
    );
    const valueLabel = dollars(row.cents);
    const valueLabelWidth = valueLabel.length * 8.4 + 8;
    const valueLabelY = barTop - 10;
    push(
      `<rect x="${(centre - valueLabelWidth / 2).toFixed(1)}" y="${(valueLabelY - 13).toFixed(1)}" width="${valueLabelWidth.toFixed(1)}" height="18" fill="${PAPER}"/>`,
    );
    push(
      `<text x="${centre.toFixed(1)}" y="${valueLabelY.toFixed(1)}" text-anchor="middle" font-size="14" fill="${INK}">${valueLabel}</text>`,
    );
    push(
      `<text x="${centre.toFixed(1)}" y="${B_BASE + 22}" text-anchor="middle" font-size="13" fill="${INK}">${escapeXml(row.structure)}</text>`,
    );
  });

  /* Footer */
  push(`<line x1="${EDGE}" y1="802" x2="${PLOT_RIGHT}" y2="802" stroke="${RULE}" stroke-width="1"/>`);
  push(
    `<text x="${EDGE}" y="824" font-size="12" fill="${INK}">source: committed book.json and comparison.json, generated at build time</text>`,
  );
  push(
    `<text x="${PLOT_RIGHT}" y="824" text-anchor="end" font-size="12" fill="${INK}">asof ${escapeXml(asof)}</text>`,
  );
  push(`</svg>`);

  const svg = `${out.join("\n")}\n`;
  mkdirSync(outDir, { recursive: true });
  writeFileSync(outFile, svg, "utf8");
  console.log(
    `cashflow-chart: wrote ${relative(repoRoot, outFile)} (${Buffer.byteLength(svg, "utf8")} bytes)`,
  );
}

main();
