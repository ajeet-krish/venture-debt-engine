/**
 * Types and shape validation for the committed payloads.
 *
 * This module validates shape only and performs no arithmetic of any kind:
 * no sums, ratios, differences, or comparisons of payload values. It walks
 * the parsed JSON looking for structural drift (a missing unit key, a float
 * JSON number, an envelope with no provenance marker, a status value with
 * no reason companion) and throws with a precise path when it finds any.
 * Every check here is structural, on strings and types, never on magnitudes.
 *
 * Envelope rules implemented from spec section 8 and architecture P2.6:
 * - every numeric leaf is an envelope: value plus unit, plus scale when the
 *   value is a fixed-scale decimal string;
 * - value is either an integer JSON number (cents, counts) or a fixed-scale
 *   decimal string (rates, multiples, ratios, months); floats are banned;
 * - unit is one of the eleven vocabulary words;
 * - provenance: external observations carry source and source_timestamp,
 *   derived and generated values carry provenance (with asof), band values
 *   carry citation and band; every envelope carries at least one marker;
 * - status companions: unit status with value not_applicable or no_solution
 *   requires a non-empty reason string on the parent object.
 */

/** Unit vocabulary from spec section 8. */
export type Unit =
  | "usd_minor_units"
  | "percent"
  | "percent_annual"
  | "multiple"
  | "ratio"
  | "bp"
  | "months"
  | "count"
  | "date"
  | "sha256_hex"
  | "status";

export const UNITS: readonly Unit[] = [
  "usd_minor_units",
  "percent",
  "percent_annual",
  "multiple",
  "ratio",
  "bp",
  "months",
  "count",
  "date",
  "sha256_hex",
  "status",
];

/** Units whose value is a formatted string rather than a number. */
const STRING_UNITS: ReadonlySet<string> = new Set(["date", "sha256_hex", "status"]);

/** Status values that must carry a reason companion on the parent object. */
const REASON_REQUIRED_STATUSES: ReadonlySet<string> = new Set([
  "not_applicable",
  "no_solution",
]);

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;
const INTEGER_STRING = /^-?\d+$/;
const DECIMAL_STRING = /^-?\d+\.\d+$/;

/** A published numeric leaf: value plus unit plus provenance class keys. */
export interface Envelope {
  readonly value: string | number;
  readonly unit: Unit;
  readonly scale?: number;
  readonly asof?: string;
  readonly provenance?: string;
  readonly source?: string;
  readonly source_timestamp?: string;
  readonly citation?: string;
  readonly band?: string;
}

/** Committed base-rate snapshot block (architecture P2.6 rates). */
export interface RatesBlock {
  readonly series: string;
  readonly label: string;
  readonly observation_date: string;
  readonly base_rate: Envelope;
  readonly source: string;
  readonly source_timestamp: string;
  readonly manifest_hashes: Readonly<Record<string, string>>;
}

/** One stratum of the generator manifest: count plus sorted member ids. */
export interface Stratum {
  readonly count: Envelope;
  readonly members: readonly string[];
}

/** Book header: identity, provenance, and coverage of the committed book. */
export interface BookMeta {
  readonly book_sha256: Envelope;
  readonly currency: string;
  readonly generator_version: string;
  readonly rounding_mode: string;
  readonly seed: Envelope;
  readonly strata_manifest: Readonly<Record<string, Stratum>>;
}

/** Borrower inputs: every configured value is an envelope. */
export interface BorrowerInputs {
  readonly [key: string]: Envelope;
}

/** Revenue and CFADS paths, one envelope per period. */
export interface BorrowerPaths {
  readonly revenue_cents: readonly Envelope[];
  readonly cfads_cents: readonly Envelope[];
}

/** Published pricing outputs plus the band excursion flag. */
export interface Pricing {
  readonly all_in_rate: Envelope;
  readonly base_rate: Envelope;
  readonly shocked_all_in_rate: Envelope;
  readonly spread_pct: Envelope;
  readonly band_excursion: string;
}

/** Sizing outputs: status envelope, facility, binding constraint, floors. */
export interface Sizing {
  readonly status: Envelope;
  readonly facility_cents: Envelope;
  readonly binding_constraint: string;
  readonly post_draw_runway_months: Envelope;
  readonly min_dscr: Envelope;
  readonly reason?: string;
}

/** One schedule row: every field is an envelope (date, count, cents). */
export interface ScheduleRow {
  readonly [field: string]: Envelope;
}

/** A structure schedule with published rows. */
export interface ScheduleWithRows {
  readonly rows: readonly ScheduleRow[];
}

/** A structure schedule marked not applicable, with its reason. */
export interface ScheduleNotApplicable {
  readonly status: Envelope;
  readonly reason: string;
}

export type Schedule = ScheduleWithRows | ScheduleNotApplicable;

/** One committed borrower record. */
export interface Borrower {
  readonly borrower_id: string;
  readonly lender_type: string;
  readonly structure: string;
  readonly pre_revenue: boolean;
  readonly inputs: BorrowerInputs;
  readonly paths: BorrowerPaths;
  readonly pricing: Pricing;
  readonly sizing: Sizing;
  readonly schedule: Schedule;
}

/** The canonical book.json document. */
export interface Book {
  readonly meta: BookMeta;
  readonly rates: RatesBlock;
  readonly borrowers: readonly Borrower[];
}

/**
 * payloads/build_meta.json. The only surface allowed to carry the wall
 * clock and the git SHA, because it sits outside the byte identity tree.
 */
export interface BuildMeta {
  readonly built_at_utc: string;
  readonly git_sha: string;
  readonly python_version: string;
}

function fail(path: string, message: string): never {
  throw new Error(`payload drift at ${path}: ${message}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireKey(
  record: Record<string, unknown>,
  key: string,
  path: string,
): unknown {
  if (!(key in record)) {
    fail(path, `missing required key "${key}"`);
  }
  return record[key];
}

function requireString(value: unknown, path: string): string {
  if (typeof value !== "string" || value.length === 0) {
    fail(path, "expected a non-empty string");
  }
  return value;
}

function requireBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") {
    fail(path, "expected a boolean");
  }
  return value;
}

function requireArray(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) {
    fail(path, "expected an array");
  }
  return value;
}

/** Assert that a node looks like an envelope at all (value plus unit). */
function requireEnvelopeShape(node: unknown, path: string): Record<string, unknown> {
  if (!isRecord(node)) {
    fail(path, "expected an envelope object with value and unit");
  }
  if (!("value" in node)) {
    fail(path, "envelope is missing the required key \"value\"");
  }
  if (!("unit" in node)) {
    fail(path, "envelope is missing the required key \"unit\"");
  }
  return node;
}

/**
 * Validate one envelope's leaves: unit vocabulary, value type, scale
 * agreement, provenance marker. String checks only, no magnitudes.
 */
function validateEnvelopeLeaves(node: Record<string, unknown>, path: string): void {
  const unit = node.unit;
  if (typeof unit !== "string" || !UNITS.includes(unit as Unit)) {
    fail(path, `unit ${JSON.stringify(unit)} is not in the spec section 8 vocabulary`);
  }
  const value = node.value;
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) {
      fail(path, `value ${value} is a float; only integer JSON numbers and fixed-scale decimal strings are allowed`);
    }
    if (STRING_UNITS.has(unit)) {
      fail(path, `unit "${unit}" requires a string value`);
    }
    if ("scale" in node) {
      fail(path, "integer values must not carry a scale");
    }
  } else if (typeof value === "string") {
    if (STRING_UNITS.has(unit)) {
      if ("scale" in node) {
        fail(path, `unit "${unit}" must not carry a scale`);
      }
      if (unit === "date" && !ISO_DATE.test(value)) {
        fail(path, `date value ${JSON.stringify(value)} is not an ISO date (YYYY-MM-DD)`);
      }
      if (unit === "sha256_hex" && !SHA256_HEX.test(value)) {
        fail(path, "sha256_hex value is not a 64 character lowercase hex digest");
      }
    } else if (INTEGER_STRING.test(value)) {
      if ("scale" in node) {
        fail(path, "integer string values must not carry a scale");
      }
    } else if (DECIMAL_STRING.test(value)) {
      const scale = node.scale;
      if (typeof scale !== "number" || !Number.isSafeInteger(scale) || scale < 0) {
        fail(path, "decimal string value requires a non-negative integer scale");
      }
      const fraction = value.split(".")[1] ?? "";
      if (fraction.length !== scale) {
        fail(path, `scale ${scale} does not match the ${fraction.length} decimal places of ${JSON.stringify(value)}`);
      }
    } else {
      fail(path, `value ${JSON.stringify(value)} is not a fixed-scale decimal string`);
    }
  } else {
    fail(path, "value must be a string or an integer JSON number");
  }
  if ("scale" in node && typeof node.scale === "number" && !Number.isSafeInteger(node.scale)) {
    fail(path, "scale must be an integer");
  }
  const hasProvenance = typeof node.provenance === "string";
  const hasSource = typeof node.source === "string";
  const hasCitation = typeof node.citation === "string";
  if (!hasProvenance && !hasSource && !hasCitation) {
    fail(path, "envelope carries no provenance marker (provenance, source, or citation)");
  }
  if ("asof" in node) {
    const asof = requireString(node.asof, `${path}.asof`);
    if (!ISO_DATE.test(asof)) {
      fail(`${path}.asof`, `asof ${JSON.stringify(asof)} is not an ISO date (YYYY-MM-DD)`);
    }
  }
  if ("source_timestamp" in node) {
    const stamp = requireString(node.source_timestamp, `${path}.source_timestamp`);
    if (!ISO_DATE.test(stamp)) {
      fail(`${path}.source_timestamp`, `source_timestamp ${JSON.stringify(stamp)} is not an ISO date (YYYY-MM-DD)`);
    }
  }
  if ("citation" in node) {
    requireString(node.citation, `${path}.citation`);
  }
  if ("band" in node) {
    requireString(node.band, `${path}.band`);
  }
}

/**
 * Generic pass over the whole tree: every object with a value key must be
 * a validated envelope, every JSON number must be an integer, and status
 * values that require a reason must find one on their parent object.
 */
function walkShape(node: unknown, path: string): void {
  if (Array.isArray(node)) {
    node.forEach((item, index) => walkShape(item, `${path}[${index}]`));
    return;
  }
  if (isRecord(node)) {
    if ("value" in node || "unit" in node) {
      const envelope = requireEnvelopeShape(node, path);
      validateEnvelopeLeaves(envelope, path);
      // Sweep the envelope's own companion keys so no float hides there.
      for (const [key, sibling] of Object.entries(envelope)) {
        if (key === "value" || key === "scale") {
          continue;
        }
        walkShape(sibling, `${path}.${key}`);
      }
      return;
    }
    for (const [key, value] of Object.entries(node)) {
      // Status companions live on the parent object: a child status value
      // of not_applicable or no_solution needs this object's reason.
      if (
        isRecord(value) &&
        value.unit === "status" &&
        typeof value.value === "string" &&
        REASON_REQUIRED_STATUSES.has(value.value)
      ) {
        const reason = node.reason;
        if (typeof reason !== "string" || reason.length === 0) {
          fail(`${path}.${key}`, `status "${value.value}" requires a non-empty reason companion on the parent object`);
        }
      }
      walkShape(value, `${path}.${key}`);
    }
    return;
  }
  if (typeof node === "number" && !Number.isSafeInteger(node)) {
    fail(path, `float ${node} found; payload numbers are integers or fixed-scale decimal strings`);
  }
}

function assertRates(node: unknown, path: string): void {
  const rates = isRecord(node) ? node : fail(path, "expected an object");
  requireString(requireKey(rates, "series", path), `${path}.series`);
  requireString(requireKey(rates, "label", path), `${path}.label`);
  const observationDate = requireString(
    requireKey(rates, "observation_date", path),
    `${path}.observation_date`,
  );
  if (!ISO_DATE.test(observationDate)) {
    fail(`${path}.observation_date`, `observation_date ${JSON.stringify(observationDate)} is not an ISO date (YYYY-MM-DD)`);
  }
  requireString(requireKey(rates, "source", path), `${path}.source`);
  requireString(requireKey(rates, "source_timestamp", path), `${path}.source_timestamp`);
  const baseRate = requireEnvelopeShape(requireKey(rates, "base_rate", path), `${path}.base_rate`);
  validateEnvelopeLeaves(baseRate, `${path}.base_rate`);
  if (baseRate.unit !== "percent_annual") {
    fail(`${path}.base_rate.unit`, `expected "percent_annual", found ${JSON.stringify(baseRate.unit)}`);
  }
  const hashes = requireKey(rates, "manifest_hashes", path);
  if (!isRecord(hashes)) {
    fail(`${path}.manifest_hashes`, "expected an object of file name to sha256 string");
  }
  for (const [file, digest] of Object.entries(hashes)) {
    requireString(digest, `${path}.manifest_hashes.${file}`);
  }
}

function assertMeta(node: unknown, path: string): void {
  const meta = isRecord(node) ? node : fail(path, "expected an object");
  const bookSha = requireEnvelopeShape(requireKey(meta, "book_sha256", path), `${path}.book_sha256`);
  validateEnvelopeLeaves(bookSha, `${path}.book_sha256`);
  if (bookSha.unit !== "sha256_hex") {
    fail(`${path}.book_sha256.unit`, `expected "sha256_hex", found ${JSON.stringify(bookSha.unit)}`);
  }
  requireString(requireKey(meta, "currency", path), `${path}.currency`);
  requireString(requireKey(meta, "generator_version", path), `${path}.generator_version`);
  requireString(requireKey(meta, "rounding_mode", path), `${path}.rounding_mode`);
  const seed = requireEnvelopeShape(requireKey(meta, "seed", path), `${path}.seed`);
  validateEnvelopeLeaves(seed, `${path}.seed`);
  if (seed.unit !== "count") {
    fail(`${path}.seed.unit`, `expected "count", found ${JSON.stringify(seed.unit)}`);
  }
  const strata = requireKey(meta, "strata_manifest", path);
  if (!isRecord(strata) || Object.keys(strata).length === 0) {
    fail(`${path}.strata_manifest`, "expected a non-empty object of strata");
  }
  for (const [stratum, entry] of Object.entries(strata)) {
    const entryPath = `${path}.strata_manifest.${stratum}`;
    if (!isRecord(entry)) {
      fail(entryPath, "expected an object with count and members");
    }
    const count = requireEnvelopeShape(requireKey(entry, "count", entryPath), `${entryPath}.count`);
    validateEnvelopeLeaves(count, `${entryPath}.count`);
    if (count.unit !== "count") {
      fail(`${entryPath}.count.unit`, `expected "count", found ${JSON.stringify(count.unit)}`);
    }
    const members = requireArray(requireKey(entry, "members", entryPath), `${entryPath}.members`);
    members.forEach((member, index) =>
      requireString(member, `${entryPath}.members[${index}]`),
    );
  }
}

function assertBorrower(node: unknown, path: string): void {
  const borrower = isRecord(node) ? node : fail(path, "expected an object");
  requireString(requireKey(borrower, "borrower_id", path), `${path}.borrower_id`);
  requireString(requireKey(borrower, "lender_type", path), `${path}.lender_type`);
  requireString(requireKey(borrower, "structure", path), `${path}.structure`);
  requireBoolean(requireKey(borrower, "pre_revenue", path), `${path}.pre_revenue`);

  const inputs = requireKey(borrower, "inputs", path);
  if (!isRecord(inputs) || Object.keys(inputs).length === 0) {
    fail(`${path}.inputs`, "expected a non-empty object of input envelopes");
  }
  for (const [name, input] of Object.entries(inputs)) {
    requireEnvelopeShape(input, `${path}.inputs.${name}`);
  }

  const paths = requireKey(borrower, "paths", path);
  if (!isRecord(paths)) {
    fail(`${path}.paths`, "expected an object with revenue_cents and cfads_cents");
  }
  for (const listName of ["revenue_cents", "cfads_cents"] as const) {
    const list = requireArray(requireKey(paths, listName, `${path}.paths`), `${path}.paths.${listName}`);
    if (list.length === 0) {
      fail(`${path}.paths.${listName}`, "expected at least one period");
    }
    list.forEach((item, index) => {
      requireEnvelopeShape(item, `${path}.paths.${listName}[${index}]`);
    });
  }

  const pricing = requireKey(borrower, "pricing", path);
  if (!isRecord(pricing)) {
    fail(`${path}.pricing`, "expected an object of pricing envelopes");
  }
  for (const name of ["all_in_rate", "base_rate", "shocked_all_in_rate", "spread_pct"] as const) {
    requireEnvelopeShape(requireKey(pricing, name, `${path}.pricing`), `${path}.pricing.${name}`);
  }
  requireString(requireKey(pricing, "band_excursion", `${path}.pricing`), `${path}.pricing.band_excursion`);

  const sizing = requireKey(borrower, "sizing", path);
  if (!isRecord(sizing)) {
    fail(`${path}.sizing`, "expected an object");
  }
  requireString(requireKey(sizing, "binding_constraint", `${path}.sizing`), `${path}.sizing.binding_constraint`);
  if ("reason" in sizing) {
    requireString(sizing.reason, `${path}.sizing.reason`);
  }
  const sizingStatus = requireEnvelopeShape(requireKey(sizing, "status", `${path}.sizing`), `${path}.sizing.status`);
  const statusValue = sizingStatus.value;
  if (statusValue === "feasible") {
    // A feasible solve publishes its facility, runway, and DSCR floor.
    for (const name of ["facility_cents", "post_draw_runway_months", "min_dscr"] as const) {
      requireEnvelopeShape(requireKey(sizing, name, `${path}.sizing`), `${path}.sizing.${name}`);
    }
  } else if (statusValue === "infeasible") {
    // An infeasible solve publishes no facility figures, only the
    // constraint label; requiring them here would be drift the other way.
  } else {
    fail(`${path}.sizing.status`, `unknown sizing status ${JSON.stringify(statusValue)}; expected "feasible" or "infeasible"`);
  }

  const schedule = requireKey(borrower, "schedule", path);
  if (!isRecord(schedule)) {
    fail(`${path}.schedule`, "expected either rows or a status envelope with reason");
  }
  if ("rows" in schedule) {
    const rows = requireArray(schedule.rows, `${path}.schedule.rows`);
    rows.forEach((row, index) => {
      const rowPath = `${path}.schedule.rows[${index}]`;
      if (!isRecord(row) || Object.keys(row).length === 0) {
        fail(rowPath, "expected an object of row envelopes");
      }
      for (const [field, cell] of Object.entries(row)) {
        requireEnvelopeShape(cell, `${rowPath}.${field}`);
      }
    });
  } else {
    requireEnvelopeShape(requireKey(schedule, "status", `${path}.schedule`), `${path}.schedule.status`);
    requireString(requireKey(schedule, "reason", `${path}.schedule`), `${path}.schedule.reason`);
  }
}

/** Assert the full book.json shape; throws with a precise path on drift. */
export function assertBookShape(data: unknown): asserts data is Book {
  const root = isRecord(data) ? data : fail("book", "expected a JSON object");
  assertRates(requireKey(root, "rates", "book"), "book.rates");
  assertMeta(requireKey(root, "meta", "book"), "book.meta");
  const borrowers = requireArray(requireKey(root, "borrowers", "book"), "book.borrowers");
  if (borrowers.length === 0) {
    fail("book.borrowers", "expected at least one borrower");
  }
  borrowers.forEach((borrower, index) => assertBorrower(borrower, `book.borrowers[${index}]`));
  walkShape(root, "book");
}

/** Validate and narrow a parsed book.json import. */
export function loadBook(raw: unknown): Book {
  assertBookShape(raw);
  return raw;
}

/** Assert the build_meta.json shape; throws with a precise path on drift. */
export function assertBuildMetaShape(data: unknown): asserts data is BuildMeta {
  const root = isRecord(data) ? data : fail("build_meta", "expected a JSON object");
  for (const key of ["built_at_utc", "git_sha", "python_version"] as const) {
    requireString(requireKey(root, key, "build_meta"), `build_meta.${key}`);
  }
  walkShape(root, "build_meta");
}

/** Validate and narrow a parsed build_meta.json import. */
export function loadBuildMeta(raw: unknown): BuildMeta {
  assertBuildMetaShape(raw);
  return raw;
}
