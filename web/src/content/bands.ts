/**
 * Committed rule-of-thumb band text used by the sizing page.
 *
 * The band name, the rule, and the citation are transcribed from the
 * committed scenario configuration (pipeline/config/scenario.toml,
 * [bands.facility_sizing]). They are printed as text: the cap value in
 * dollars is not published in any payload, so the site shows the rule with
 * its citation and never derives a cap figure in the browser.
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
