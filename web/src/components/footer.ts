/**
 * Footer: product identity plus the provenance stamps.
 *
 * The snapshot date comes from the payload rates block (the committed
 * asof, never the wall clock). The commit SHA and the wall clock come
 * from payloads/build_meta.json, which is the only surface allowed to
 * carry them. The checks passed stamp deliberately does not render here:
 * the footer never displays an unverified claim, and that stamp ships
 * with the verification page in phase 5.
 */
import { isoDate } from "../lib/format";

export interface FooterStamps {
  /** Committed asof from book.rates.observation_date. */
  readonly snapshotDate: string;
  /** git_sha from build_meta.json. */
  readonly gitSha: string;
  /** built_at_utc from build_meta.json. */
  readonly builtAtUtc: string;
}

const PRODUCT_NAME = "Venture Debt Engine";
const PRODUCT_SUBTITLE = "Venture debt sizing and lender returns";

function stamp(label: string, value: string, extraClass?: string): HTMLDivElement {
  const group = document.createElement("div");
  const term = document.createElement("dt");
  term.textContent = label;
  const description = document.createElement("dd");
  description.textContent = value;
  if (extraClass !== undefined) {
    description.className = extraClass;
  }
  group.append(term, description);
  return group;
}

export function renderFooter(container: Element, stamps: FooterStamps): void {
  container.textContent = "";

  const product = document.createElement("p");
  product.className = "footer-product";
  const name = document.createElement("span");
  name.className = "footer-name";
  name.textContent = PRODUCT_NAME;
  const subtitle = document.createElement("span");
  subtitle.className = "footer-subtitle";
  subtitle.textContent = PRODUCT_SUBTITLE;
  product.append(name, subtitle);

  const list = document.createElement("dl");
  list.className = "footer-stamps";
  const snapshot = stamp("Snapshot", isoDate(stamps.snapshotDate));
  const commit = stamp("Commit", stamps.gitSha, "footer-sha");
  const built = stamp("Built (UTC)", stamps.builtAtUtc);
  list.append(snapshot, commit, built);

  container.append(product, list);
}
