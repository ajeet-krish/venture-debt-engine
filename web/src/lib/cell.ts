/**
 * Cell rendering: turn one committed envelope into display text.
 *
 * Formatting only, exactly as lib/format.ts defines it: digit grouping,
 * decimal point placement per the unit's scale, unit suffixes, and date
 * rendering. The digits that leave this module are the digits the payload
 * published. No sum, ratio, difference, or comparison of payload values
 * happens here.
 */
import { centsToDisplayDollars, formatFixed, isoDate, multiple, percent } from "./format";
import {
  isFirstBreachCell,
  type Envelope,
  type FirstBreach,
  type Unit,
} from "./payload";

/** The numeric part of an envelope value as a string, never converted. */
function valueString(value: string | number): string {
  return typeof value === "number" ? String(value) : value;
}

/** Integer cents as display dollars with the currency sign in front. */
export function dollars(cents: string): string {
  const display = centsToDisplayDollars(cents);
  if (display.startsWith("-")) {
    return `-$${display.slice(1)}`;
  }
  return `$${display}`;
}

/**
 * Render one envelope for a table cell or a figure row. The unit selects
 * the presentation; the digits are passed through untouched.
 */
export function envelopeText(env: Envelope): string {
  const raw = valueString(env.value);
  const scale = typeof env.scale === "number" ? env.scale : 0;
  const unit: Unit = env.unit;
  switch (unit) {
    case "usd_minor_units":
      return dollars(raw);
    case "percent":
    case "percent_annual":
      return percent(raw, scale);
    case "multiple":
      return multiple(raw, scale);
    case "ratio":
      return `${formatFixed(raw, scale)}x`;
    case "bp":
      return `${formatFixed(raw, scale)} bp`;
    case "count":
    case "months":
      return scale > 0 ? formatFixed(raw, scale) : raw;
    case "date":
      return isoDate(raw);
    case "sha256_hex":
      return raw;
    case "status":
      return raw;
  }
}

/** Human labels for the three committed binding constraint tokens. */
const CONSTRAINT_LABELS: Readonly<Record<string, string>> = {
  dscr: "DSCR floor",
  runway: "Runway floor",
  band_cap: "Band cap",
};

/** Label for a committed binding constraint token, or the token itself. */
export function constraintLabel(token: string): string {
  return CONSTRAINT_LABELS[token] ?? token;
}

/** Display text for a status cell value, for example not_applicable. */
export function statusLabel(value: string | number): string {
  return String(value).replace(/_/g, " ");
}

/** Resolved view of a published first breach leaf (architecture P4.1). */
export interface BreachView {
  readonly status: string;
  readonly value?: Envelope;
  readonly reason?: string;
}

/** The covenant stamp chip for one first breach view. */
export interface BreachChip {
  readonly className: string;
  readonly text: string;
  readonly reason?: string;
}

/**
 * Resolve a first breach leaf against the reason that may travel as its
 * sibling under the section 6.4 companion rule. Shape reading only: the
 * status text comes from the payload and no magnitude is compared.
 */
export function breachView(leaf: FirstBreach, siblingReason?: string): BreachView {
  if (isFirstBreachCell(leaf)) {
    return {
      status: String(leaf.status.value),
      value: leaf.value,
      reason: leaf.reason ?? siblingReason,
    };
  }
  if (leaf.unit === "months") {
    return { status: "breach", value: leaf };
  }
  return { status: String(leaf.value), reason: siblingReason };
}

/**
 * The covenant stamp chip for one first breach view: a breach names its
 * published month under downside, feasible reads no breach through the
 * horizon, and any other status prints its label plus its stated reason.
 */
export function breachChip(view: BreachView): BreachChip {
  if (view.status === "breach") {
    if (view.value === undefined) {
      return {
        className: "chip chip-flag",
        text: "breach",
        reason: "breach status published without a month value",
      };
    }
    return {
      className: "chip chip-flag",
      text: `month ${envelopeText(view.value)} under downside`,
    };
  }
  if (view.status === "feasible") {
    return { className: "chip chip-pass", text: "no breach through the horizon" };
  }
  if (view.reason !== undefined) {
    return {
      className: "chip chip-pending",
      text: statusLabel(view.status),
      reason: view.reason,
    };
  }
  return { className: "chip chip-pending", text: statusLabel(view.status) };
}
