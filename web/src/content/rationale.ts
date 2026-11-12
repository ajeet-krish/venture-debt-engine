/**
 * Illustrative structuring rationale notes for the structures page.
 *
 * Short teaching commentary per structure, grounded in the cited bands
 * and tied to what the table shows. Every note states that it is
 * illustrative. None of it is advice, a suggestion, or a verdict on any
 * structure: the bands are cited market heuristics, and the numbers in
 * the table are committed payload values for synthetic borrowers.
 */

export interface RationaleNote {
  /** Committed structure token as it appears in the comparison rows. */
  readonly structure: string;
  readonly title: string;
  readonly body: string;
}

/** Display labels for the three committed comparison structures. */
export const STRUCTURE_LABELS: Readonly<Record<string, string>> = {
  bridge: "Bridge",
  io_then_straight_line: "Interest only then amortizing",
  rbf_capped: "Revenue based capped",
};

export const RATIONALE_NOTES: readonly RationaleNote[] = [
  {
    structure: "bridge",
    title: "Bridge: a bridge toward a financing event",
    body:
      "A bridge suits a pre-raise profile awaiting a financing event: the borrower " +
      "draws now and repays principal plus accrued interest in a single payment at " +
      "maturity, so the lender's exposure is short and the tenor is pinned at 3 " +
      "months. The spread sits in the cited pricing band and the closing fee sits in " +
      "the cited fee band, both from the Mercury venture debt term sheet guide " +
      "(accessed 2026-11-10). In the table above the bridge row shows the pinned " +
      "three month term, a single repayment, and an interest-only period marked not " +
      "applicable, which illustrates how the structure behaves at this capacity. " +
      "Illustrative teaching commentary only.",
  },
  {
    structure: "io_then_straight_line",
    title: "Amortizing term: matched to live cash flows",
    body:
      "The amortizing term suits this borrower's cash flows when revenue is live: an " +
      "interest-only window inside the cited 12 to 18 month band (Mercury venture debt " +
      "term sheet guide, accessed 2026-11-10) defers principal, then straight-line " +
      "principal runs over the remaining periods. Because the canonical sizing is " +
      "solved on this schedule, the table's minimum DSCR ties back to the sizing " +
      "answer, and the term equals the interest-only months plus the pinned " +
      "amortizing periods. Read against the cited bands, the row illustrates how " +
      "schedule shape moves interest and DSCR. Illustrative teaching commentary only.",
  },
  {
    structure: "rbf_capped",
    title: "Revenue based: smaller advances tied to revenue",
    body:
      "Revenue-based financing suits revenue-heavy profiles at smaller advances: " +
      "remittances take a fixed share of period revenue until the drawn cap multiple " +
      "inside the cited 1.3 to 2.5x band (Mercury venture debt term sheet guide, " +
      "accessed 2026-11-10) is reached, so repayment follows revenue rather than a " +
      "fixed schedule. In the table the revenue-based row shows the pinned 24 month " +
      "horizon, contractual interest of 0 because this structure carries no interest " +
      "term, and total receipts equal to the final cumulative remittance. Those " +
      "values illustrate the mechanics at this capacity. Illustrative teaching " +
      "commentary only.",
  },
];
