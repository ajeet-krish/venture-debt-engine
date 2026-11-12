/**
 * asof deep link handling (spec section 12, architecture P3.7).
 *
 * One committed day: the ?asof= parameter is compared with the committed
 * snapshot as text. When the link names a day that is not committed here,
 * the page says so honestly and keeps showing the committed snapshot.
 * Nothing is recomputed and no figure moves.
 */

export interface AsofLinkStatus {
  /** The committed snapshot date (book rates observation date). */
  readonly committed: string;
  /** The requested day from the link, or null when the link has none. */
  readonly requested: string | null;
  /** True when there is no link parameter or it names the committed day. */
  readonly matches: boolean;
}

/** Read ?asof=YYYY-MM-DD from the current URL against the committed day. */
export function readAsofLink(committed: string): AsofLinkStatus {
  const requested = new URLSearchParams(window.location.search).get("asof");
  if (requested === null) {
    return { committed, requested: null, matches: true };
  }
  return { committed, requested, matches: requested === committed };
}

/** Render the asof status line into its mount point. */
export function renderAsofNote(container: Element, status: AsofLinkStatus): void {
  container.textContent = "";
  const note = document.createElement("p");
  note.className = "asof-note";
  if (status.requested === null) {
    note.textContent =
      `Committed snapshot ${status.committed}. ` +
      "Append ?asof=YYYY-MM-DD to this page address to link a committed day.";
  } else if (status.matches) {
    note.textContent =
      `Deep link ?asof=${status.requested} matches the committed snapshot ${status.committed}.`;
  } else {
    note.classList.add("asof-mismatch");
    note.textContent =
      `This site publishes one committed snapshot: ${status.committed}. ` +
      `The link asked for ${status.requested}, which is not committed here, ` +
      "so the page keeps showing the committed snapshot. Nothing is recomputed.";
  }
  container.append(note);
}
