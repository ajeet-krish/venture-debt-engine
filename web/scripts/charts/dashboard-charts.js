/**
 * Build time generator for web/src/charts/dashboard-capacity.svg and
 * web/src/charts/dashboard-mix.svg (architecture P4.6).
 *
 * Reads the byte copied book.json in web/src/data and draws geometry
 * ONLY from the committed meta.book_summary counts: histogram bar heights
 * come from the published bin counts, mix bar heights from the published
 * binding constraint counts. This script never counts and never bins.
 * Counting and binning are engine arithmetic that lives in the pipeline,
 * where the phase 5 verify gate recomputes them naively; a second
 * counting script here would be an arithmetic engine outside that gate.
 * Scaling a bar height from a published count is build time geometry,
 * which is why it is allowed in this file and nowhere in the browser.
 *
 * When meta.book_summary is absent (the block lands with the phase 4
 * payload build), the script prints an honest notice and writes nothing:
 * no zero is invented and no bar is drawn from thin air. Each block is
 * read independently, so a present capacity block still renders when the
 * mix block is missing, and the reverse.
 *
 * Count reads accept either a bare integer or an envelope carrying an
 * integer value, so either published shape works without a rewrite.
 *
 * Output is byte stable: fixed ordering, fixed precision, no timestamps.
 * Run it twice and the bytes match.
 *
 * The .js extension is deliberate: the pinned dash scan has no .mjs
 * class, and .js sits in the source class of the exemption table.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(scriptDir, "..", "..");
const repoRoot = resolve(webRoot, "..");
const dataDir = join(webRoot, "src", "data");
const outDir = join(webRoot, "src", "charts");
const bookPath = join(dataDir, "book.json");

/* Palette: the six named ink on ledger values from styles/main.css. */
const PAPER = "#f3f5f6";
const INK = "#16233b";
const RULE = "#c6cfd6";
const GREEN = "#0b6e4f";

const MONO = "Menlo, Consolas, monospace";
const DISPLAY = "Charter, Georgia, serif";

const CAP_WIDTH = 960;
const CAP_HEIGHT = 580;
const EDGE = 48;
const CAP_PLOT_LEFT = 140;
const CAP_PLOT_RIGHT = 912;
const CAP_PLOT_TOP = 160;
const CAP_PLOT_BASE = 440;

const MIX_WIDTH = 960;
const MIX_HEIGHT = 520;
const MIX_PLOT_LEFT = 160;
const MIX_PLOT_RIGHT = 912;
const MIX_PLOT_TOP = 150;
const MIX_PLOT_BASE = 380;

/** XML text escaping, applied to every string that reaches the file. */
function escapeXml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** A committed integer, from a bare number or an envelope value. */
function publishedNumber(node) {
  if (typeof node === "number" && Number.isInteger(node)) {
    return node;
  }
  if (
    node !== null &&
    typeof node === "object" &&
    typeof node.value === "number" &&
    Number.isInteger(node.value)
  ) {
    return node.value;
  }
  return null;
}

/** Integer cents as a compact dollar label: $0, $1M, $2.5M, $500M. */
function compactDollars(cents) {
  const sign = cents < 0 ? "-" : "";
  const dollars = Math.abs(cents) / 100;
  if (dollars >= 1000000000) {
    return `${sign}$${trimNumber(dollars / 1000000000)}B`;
  }
  if (dollars >= 1000000) {
    return `${sign}$${trimNumber(dollars / 1000000)}M`;
  }
  if (dollars >= 1000) {
    return `${sign}$${trimNumber(dollars / 1000)}K`;
  }
  return `${sign}$${trimNumber(dollars)}`;
}

/** Fixed two decimal places with trailing zeros dropped, no exponent. */
function trimNumber(value) {
  return String(Number(value.toFixed(2)));
}

/**
 * Whole number count axis: at most six ticks, every tick an integer,
 * so a committed count never prints as a fraction.
 */
function countAxis(maxCount) {
  const top = Math.max(1, maxCount);
  const step = Math.max(1, Math.ceil(top / 5));
  const axisMax = Math.ceil(top / step) * step;
  const ticks = [];
  for (let value = 0; value <= axisMax; value += step) {
    ticks.push(value);
  }
  return { axisMax, ticks };
}

/** Read the published histogram bins, or null when unreadable. */
function readBins(summary) {
  const distribution = summary.capacity_distribution;
  if (!Array.isArray(distribution) || distribution.length === 0) {
    return null;
  }
  const bins = [];
  for (const entry of distribution) {
    if (entry === null || typeof entry !== "object") {
      return null;
    }
    const low = publishedNumber(entry.bin_low);
    const high = publishedNumber(entry.bin_high);
    const count = publishedNumber(entry.count);
    if (low === null || high === null || count === null) {
      return null;
    }
    bins.push({ low, high, count });
  }
  return bins;
}

/**
 * Read the published mix counts, or null when unreadable. The bars are
 * required; the feasible and infeasible companions are optional and
 * print as "not published" when the payload omits them.
 */
function readMix(summary) {
  const mix = summary.binding_constraint_mix;
  if (mix === null || typeof mix !== "object" || Array.isArray(mix)) {
    return null;
  }
  const nested = mix.counts;
  const countsNode =
    nested !== null && typeof nested === "object" && !Array.isArray(nested)
      ? nested
      : mix;
  const wanted = [
    ["runway", "Runway"],
    ["dscr", "DSCR"],
    ["band_cap", "Band cap"],
  ];
  const bars = [];
  for (const [key, label] of wanted) {
    const count = publishedNumber(countsNode[key]);
    if (count === null) {
      return null;
    }
    bars.push({ key, label, count });
  }
  const feasibleSource =
    publishedNumber(mix.feasible_count) ?? publishedNumber(countsNode.feasible_count);
  const infeasibleSource =
    publishedNumber(mix.infeasible_count) ?? publishedNumber(countsNode.infeasible_count);
  return { bars, feasible: feasibleSource, infeasible: infeasibleSource };
}

/** Shared chrome: paper sheet, ink header band, ruled border. */
function sheet(out, width, height) {
  out.push(`<rect width="${width}" height="${height}" fill="${PAPER}"/>`);
  out.push(`<rect width="${width}" height="4" fill="${INK}"/>`);
  out.push(
    `<rect x="0.5" y="0.5" width="${width - 1}" height="${height - 1}" fill="none" stroke="${RULE}" stroke-width="1"/>`,
  );
}

/** Header block: eyebrow, display title, rule, one subtitle line. */
function header(out, eyebrow, title, subtitle, right) {
  out.push(
    `<text x="${EDGE}" y="44" font-size="13" letter-spacing="2.5" fill="${INK}">${escapeXml(eyebrow)}</text>`,
  );
  out.push(
    `<text x="${EDGE}" y="88" font-family="${DISPLAY}" font-size="30" font-weight="700" fill="${INK}">${escapeXml(title)}</text>`,
  );
  out.push(`<line x1="${EDGE}" y1="106" x2="${right}" y2="106" stroke="${RULE}" stroke-width="1"/>`);
  out.push(
    `<text x="${EDGE}" y="130" font-size="14" fill="${INK}">${escapeXml(subtitle)}</text>`,
  );
}

/** Grid, baseline, left axis, and integer tick labels for one plot. */
function axis(out, plot) {
  const { left, right, top, base, axisMax, ticks } = plot;
  const span = base - top;
  const yOf = (value) => base - (value / axisMax) * span;
  out.push(`<g stroke="${RULE}" stroke-width="1" shape-rendering="crispEdges">`);
  for (const tick of ticks) {
    if (tick === 0) {
      continue;
    }
    out.push(`<line x1="${left}" y1="${yOf(tick).toFixed(1)}" x2="${right}" y2="${yOf(tick).toFixed(1)}"/>`);
  }
  out.push(`</g>`);
  out.push(
    `<line x1="${left}" y1="${base}" x2="${right}" y2="${base}" stroke="${INK}" stroke-width="1.5"/>`,
  );
  out.push(`<line x1="${left}" y1="${top}" x2="${left}" y2="${base}" stroke="${RULE}" stroke-width="1"/>`);
  for (const tick of ticks) {
    const y = (yOf(tick) + 4).toFixed(1);
    out.push(
      `<text x="${left - 12}" y="${y}" text-anchor="end" font-size="13" fill="${INK}">${tick}</text>`,
    );
  }
  return yOf;
}

function writeCapacity(bins, asof) {
  const outFile = join(outDir, "dashboard-capacity.svg");
  const maxCount = bins.reduce((peak, bin) => Math.max(peak, bin.count), 0);
  const { axisMax, ticks } = countAxis(maxCount);
  const out = [];

  out.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${CAP_WIDTH}" height="${CAP_HEIGHT}" viewBox="0 0 ${CAP_WIDTH} ${CAP_HEIGHT}" role="img" aria-labelledby="dash-cap-title dash-cap-desc" font-family="${MONO}">`,
  );
  out.push(`<title id="dash-cap-title">Capacity distribution across the committed book</title>`);
  out.push(
    `<desc id="dash-cap-desc">Histogram of ${bins.length} published capacity bins over the committed book, asof ${escapeXml(asof)}. Bar heights scale the committed counts, each count prints above its bar, and each bin edge prints below the axis. Counts and edges are verbatim from book.json meta.book_summary; the pipeline did the binning. No figure is computed in the browser.</desc>`,
  );
  sheet(out, CAP_WIDTH, CAP_HEIGHT);
  header(
    out,
    `COMMITTED BOOK, ASOF ${asof}`,
    "Capacity distribution across the committed book",
    `${bins.length} published bins, counts printed verbatim from book.json`,
    CAP_PLOT_RIGHT,
  );

  const yOf = axis(out, {
    left: CAP_PLOT_LEFT,
    right: CAP_PLOT_RIGHT,
    top: CAP_PLOT_TOP,
    base: CAP_PLOT_BASE,
    axisMax,
    ticks,
  });
  const midY = (CAP_PLOT_TOP + CAP_PLOT_BASE) / 2;
  out.push(
    `<text x="36" y="${midY}" transform="rotate(-90 36 ${midY})" text-anchor="middle" font-size="13" letter-spacing="1.5" fill="${INK}">borrowers (count)</text>`,
  );

  const slot = (CAP_PLOT_RIGHT - CAP_PLOT_LEFT) / bins.length;
  const barWidth = Math.min(96, slot * 0.62);
  bins.forEach((bin, position) => {
    const centre = CAP_PLOT_LEFT + slot * (position + 0.5);
    const barX = centre - barWidth / 2;
    if (bin.count > 0) {
      const barTop = yOf(bin.count);
      out.push(
        `<rect x="${barX.toFixed(1)}" y="${barTop.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${(CAP_PLOT_BASE - barTop).toFixed(1)}" fill="${GREEN}"/>`,
      );
      out.push(
        `<text x="${centre.toFixed(1)}" y="${(barTop - 8).toFixed(1)}" text-anchor="middle" font-size="15" fill="${INK}">${bin.count}</text>`,
      );
    } else {
      out.push(
        `<text x="${centre.toFixed(1)}" y="${CAP_PLOT_BASE - 8}" text-anchor="middle" font-size="15" fill="${INK}">0</text>`,
      );
    }
  });

  bins.forEach((bin, position) => {
    const edgeX = CAP_PLOT_LEFT + slot * position;
    const anchor = position === 0 ? "start" : "middle";
    out.push(
      `<text x="${edgeX.toFixed(1)}" y="${CAP_PLOT_BASE + 26}" text-anchor="${anchor}" font-size="12" fill="${INK}">${escapeXml(compactDollars(bin.low))}</text>`,
    );
  });
  const lastEdgeX = CAP_PLOT_LEFT + slot * bins.length;
  out.push(
    `<text x="${lastEdgeX.toFixed(1)}" y="${CAP_PLOT_BASE + 26}" text-anchor="end" font-size="12" fill="${INK}">${escapeXml(compactDollars(bins[bins.length - 1].high))}</text>`,
  );
  out.push(
    `<text x="${((CAP_PLOT_LEFT + CAP_PLOT_RIGHT) / 2).toFixed(1)}" y="${CAP_PLOT_BASE + 48}" text-anchor="middle" font-size="11" letter-spacing="1" fill="${INK}">bin edges, US dollars</text>`,
  );

  out.push(`<line x1="${EDGE}" y1="510" x2="${CAP_PLOT_RIGHT}" y2="510" stroke="${RULE}" stroke-width="1"/>`);
  out.push(
    `<text x="${EDGE}" y="534" font-size="13" fill="${INK}">source: committed book.json meta.book_summary, counts verbatim</text>`,
  );
  out.push(
    `<text x="${CAP_PLOT_RIGHT}" y="534" text-anchor="end" font-size="13" fill="${INK}">asof ${escapeXml(asof)}</text>`,
  );
  out.push(
    `<text x="${EDGE}" y="556" font-size="13" fill="${INK}">binning done by the pipeline; this chart draws geometry only</text>`,
  );
  out.push(`</svg>`);

  const svg = `${out.join("\n")}\n`;
  writeFileSync(outFile, svg, "utf8");
  console.log(
    `dashboard-charts: wrote ${relative(repoRoot, outFile)} (${Buffer.byteLength(svg, "utf8")} bytes)`,
  );
}

function writeMix(mix, asof) {
  const outFile = join(outDir, "dashboard-mix.svg");
  const maxCount = mix.bars.reduce((peak, bar) => Math.max(peak, bar.count), 0);
  const { axisMax, ticks } = countAxis(maxCount);
  const feasibleText =
    mix.feasible === null ? "feasible: not published" : `feasible: ${mix.feasible}`;
  const infeasibleText =
    mix.infeasible === null ? "infeasible: not published" : `infeasible: ${mix.infeasible}`;
  const out = [];

  out.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${MIX_WIDTH}" height="${MIX_HEIGHT}" viewBox="0 0 ${MIX_WIDTH} ${MIX_HEIGHT}" role="img" aria-labelledby="dash-mix-title dash-mix-desc" font-family="${MONO}">`,
  );
  out.push(`<title id="dash-mix-title">Binding constraint mix across the committed book</title>`);
  out.push(
    `<desc id="dash-mix-desc">Three bars of committed binding constraint counts over the committed book, asof ${escapeXml(asof)}: runway ${mix.bars[0].count}, dscr ${mix.bars[1].count}, band cap ${mix.bars[2].count}. The footer prints ${escapeXml(feasibleText)} and ${escapeXml(infeasibleText)}. All values are verbatim from book.json meta.book_summary; the pipeline did the counting. No figure is computed in the browser.</desc>`,
  );
  sheet(out, MIX_WIDTH, MIX_HEIGHT);
  header(
    out,
    `COMMITTED BOOK, ASOF ${asof}`,
    "Binding constraint mix across the committed book",
    "counts per binding constraint, printed verbatim from book.json",
    MIX_PLOT_RIGHT,
  );

  const yOf = axis(out, {
    left: MIX_PLOT_LEFT,
    right: MIX_PLOT_RIGHT,
    top: MIX_PLOT_TOP,
    base: MIX_PLOT_BASE,
    axisMax,
    ticks,
  });
  const midY = (MIX_PLOT_TOP + MIX_PLOT_BASE) / 2;
  out.push(
    `<text x="36" y="${midY}" transform="rotate(-90 36 ${midY})" text-anchor="middle" font-size="13" letter-spacing="1.5" fill="${INK}">borrowers (count)</text>`,
  );

  const slot = (MIX_PLOT_RIGHT - MIX_PLOT_LEFT) / mix.bars.length;
  const barWidth = Math.min(140, slot * 0.5);
  mix.bars.forEach((bar, position) => {
    const centre = MIX_PLOT_LEFT + slot * (position + 0.5);
    const barX = centre - barWidth / 2;
    if (bar.count > 0) {
      const barTop = yOf(bar.count);
      out.push(
        `<rect x="${barX.toFixed(1)}" y="${barTop.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${(MIX_PLOT_BASE - barTop).toFixed(1)}" fill="${GREEN}"/>`,
      );
      out.push(
        `<text x="${centre.toFixed(1)}" y="${(barTop - 10).toFixed(1)}" text-anchor="middle" font-size="17" fill="${INK}">${bar.count}</text>`,
      );
    } else {
      out.push(
        `<text x="${centre.toFixed(1)}" y="${MIX_PLOT_BASE - 10}" text-anchor="middle" font-size="17" fill="${INK}">0</text>`,
      );
    }
    out.push(
      `<text x="${centre.toFixed(1)}" y="${MIX_PLOT_BASE + 28}" text-anchor="middle" font-size="15" fill="${INK}">${escapeXml(bar.label)}</text>`,
    );
    out.push(
      `<text x="${centre.toFixed(1)}" y="${MIX_PLOT_BASE + 50}" text-anchor="middle" font-size="12" fill="${INK}">${escapeXml(`(${bar.key})`)}</text>`,
    );
  });

  out.push(`<line x1="${EDGE}" y1="440" x2="${MIX_PLOT_RIGHT}" y2="440" stroke="${RULE}" stroke-width="1"/>`);
  out.push(
    `<text x="${EDGE}" y="464" font-size="13" fill="${INK}">${escapeXml(feasibleText)}</text>`,
  );
  out.push(
    `<text x="${MIX_PLOT_RIGHT}" y="464" text-anchor="end" font-size="13" fill="${INK}">${escapeXml(infeasibleText)}</text>`,
  );
  out.push(
    `<text x="${EDGE}" y="486" font-size="13" fill="${INK}">source: committed book.json meta.book_summary, counts verbatim</text>`,
  );
  out.push(
    `<text x="${MIX_PLOT_RIGHT}" y="486" text-anchor="end" font-size="13" fill="${INK}">asof ${escapeXml(asof)}</text>`,
  );
  out.push(`</svg>`);

  const svg = `${out.join("\n")}\n`;
  writeFileSync(outFile, svg, "utf8");
  console.log(
    `dashboard-charts: wrote ${relative(repoRoot, outFile)} (${Buffer.byteLength(svg, "utf8")} bytes)`,
  );
}

function main() {
  if (!existsSync(bookPath)) {
    console.log(
      "dashboard-charts: web/src/data/book.json is absent, so no dashboard SVGs " +
        "were written; run scripts/sync-payloads.js first. The dashboard route " +
        "keeps its honest placeholders.",
    );
    return;
  }

  const book = JSON.parse(readFileSync(bookPath, "utf8"));
  const meta = book !== null && typeof book === "object" ? book.meta : null;
  const summary =
    meta !== null && typeof meta === "object" && meta.book_summary !== null && typeof meta.book_summary === "object" && !Array.isArray(meta.book_summary)
      ? meta.book_summary
      : null;

  if (summary === null) {
    console.log(
      "dashboard-charts: meta.book_summary is absent from the committed " +
        "book.json, so no dashboard SVGs were written. The block lands with " +
        "the phase 4 payload build; until then the dashboard route keeps its " +
        "honest placeholders and prints no invented count.",
    );
    return;
  }

  const rates = book !== null && typeof book === "object" ? book.rates : null;
  const asof =
    rates !== null && typeof rates === "object" && typeof rates.observation_date === "string"
      ? rates.observation_date
      : "committed snapshot";

  const bins = readBins(summary);
  if (bins === null) {
    console.log(
      "dashboard-charts: meta.book_summary carries no readable " +
        "capacity_distribution of bin_low, bin_high, and count entries, so " +
        "dashboard-capacity.svg was not written; the route keeps its placeholder.",
    );
  } else {
    writeCapacity(bins, asof);
  }

  const mix = readMix(summary);
  if (mix === null) {
    console.log(
      "dashboard-charts: meta.book_summary carries no readable " +
        "binding_constraint_mix counts for runway, dscr, and band_cap, so " +
        "dashboard-mix.svg was not written; the route keeps its placeholder.",
    );
  } else {
    writeMix(mix, asof);
  }
}

main();
