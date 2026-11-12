/**
 * Committed rule-of-thumb band text used by the sizing page.
 *
 * The band name, the rule, and the citation are transcribed from the
 * committed scenario configuration (pipeline/config/scenario.toml,
 * [bands.facility_sizing]) so the site can always render the rule beside a
 * cap figure. The cap VALUE itself is never derived in the browser: it is
 * read from the published sizing.band_cap envelope in the committed
 * payloads, and this module supplies only the rule text and citation.
 */

export interface BandRule {
  /** Committed band identifier as written in the payload vocabulary. */
  readonly band: string;
  /** Plain language statement of the rule, numbers included. */
  readonly rule: string;
  /** Source name plus access date, required beside every band value. */
  readonly citation: string;
}

export const FACILITY_SIZING_BAND: BandRule = {
  band: "facility_sizing_band_20_50_pct_of_last_round",
  rule: "20 to 50 percent of last round",
  citation: "Mercury venture debt term sheet guide (accessed 2026-11-10)",
};
