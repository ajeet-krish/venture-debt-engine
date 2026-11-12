/**
 * Byte copy of the committed payloads into web/src/data.
 *
 * No parsing, no transformation: copyFileSync only, so the committed payload
 * stays the single source of truth and the site bundle prints payload bytes.
 * comparison.json is copied when it exists (phase 3 slice 6 commits it);
 * until then a clearly marked stub keeps the build working without inventing
 * any figures.
 *
 * Runs as an ES module because web/package.json sets "type": "module".
 * The .js extension is deliberate: the pinned dash scan classifies .mjs as
 * an unclassified extension (a scan failure), while .js is in the source
 * class of the exemption table in docs/architecture.md section 9.
 */
import { copyFileSync, existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(scriptDir, "..");
const repoRoot = resolve(webRoot, "..");
const payloadsRoot = join(repoRoot, "payloads");
const outDir = join(webRoot, "src", "data");

mkdirSync(outDir, { recursive: true });

// The asof directory is the single subdirectory of payloads/ that holds
// book.json. Sorted for determinism if more than one day is ever committed.
const asof = readdirSync(payloadsRoot, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .sort()
  .find((name) => existsSync(join(payloadsRoot, name, "book.json")));

if (asof === undefined) {
  console.error("sync-payloads: no payloads/<asof>/book.json found");
  process.exit(1);
}

const copied = [];

function copyIntoData(source, destinationName) {
  const destination = join(outDir, destinationName);
  copyFileSync(source, destination);
  copied.push(`${source} -> ${destination}`);
}

copyIntoData(join(payloadsRoot, asof, "book.json"), "book.json");
copyIntoData(join(payloadsRoot, "build_meta.json"), "build_meta.json");

const comparisonSource = join(payloadsRoot, asof, "comparison.json");
if (existsSync(comparisonSource)) {
  copyIntoData(comparisonSource, "comparison.json");
} else {
  const stubPath = join(outDir, "comparison.json");
  const stub =
    '{"placeholder": true, "tables": [], "_comment": "comparison payload lands in phase 3 slice 6"}\n';
  writeFileSync(stubPath, stub, "utf8");
  copied.push(`stub written -> ${stubPath} (no comparison payload at ${asof} yet)`);
}

console.log("sync-payloads: copied committed payloads into web/src/data");
for (const line of copied) {
  console.log(`  ${line}`);
}
