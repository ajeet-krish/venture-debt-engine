/**
 * Shared page shell: payload load, shape assertion, framing, footer.
 *
 * Every route module calls mountShell() first. The book and build meta
 * imports are static (copied at build time by scripts/sync-payloads.js),
 * validated for shape on load, and never fetched at runtime.
 */
import bookJson from "../data/book.json";
import buildMetaJson from "../data/build_meta.json";
import { loadBook, loadBuildMeta, type Book, type BuildMeta } from "../lib/payload";
import { renderFraming } from "./framing";
import { renderFooter } from "./footer";

export interface PageShell {
  readonly main: HTMLElement;
  readonly book: Book;
  readonly buildMeta: BuildMeta;
}

function mountPoint(selector: string): Element {
  const node = document.querySelector(selector);
  if (node === null) {
    throw new Error(`page shell: missing ${selector} mount point in the HTML entry`);
  }
  return node;
}

export function mountShell(): PageShell {
  const book = loadBook(bookJson);
  const buildMeta = loadBuildMeta(buildMetaJson);

  const main = document.querySelector("main");
  if (main === null) {
    throw new Error('page shell: every route entry needs a <main id="main"> element');
  }

  renderFraming(mountPoint("[data-framing]"));
  renderFooter(mountPoint("[data-footer]"), {
    snapshotDate: book.rates.observation_date,
    gitSha: buildMeta.git_sha,
    builtAtUtc: buildMeta.built_at_utc,
  });

  return { main, book, buildMeta };
}
