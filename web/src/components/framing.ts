/**
 * The framing line from spec section 4, rendered on every page.
 *
 * FRAMING_LINE is verbatim and may not be edited to sound stronger. The
 * same string also appears in every HTML entry as the og:description meta
 * content and as the static footer paragraph, so the line survives with
 * JavaScript disabled and stays greppable in the built site.
 */

export const FRAMING_LINE =
  "A portfolio demonstration of venture debt structuring and lender returns on synthetic borrowers and public rate data. Illustrative sizing tool, not credit approval.";

/**
 * Ensure the framing line is present in the container. Idempotent: when
 * the static HTML already carries the paragraph, the text is rewritten
 * from the constant rather than duplicated.
 */
export function renderFraming(container: Element): void {
  let line = container.querySelector(".framing");
  if (line === null) {
    line = document.createElement("p");
    line.className = "framing";
    container.append(line);
  }
  line.textContent = FRAMING_LINE;
}
