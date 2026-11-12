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
import type { Envelope, Unit } from "./payload";

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
