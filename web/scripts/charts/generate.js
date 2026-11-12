/**
 * Runs every build time chart generator.
 *
 * Each chart is a separate Node process so a failing chart cannot take
 * the others down with it. A failure is reported loudly and the build
 * continues: the route modules import the generated SVGs through a glob
 * and keep their honest placeholder when the asset is absent, so a
 * missing chart degrades to "no chart" instead of a broken build.
 *
 * The output directory is always created, even when every chart fails,
 * so the glob in src/lib/chart.ts resolves to an empty set rather than
 * to a directory that does not exist.
 *
 * The .js extension is deliberate: the pinned dash scan has no .mjs
 * class, and .js sits in the source class of the exemption table.
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(scriptDir, "..", "..");
const outDir = join(webRoot, "src", "charts");

const scripts = ["cashflow-chart.js", "capacity-chart.js"];

mkdirSync(outDir, { recursive: true });

let failed = 0;
for (const name of scripts) {
  const target = join(scriptDir, name);
  if (!existsSync(target)) {
    failed += 1;
    console.error(`charts: missing generator ${name}; routes keep the placeholder fallback`);
    continue;
  }
  const result = spawnSync(process.execPath, [target], {
    encoding: "utf8",
    cwd: webRoot,
  });
  if (result.stdout !== null && result.stdout.length > 0) {
    process.stdout.write(result.stdout);
  }
  if (result.stderr !== null && result.stderr.length > 0) {
    process.stderr.write(result.stderr);
  }
  if (result.status !== 0) {
    failed += 1;
    console.error(
      `charts: ${name} exited ${result.status === null ? "without a status" : result.status}; ` +
        "routes keep the placeholder fallback",
    );
  }
}

const generated = readdirSync(outDir)
  .filter((name) => name.endsWith(".svg"))
  .sort();
if (failed > 0) {
  console.warn(`charts: ${failed} of ${scripts.length} generators did not run cleanly`);
} else {
  console.log(`charts: ${generated.length} SVG asset(s) in web/src/charts: ${generated.join(", ")}`);
}

// Always exit 0: an absent chart must never fail the site build.
process.exit(0);
