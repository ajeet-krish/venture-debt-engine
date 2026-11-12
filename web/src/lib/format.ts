/**
 * Display formatting only.
 *
 * This module performs no arithmetic; any sum, ratio, difference, or
 * comparison is forbidden here and enforced by review. Values arrive as
 * committed payload strings or integer cent strings. What happens here is
 * string presentation: digit grouping, decimal point placement per the
 * unit's scale (cents to display dollars is point placement, not
 * computation), unit suffixes, and ISO date rendering. A payload value in
 * and out of this module reads the same digits, only arranged for humans.
 */

const FIXED_DECIMAL = /^-?\d+(?:\.\d+)?$/;
const INTEGER_STRING = /^-?\d+$/;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

const MONTHS: Readonly<Record<string, string>> = {
  "01": "Jan",
  "02": "Feb",
  "03": "Mar",
  "04": "Apr",
  "05": "May",
  "06": "Jun",
  "07": "Jul",
  "08": "Aug",
  "09": "Sep",
  10: "Oct",
  11: "Nov",
  12: "Dec",
};

function groupThousands(digits: string): string {
  // Regex grouping only: no counters, no modulo, no arithmetic.
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/**
 * Render a fixed-scale decimal string with thousands grouping.
 * The integer part is grouped by comma; the fraction is padded to the
 * stated scale when short and printed verbatim when long (the committed
 * payload guarantees an exact scale, so the long case is drift, not input).
 */
export function formatFixed(value: string, scale: number): string {
  if (!FIXED_DECIMAL.test(value)) {
    throw new Error(`formatFixed: ${JSON.stringify(value)} is not a fixed-scale decimal string`);
  }
  if (!Number.isInteger(scale) || scale < 0) {
    throw new Error(`formatFixed: scale must be a non-negative integer, received ${scale}`);
  }
  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const parts = unsigned.split(".");
  const whole = groupThousands(parts[0]);
  const fraction = parts[1] ?? "";
  const sign = negative ? "-" : "";
  if (scale > 0) {
    const padded = fraction.padEnd(scale, "0");
    return `${sign}${whole}.${padded}`;
  }
  return `${sign}${whole}`;
}

/**
 * Render integer cents as display dollars by placing the decimal point
 * two positions from the right. Point placement only: the digits are the
 * committed cents string, never multiplied or divided.
 */
export function centsToDisplayDollars(cents: string): string {
  if (!INTEGER_STRING.test(cents)) {
    throw new Error(`centsToDisplayDollars: ${JSON.stringify(cents)} is not an integer cents string`);
  }
  const negative = cents.startsWith("-");
  const digits = negative ? cents.slice(1) : cents;
  const tail = digits.slice(-2).padStart(2, "0");
  const head = digits.slice(0, -2);
  const dollars = head.length > 0 ? `${groupThousands(head)}.${tail}` : `0.${tail}`;
  return negative ? `-${dollars}` : dollars;
}

/** Render a rate or percentage envelope value with its percent suffix. */
export function percent(value: string, scale: number): string {
  return `${formatFixed(value, scale)}%`;
}

/** Render a multiple envelope value with its x suffix, as in 1.42x. */
export function multiple(value: string, scale: number): string {
  return `${formatFixed(value, scale)}x`;
}

/**
 * Render an ISO date (YYYY-MM-DD) for display, for example 2026-11-10
 * becomes 10 Nov 2026. Month names come from a fixed lookup table; the
 * digits are read, never converted through arithmetic.
 */
export function isoDate(iso: string): string {
  const parts = ISO_DATE.exec(iso);
  if (parts === null) {
    throw new Error(`isoDate: ${JSON.stringify(iso)} is not an ISO date (YYYY-MM-DD)`);
  }
  const [, year, month, day] = parts;
  const monthName = MONTHS[month];
  if (monthName === undefined) {
    throw new Error(`isoDate: ${JSON.stringify(iso)} has an unusable month`);
  }
  const displayDay = day.startsWith("0") ? day.slice(1) : day;
  return `${displayDay} ${monthName} ${year}`;
}
