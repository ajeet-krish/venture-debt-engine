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

/** Status vocabulary from spec section 6.4, including the phase 4 "breach". */
export type StatusValue =
  | "feasible"
  | "infeasible"
  | "not_applicable"
  | "not_granted"
  | "shortfall"
  | "no_solution"
  | "breach";

export const STATUS_VALUES: readonly StatusValue[] = [
  "feasible",
  "infeasible",
  "not_applicable",
  "not_granted",
  "shortfall",
  "no_solution",
  "breach",
];

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
  /** Committed dashboard counts, published by the phase 4 payload build. */
  readonly book_summary?: BookSummary;
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

/** Sizing outputs: status envelope, band cap, facility, constraint, floors. */
export interface Sizing {
  readonly status: Envelope;
  /** Published for every borrower, feasible or not (spec section 12). */
  readonly band_cap?: Envelope;
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

/** A structure schedule with published rows (kind tagged: amortizing or rbf). */
export interface ScheduleWithRows {
  readonly kind?: string;
  readonly rows: readonly ScheduleRow[];
}

/** A structure schedule marked not applicable, with its reason. */
export interface ScheduleNotApplicable {
  readonly status: Envelope;
  readonly reason: string;
}

export type Schedule = ScheduleWithRows | ScheduleNotApplicable;

/** One dated lender cash flow from the published returns block. */
export interface ReturnsCashFlow {
  readonly date: Envelope;
  readonly cash_flow: Envelope;
}

/** The feasible returns block: every figure plus the dated cash flows. */
export interface ReturnsFeasible {
  readonly status: Envelope;
  readonly lender_irr: Envelope;
  readonly moic: Envelope;
  readonly total_receipts: Envelope;
  readonly closing_fee: Envelope;
  readonly warrant: Envelope;
  readonly cash_flows: readonly ReturnsCashFlow[];
}

/** The infeasible returns block: status plus reason, no figures at all. */
export interface ReturnsNotApplicable {
  readonly status: Envelope;
  readonly reason: string;
}

export type ReturnsBlock = ReturnsFeasible | ReturnsNotApplicable;

/* Phase 4 blocks (architecture P4.1, P4.2, P4.3, and P4.6).
 *
 * book.json gains three additive blocks once the phase 4 payload build
 * lands: borrower.scenarios (base and downside headline figures, their
 * published deltas, and the rate shock block), borrower.covenants (the
 * four covenant summaries per scenario plus the borrower level first
 * breach month), and meta.book_summary (the committed dashboard counts).
 * All three stay optional here so a payload without them degrades to the
 * honest placeholder instead of a crash, and each is validated for shape
 * when present: keys, units, scales, and status companions. No magnitude
 * is ever compared.
 */

/** A published delta leaf: its envelope plus the pipeline's own label. */
export interface DeltaLeaf extends Envelope {
  /**
   * The published delta label on the envelope itself, for example
   * "invariant by construction" or "downside minus base".
   */
  readonly status: string;
}

/** Any published delta: a figure with its label, or a status with reason. */
export type DeltaCell = StatusCell | DeltaLeaf;

/** One scenario's headline figures (P4.2 base or downside block). */
export interface ScenarioFigures {
  readonly lender_irr: Envelope;
  readonly moic: Envelope;
  readonly total_interest: Envelope;
  readonly total_receipts: Envelope;
  readonly closing_fee: Envelope;
  readonly warrant: Envelope;
  readonly warrant_contractual: Envelope;
  /** RBF only: schedule status and residual companions (P4.2). */
  readonly rbf_status?: Envelope;
  readonly rbf_residual?: Envelope;
}

/** An infeasible borrower's scenario block: status plus reason only. */
export interface ScenarioNotApplicable {
  readonly status: Envelope;
  readonly reason: string;
}

export type ScenarioBlock = ScenarioFigures | ScenarioNotApplicable;

/** A rate shock leaf: a figure, or a status cell with its reason (P4.3). */
export type RateShockCell = Envelope | StatusCell;

/** The rate shock block: shocked interest, shocked IRR, delta, sensitivity. */
export interface RateShockBlock {
  readonly shocked_all_in_rate: Envelope;
  readonly shocked_total_interest: Envelope;
  readonly shocked_lender_irr: RateShockCell;
  readonly irr_delta_bp: RateShockCell;
  readonly sensitivity_per_100bp: RateShockCell;
}

/** Every published headline delta, downside minus base (P4.2). */
export interface ScenarioDeltas {
  readonly lender_irr_delta: DeltaCell;
  readonly moic_delta: DeltaCell;
  readonly total_interest_delta: DeltaCell;
  readonly total_receipts_delta: DeltaCell;
  readonly warrant_delta: DeltaCell;
}

/** The borrower's scenarios block: two scenarios, deltas, rate shock. */
export interface ScenariosFiguresBlock {
  readonly base: ScenarioBlock;
  readonly downside: ScenarioBlock;
  readonly deltas: ScenarioDeltas;
  readonly rate_shock: RateShockBlock;
}

/**
 * The scenarios block: the figure shape for a feasible borrower, or the
 * not_applicable shape for an infeasible one (published as one status
 * cell with its reason, never fabricated zeros).
 */
export type ScenariosBlock = ScenariosFiguresBlock | ScenarioNotApplicable;

/** First breach cell: status with value iff breach, reason iff not applicable. */
export interface FirstBreachCell {
  readonly status: Envelope;
  readonly value?: Envelope;
  readonly reason?: string;
}

/** A first breach published as a bare months leaf or a bare status leaf. */
export type FirstBreach = FirstBreachCell | Envelope;

/** One covenant's summary: spec leaves, status, headroom, first breach. */
export interface CovenantBlock {
  readonly name: string;
  readonly kind: string;
  readonly limit: Envelope;
  readonly status: Envelope;
  readonly reason?: string;
  readonly min_headroom?: Envelope;
  readonly first_breach_month?: FirstBreach;
}

/** The four pinned covenants under one scenario (P4.1). */
export interface CovenantBlocks {
  readonly net_leverage: CovenantBlock;
  readonly interest_cover: CovenantBlock;
  readonly min_cash: CovenantBlock;
  readonly dscr_floor: CovenantBlock;
  readonly reason?: string;
}

/**
 * One scenario's covenant side: the four blocks for a feasible borrower,
 * or a not_applicable status cell with its reason for an infeasible one.
 */
export type CovenantScenario = CovenantBlocks | ScenarioNotApplicable;

/** The borrower's covenants block plus its borrower level first breach. */
export interface CovenantsBlock {
  readonly base: CovenantScenario;
  readonly downside: CovenantScenario;
  readonly first_breach_month: FirstBreach;
  readonly reason?: string;
}

/** A committed count: a bare integer or a count envelope, both published. */
export type CountValue = number | Envelope;

/** One published capacity bin: edges and count, all committed values. */
export interface CapacityBin {
  readonly bin_low: CountValue;
  readonly bin_high: CountValue;
  readonly count: CountValue;
}

/** Published binding constraint mix: counts nested or flat (P4.6). */
export interface BindingConstraintMix {
  readonly counts?: Readonly<Record<string, CountValue>>;
  readonly feasible_count?: CountValue;
  readonly infeasible_count?: CountValue;
  readonly runway?: CountValue;
  readonly dscr?: CountValue;
  readonly band_cap?: CountValue;
}

/** meta.book_summary: every dashboard number, computed by the pipeline. */
export interface BookSummary {
  readonly capacity_distribution?: readonly CapacityBin[];
  readonly binding_constraint_mix?: BindingConstraintMix;
}

/** True when a published block is a status cell with its reason companion. */
export function isNotApplicableShape(block: ScenarioBlock): block is ScenarioNotApplicable;
export function isNotApplicableShape(block: ScenariosBlock): block is ScenarioNotApplicable;
export function isNotApplicableShape(block: CovenantScenario): block is ScenarioNotApplicable;
export function isNotApplicableShape(block: unknown): boolean;
export function isNotApplicableShape(block: unknown): boolean {
  return (
    typeof block === "object" &&
    block !== null &&
    "status" in block &&
    "reason" in block
  );
}

/** True when a first breach leaf publishes a status cell rather than a leaf. */
export function isFirstBreachCell(leaf: FirstBreach): leaf is FirstBreachCell {
  return "status" in leaf && !("unit" in leaf);
}

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
  readonly returns: ReturnsBlock;
  /** Phase 4 covenant summaries, present once the payload build lands. */
  readonly covenants?: CovenantsBlock;
  /** Phase 4 scenario headlines, present once the payload build lands. */
  readonly scenarios?: ScenariosBlock;
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
  if (unit === "status") {
    if (typeof value !== "string" || !STATUS_VALUES.includes(value as StatusValue)) {
      fail(path, `status value ${JSON.stringify(value)} is not in the section 6.4 vocabulary`);
    }
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
        // snapshot.py accepts the companion inside the envelope or in its
        // containing object, so both locations satisfy this check.
        const inside =
          typeof value.reason === "string" && value.reason.length > 0;
        const onParent =
          typeof node.reason === "string" && node.reason.length > 0;
        if (!inside && !onParent) {
          fail(`${path}.${key}`, `status "${value.value}" requires a non-empty reason companion on the envelope or the parent object`);
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
  if ("book_summary" in meta) {
    assertBookSummary(meta.book_summary, `${path}.book_summary`);
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
  if ("band_cap" in sizing) {
    // Published for every borrower, feasible or not; validated when present
    // so a payload that drops it degrades to an absent figure, not a crash.
    requireEnvelopeShape(requireKey(sizing, "band_cap", `${path}.sizing`), `${path}.sizing.band_cap`);
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

  // Returns block (architecture P3.6): feasible publishes figures plus the
  // dated cash flows; not_applicable publishes status plus reason only.
  const returns = requireKey(borrower, "returns", path);
  if (!isRecord(returns)) {
    fail(`${path}.returns`, "expected an object");
  }
  const returnsStatus = requireEnvelopeShape(
    requireKey(returns, "status", `${path}.returns`),
    `${path}.returns.status`,
  );
  if (returnsStatus.unit !== "status") {
    fail(`${path}.returns.status`, `expected "status" unit, found ${JSON.stringify(returnsStatus.unit)}`);
  }
  if (returnsStatus.value === "feasible") {
    for (const name of ["lender_irr", "moic", "total_receipts", "closing_fee", "warrant"] as const) {
      requireEnvelopeShape(requireKey(returns, name, `${path}.returns`), `${path}.returns.${name}`);
    }
    const flows = requireArray(
      requireKey(returns, "cash_flows", `${path}.returns`),
      `${path}.returns.cash_flows`,
    );
    if (flows.length === 0) {
      fail(`${path}.returns.cash_flows`, "expected at least one dated cash flow");
    }
    flows.forEach((flow, index) => {
      const flowPath = `${path}.returns.cash_flows[${index}]`;
      if (!isRecord(flow)) {
        fail(flowPath, "expected an object with date and cash_flow");
      }
      requireEnvelopeShape(requireKey(flow, "date", flowPath), `${flowPath}.date`);
      requireEnvelopeShape(requireKey(flow, "cash_flow", flowPath), `${flowPath}.cash_flow`);
    });
  } else if (returnsStatus.value === "not_applicable") {
    requireString(requireKey(returns, "reason", `${path}.returns`), `${path}.returns.reason`);
  } else {
    fail(
      `${path}.returns.status`,
      `unknown returns status ${JSON.stringify(returnsStatus.value)}; expected "feasible" or "not_applicable"`,
    );
  }

  // Phase 4 blocks (P4.1, P4.2): validated when present, absent until the
  // payload build publishes them.
  if ("covenants" in borrower) {
    assertCovenants(borrower.covenants, `${path}.covenants`);
  }
  if ("scenarios" in borrower) {
    assertScenarios(borrower.scenarios, `${path}.scenarios`);
  }
}

/* Phase 4 shape assertions (architecture P4.1, P4.2, P4.3, P4.6).
 *
 * Structural only: keys, units, scales, and status companions, never
 * magnitudes. Each block is optional at its owner, so a payload that has
 * not published one still loads and the route keeps its placeholder.
 */

function assertStatusEnvelope(node: unknown, path: string): Record<string, unknown> {
  const envelope = requireEnvelopeShape(node, path);
  if (envelope.unit !== "status") {
    fail(`${path}.unit`, `expected "status", found ${JSON.stringify(envelope.unit)}`);
  }
  validateEnvelopeLeaves(envelope, path);
  return envelope;
}

/** A status cell: status envelope plus the required reason companion. */
function assertStatusCellShape(node: unknown, path: string): void {
  if (!isRecord(node)) {
    fail(path, "expected a status cell with a status envelope and a reason");
  }
  assertStatusEnvelope(requireKey(node, "status", path), `${path}.status`);
  requireString(requireKey(node, "reason", path), `${path}.reason`);
}

/** One delta leaf: a figure carrying its pipeline label, or a status cell. */
function assertDeltaCell(node: unknown, path: string): void {
  if (
    isRecord(node) &&
    "status" in node &&
    typeof node.status === "object" &&
    node.status !== null &&
    "reason" in node
  ) {
    assertStatusCellShape(node, path);
    return;
  }
  const leaf = requireEnvelopeShape(node, path);
  validateEnvelopeLeaves(leaf, path);
  requireString(requireKey(leaf, "status", path), `${path}.status`);
}

const SCENARIO_FIGURE_KEYS = [
  "lender_irr",
  "moic",
  "total_interest",
  "total_receipts",
  "closing_fee",
  "warrant",
  "warrant_contractual",
] as const;

function assertScenarioBlock(node: unknown, path: string): void {
  const block = isRecord(node) ? node : fail(path, "expected an object");
  if ("status" in block && "reason" in block) {
    assertStatusEnvelope(block.status, `${path}.status`);
    requireString(block.reason, `${path}.reason`);
    return;
  }
  for (const key of SCENARIO_FIGURE_KEYS) {
    const leaf = requireEnvelopeShape(requireKey(block, key, path), `${path}.${key}`);
    validateEnvelopeLeaves(leaf, `${path}.${key}`);
  }
  for (const key of ["rbf_status", "rbf_residual"] as const) {
    if (key in block) {
      const leaf = requireEnvelopeShape(block[key], `${path}.${key}`);
      validateEnvelopeLeaves(leaf, `${path}.${key}`);
    }
  }
}

function assertRateShockCell(node: unknown, path: string, expectedUnit?: Unit): void {
  if (isRecord(node) && "status" in node && "reason" in node) {
    assertStatusCellShape(node, path);
    return;
  }
  const leaf = requireEnvelopeShape(node, path);
  validateEnvelopeLeaves(leaf, path);
  if (expectedUnit !== undefined && leaf.unit !== expectedUnit) {
    fail(`${path}.unit`, `expected "${expectedUnit}", found ${JSON.stringify(leaf.unit)}`);
  }
}

function assertRateShock(node: unknown, path: string): void {
  const block = isRecord(node) ? node : fail(path, "expected an object");
  for (const key of ["shocked_all_in_rate", "shocked_total_interest"] as const) {
    const leaf = requireEnvelopeShape(requireKey(block, key, path), `${path}.${key}`);
    validateEnvelopeLeaves(leaf, `${path}.${key}`);
  }
  assertRateShockCell(
    requireKey(block, "shocked_lender_irr", path),
    `${path}.shocked_lender_irr`,
  );
  assertRateShockCell(requireKey(block, "irr_delta_bp", path), `${path}.irr_delta_bp`, "bp");
  assertRateShockCell(
    requireKey(block, "sensitivity_per_100bp", path),
    `${path}.sensitivity_per_100bp`,
    "bp",
  );
}

const DELTA_KEYS = [
  "lender_irr_delta",
  "moic_delta",
  "total_interest_delta",
  "total_receipts_delta",
  "warrant_delta",
] as const;

function assertScenarios(node: unknown, path: string): void {
  const block = isRecord(node) ? node : fail(path, "expected an object");
  if (isNotApplicableShape(block)) {
    // Infeasible borrowers publish one status cell with its reason.
    assertStatusCellShape(block, path);
    return;
  }
  assertScenarioBlock(requireKey(block, "base", path), `${path}.base`);
  assertScenarioBlock(requireKey(block, "downside", path), `${path}.downside`);
  const deltas = requireKey(block, "deltas", path);
  if (!isRecord(deltas)) {
    fail(`${path}.deltas`, "expected an object of published delta leaves");
  }
  for (const key of DELTA_KEYS) {
    assertDeltaCell(requireKey(deltas, key, `${path}.deltas`), `${path}.deltas.${key}`);
  }
  assertRateShock(requireKey(block, "rate_shock", path), `${path}.rate_shock`);
}

function assertFirstBreach(node: unknown, path: string): void {
  if (isRecord(node) && "status" in node && !("unit" in node)) {
    const status = assertStatusEnvelope(node.status, `${path}.status`);
    if ("value" in node) {
      const value = requireEnvelopeShape(node.value, `${path}.value`);
      if (value.unit !== "months") {
        fail(`${path}.value.unit`, `expected "months", found ${JSON.stringify(value.unit)}`);
      }
      validateEnvelopeLeaves(value, `${path}.value`);
    } else if (status.value === "breach") {
      fail(path, 'status "breach" requires the months value leaf');
    }
    if ("reason" in node) {
      requireString(node.reason, `${path}.reason`);
    }
    return;
  }
  const leaf = requireEnvelopeShape(node, path);
  validateEnvelopeLeaves(leaf, path);
  if (leaf.unit !== "months" && leaf.unit !== "status") {
    fail(`${path}.unit`, `expected "months" or "status", found ${JSON.stringify(leaf.unit)}`);
  }
}

function assertCovenantBlock(node: unknown, path: string): void {
  const block = isRecord(node) ? node : fail(path, "expected an object");
  requireString(requireKey(block, "name", path), `${path}.name`);
  requireString(requireKey(block, "kind", path), `${path}.kind`);
  const limit = requireEnvelopeShape(requireKey(block, "limit", path), `${path}.limit`);
  validateEnvelopeLeaves(limit, `${path}.limit`);
  assertStatusEnvelope(requireKey(block, "status", path), `${path}.status`);
  if ("reason" in block) {
    requireString(block.reason, `${path}.reason`);
  }
  if ("min_headroom" in block) {
    const headroom = requireEnvelopeShape(block.min_headroom, `${path}.min_headroom`);
    validateEnvelopeLeaves(headroom, `${path}.min_headroom`);
  }
  if ("first_breach_month" in block) {
    assertFirstBreach(block.first_breach_month, `${path}.first_breach_month`);
  }
}

const COVENANT_NAMES = ["net_leverage", "interest_cover", "min_cash", "dscr_floor"] as const;

function assertCovenants(node: unknown, path: string): void {
  const block = isRecord(node) ? node : fail(path, "expected an object");
  for (const scenario of ["base", "downside"] as const) {
    const side = requireKey(block, scenario, path);
    if (!isRecord(side)) {
      fail(`${path}.${scenario}`, "expected the four covenant blocks or a status cell");
    }
    if (isNotApplicableShape(side)) {
      // Infeasible borrowers publish one status cell with its reason.
      assertStatusCellShape(side, `${path}.${scenario}`);
      continue;
    }
    for (const name of COVENANT_NAMES) {
      assertCovenantBlock(
        requireKey(side, name, `${path}.${scenario}`),
        `${path}.${scenario}.${name}`,
      );
    }
    if ("reason" in side) {
      requireString(side.reason, `${path}.${scenario}.reason`);
    }
  }
  assertFirstBreach(
    requireKey(block, "first_breach_month", path),
    `${path}.first_breach_month`,
  );
  if ("reason" in block) {
    requireString(block.reason, `${path}.reason`);
  }
}

/** A committed count: a bare integer or a validated count envelope. */
function assertCountValue(node: unknown, path: string): void {
  if (typeof node === "number") {
    if (!Number.isSafeInteger(node)) {
      fail(path, `count ${node} is a float; counts are integers or count envelopes`);
    }
    return;
  }
  const leaf = requireEnvelopeShape(node, path);
  validateEnvelopeLeaves(leaf, path);
}

function assertBookSummary(node: unknown, path: string): void {
  const summary = isRecord(node) ? node : fail(path, "expected an object");
  if ("capacity_distribution" in summary) {
    const bins = requireArray(summary.capacity_distribution, `${path}.capacity_distribution`);
    bins.forEach((bin, index) => {
      const binPath = `${path}.capacity_distribution[${index}]`;
      if (!isRecord(bin)) {
        fail(binPath, "expected an object with bin_low, bin_high, and count");
      }
      for (const key of ["bin_low", "bin_high", "count"] as const) {
        assertCountValue(requireKey(bin, key, binPath), `${binPath}.${key}`);
      }
    });
  }
  if ("binding_constraint_mix" in summary) {
    const mix = summary.binding_constraint_mix;
    if (!isRecord(mix)) {
      fail(`${path}.binding_constraint_mix`, "expected an object of published counts");
    }
    if ("counts" in mix) {
      const counts = mix.counts;
      if (!isRecord(counts)) {
        fail(`${path}.binding_constraint_mix.counts`, "expected an object of counts");
      }
      for (const [key, value] of Object.entries(counts)) {
        assertCountValue(value, `${path}.binding_constraint_mix.counts.${key}`);
      }
    }
    for (const key of ["feasible_count", "infeasible_count", "runway", "dscr", "band_cap"] as const) {
      if (key in mix) {
        assertCountValue(mix[key], `${path}.binding_constraint_mix.${key}`);
      }
    }
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

/* comparison.json (architecture P3.5 and P3.6).
 *
 * Same contract as the book: shape only, never magnitudes. A cell is
 * either a published envelope or a status cell (status envelope plus the
 * reason companion that section 6.4 requires).
 */

/** A cell that publishes a status instead of a figure, with its reason. */
export interface StatusCell {
  readonly status: Envelope;
  readonly reason: string;
}

/** Any comparison cell: a figure envelope or a status cell. */
export type ComparisonCell = Envelope | StatusCell;

/** Terms shared by the three structure rows of one borrower table. */
export interface ComparisonShared {
  readonly close: Envelope;
  readonly base_rate: Envelope;
  readonly all_in_rate: Envelope;
  readonly facility: ComparisonCell;
  readonly closing_fee: ComparisonCell;
  readonly warrant_coverage: Envelope;
}

/** One structure row: spec section 10.6 columns as committed envelopes. */
export interface ComparisonRow {
  readonly structure: string;
  readonly binding_constraint: string;
  readonly band_names: readonly string[];
  readonly facility: ComparisonCell;
  readonly all_in_rate: Envelope;
  readonly term_months: Envelope;
  readonly io_months: ComparisonCell;
  readonly total_interest: ComparisonCell;
  readonly closing_fee: ComparisonCell;
  readonly lender_irr: ComparisonCell;
  readonly moic: ComparisonCell;
  readonly min_dscr: ComparisonCell;
  readonly first_breach_month: FirstBreach;
  readonly warrant_coverage: Envelope;
  readonly total_receipts: ComparisonCell;
}

/** One borrower's three-row comparison table plus its shared terms. */
export interface ComparisonTable {
  readonly borrower_id: string;
  readonly shared: ComparisonShared;
  readonly rows: readonly ComparisonRow[];
}

/** The comparison meta block: asof, book binding hash, featured id. */
export interface ComparisonMeta {
  readonly asof: string;
  readonly book_sha256: Envelope;
  readonly featured_borrower_id: string;
}

/** The canonical comparison.json document. */
export interface Comparison {
  readonly meta: ComparisonMeta;
  readonly tables: readonly ComparisonTable[];
}

/** True when a cell publishes a status plus reason rather than a figure. */
export function isStatusCell(cell: ComparisonCell): cell is StatusCell {
  return "reason" in cell && "status" in cell;
}

function assertComparisonCell(node: unknown, path: string): void {
  if (isRecord(node) && "status" in node && "reason" in node) {
    const status = requireEnvelopeShape(node.status, `${path}.status`);
    if (status.unit !== "status") {
      fail(`${path}.status`, `expected "status" unit, found ${JSON.stringify(status.unit)}`);
    }
    requireString(node.reason, `${path}.reason`);
    return;
  }
  const envelope = requireEnvelopeShape(node, path);
  validateEnvelopeLeaves(envelope, path);
}

function assertComparisonRow(node: unknown, path: string): void {
  const row = isRecord(node) ? node : fail(path, "expected an object");
  requireString(requireKey(row, "structure", path), `${path}.structure`);
  requireString(requireKey(row, "binding_constraint", path), `${path}.binding_constraint`);
  const bands = requireArray(requireKey(row, "band_names", path), `${path}.band_names`);
  bands.forEach((band, index) => requireString(band, `${path}.band_names[${index}]`));
  for (const name of [
    "facility",
    "io_months",
    "total_interest",
    "closing_fee",
    "lender_irr",
    "moic",
    "min_dscr",
    "warrant_coverage",
    "total_receipts",
    "all_in_rate",
    "term_months",
  ] as const) {
    assertComparisonCell(requireKey(row, name, path), `${path}.${name}`);
  }
  const breach = requireKey(row, "first_breach_month", path);
  assertFirstBreach(breach, `${path}.first_breach_month`);
}

/** Assert the full comparison.json shape; throws with a precise path. */
export function assertComparisonShape(data: unknown): asserts data is Comparison {
  const root = isRecord(data) ? data : fail("comparison", "expected a JSON object");
  const metaNode = requireKey(root, "meta", "comparison");
  if (!isRecord(metaNode)) {
    fail("comparison.meta", "expected an object");
  }
  const meta = metaNode;
  const asof = requireString(requireKey(meta, "asof", "comparison.meta"), "comparison.meta.asof");
  if (!ISO_DATE.test(asof)) {
    fail("comparison.meta.asof", `asof ${JSON.stringify(asof)} is not an ISO date (YYYY-MM-DD)`);
  }
  const bookSha = requireEnvelopeShape(
    requireKey(meta, "book_sha256", "comparison.meta"),
    "comparison.meta.book_sha256",
  );
  validateEnvelopeLeaves(bookSha, "comparison.meta.book_sha256");
  if (bookSha.unit !== "sha256_hex") {
    fail("comparison.meta.book_sha256.unit", `expected "sha256_hex", found ${JSON.stringify(bookSha.unit)}`);
  }
  requireString(
    requireKey(meta, "featured_borrower_id", "comparison.meta"),
    "comparison.meta.featured_borrower_id",
  );

  const tables = requireArray(requireKey(root, "tables", "comparison"), "comparison.tables");
  if (tables.length === 0) {
    fail("comparison.tables", "expected at least one borrower table");
  }
  tables.forEach((table, index) => {
    const tablePath = `comparison.tables[${index}]`;
    const entry = isRecord(table) ? table : fail(tablePath, "expected an object");
    requireString(requireKey(entry, "borrower_id", tablePath), `${tablePath}.borrower_id`);
    const shared = requireKey(entry, "shared", tablePath);
    if (!isRecord(shared)) {
      fail(`${tablePath}.shared`, "expected an object");
    }
    for (const name of ["close", "base_rate", "all_in_rate", "facility", "closing_fee", "warrant_coverage"] as const) {
      requireKey(shared, name, `${tablePath}.shared`);
      assertComparisonCell(requireKey(shared, name, `${tablePath}.shared`), `${tablePath}.shared.${name}`);
    }
    const rows = requireArray(requireKey(entry, "rows", tablePath), `${tablePath}.rows`);
    if (rows.length !== 3) {
      fail(`${tablePath}.rows`, `expected the three pinned comparison rows, found ${rows.length}`);
    }
    rows.forEach((row, rowIndex) => assertComparisonRow(row, `${tablePath}.rows[${rowIndex}]`));
  });
  walkShape(root, "comparison");
}

/** Validate and narrow a parsed comparison.json import. */
export function loadComparison(raw: unknown): Comparison {
  assertComparisonShape(raw);
  return raw;
}
