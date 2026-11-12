/**
 * Borrower selector (architecture P3.7).
 *
 * A labeled select over the committed borrower ids. Changing it swaps
 * which committed record the page displays; it never computes, filters on,
 * or re-sizes anything.
 */

export interface SelectorOption {
  /** Committed borrower id, used as the option value. */
  readonly value: string;
  /** Display text: the id plus a committed status or structure marker. */
  readonly label: string;
}

export interface SelectorConfig {
  /** Unique id for the select element, used by the label's for attribute. */
  readonly selectId: string;
  readonly label: string;
  readonly options: readonly SelectorOption[];
  readonly defaultValue: string;
  readonly onChange: (value: string) => void;
}

/** Render the selector into its mount point and return the select node. */
export function mountSelector(container: Element, config: SelectorConfig): HTMLSelectElement {
  container.textContent = "";

  const wrap = document.createElement("div");
  wrap.className = "selector";

  const label = document.createElement("label");
  label.setAttribute("for", config.selectId);
  label.textContent = config.label;

  const select = document.createElement("select");
  select.id = config.selectId;
  for (const option of config.options) {
    const node = document.createElement("option");
    node.value = option.value;
    node.textContent = option.label;
    select.append(node);
  }
  select.value = config.defaultValue;
  select.addEventListener("change", () => {
    config.onChange(select.value);
  });

  wrap.append(label, select);
  container.append(wrap);
  return select;
}
