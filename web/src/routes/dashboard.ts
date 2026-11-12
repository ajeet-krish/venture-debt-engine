/**
 * Dashboard route: honest skeleton over the committed book (architecture
 * P4.6).
 *
 * Every dashboard number publishes in book.json meta.book_summary as a
 * count envelope computed by the pipeline at build time. This route
 * reads no figure from that block: it reports structurally whether the
 * block exists (a key presence check, never a magnitude read) and mounts
 * the two build time SVG assets through the shared chart pattern,
 * keeping the honest placeholder already in dashboard.html whenever an
 * asset is absent. No arithmetic runs anywhere in this file.
 */
import "../styles/main.css";
import { mountShell } from "../components/page";
import { readAsofLink, renderAsofNote } from "../lib/asof";
import { chartAsset } from "../lib/chart";

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

/**
 * Structural presence check only: whether the meta object carries a
 * book_summary key. No value is read, no magnitude compared, no count
 * derived, so the zero arithmetic boundary holds.
 */
function hasBookSummary(meta: unknown): boolean {
  if (typeof meta !== "object" || meta === null || Array.isArray(meta)) {
    return false;
  }
  return "book_summary" in meta;
}

/** The summary slot: an honest notice for either state of the payload. */
function renderSummaryNotice(): void {
  const host = mount("[data-dashboard-summary]");
  host.textContent = "";
  const notice = element("div", "notice");
  notice.setAttribute("role", "note");
  if (hasBookSummary(book.meta)) {
    notice.append(
      element("p", "notice-head", "Summary block present, figures not wired yet"),
      element(
        "p",
        undefined,
        "meta.book_summary is present in the committed payload. The verbatim " +
          "figure table for the capacity distribution, binding constraint mix, " +
          "and feasible and infeasible counts lands with the dashboard wiring " +
          "task; this route reads nothing from the block yet.",
      ),
    );
  } else {
    notice.append(
      element("p", "notice-head", "Dashboard figures not published yet"),
      element(
        "p",
        undefined,
        "The committed book summary (meta.book_summary) has not landed in " +
          "book.json, so this page prints no dashboard number. The capacity " +
          "distribution, binding constraint mix, and infeasible count render " +
          "verbatim from that block when the phase 4 payload build publishes " +
          "it. Until then the chart slots below keep their honest placeholders.",
      ),
    );
  }
  host.append(notice);
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
  figure.append(
    element(
      "figcaption",
      "chart-note",
      "Generated at build time from the committed book.json meta.book_summary " +
        "counts. The bars are a static asset: no figure is computed in the browser.",
    ),
  );
  host.append(figure);
}

renderAsofNote(mount("[data-asof]"), readAsofLink(book.rates.observation_date));
renderSummaryNotice();
for (const slot of CHARTS) {
  renderChart(slot);
}
