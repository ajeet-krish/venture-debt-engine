/**
 * Build time compiler for the methods page (architecture P4.4).
 *
 * Reads docs/methods.md from the repository root and emits
 * web/src/content/methods.ts, a generated TypeScript module exporting:
 * - METHODS_HTML: the pre-rendered static HTML string the route mounts;
 * - METHODS_TEXT: the plain text export for the documentation tests;
 * - METHODS_PLACEHOLDER and METHODS_NOTICE: the honest fallback while
 *   the canonical document does not exist yet.
 *
 * The compiler is deliberately minimal: h1 to h3 headings (deeper
 * headings render at h3), paragraphs, unordered and ordered lists, pipe
 * tables with a separator row, fenced code blocks, thematic breaks, and
 * inline code and bold. No markdown library ships to the browser and no
 * npm dependency is added. Every text node passes through HTML entity
 * escaping, so raw markup in the source can never reach the output.
 *
 * The output stays free of double hyphen sequences by construction: the
 * only constructs that may carry them in the source are whole line
 * thematic breaks and full pipe table separator rows (the prose scan
 * exemptions), and both are consumed here rather than echoed. The
 * source file is scanned as prose and the generated module is scanned
 * as source, so a source built only from the supported constructs
 * yields a clean module.
 *
 * When docs/methods.md does not exist yet (it lands in a later slice),
 * the script writes a clearly marked placeholder module carrying an
 * honest notice string instead of failing the build.
 *
 * Path resolution: import.meta.url resolved relative to this script,
 * the same convention as scripts/sync-payloads.js and scripts/charts/*,
 * so the compile works from any working directory.
 *
 * The .js extension is deliberate: the pinned dash scan has no .mjs
 * class, and .js sits in the source class of the exemption table.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(scriptDir, "..");
const repoRoot = resolve(webRoot, "..");
const sourcePath = join(repoRoot, "docs", "methods.md");
const outDir = join(webRoot, "src", "content");
const outFile = join(outDir, "methods.ts");

/** Honest notice shown while docs/methods.md does not exist. */
const PLACEHOLDER_NOTICE =
  "docs/methods.md has not been authored yet, so this page has no compiled " +
  "text to show. The canonical methods document is docs/methods.md in this " +
  "repository: the site build compiles it into this page the moment it exists. " +
  "Until then no convention, citation, or limitation is quoted here.";

const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const UNORDERED_RE = /^[-*+]\s+(.*)$/;
const ORDERED_RE = /^(\d+)[.)]\s+(.*)$/;
const THEMATIC_RE = /^\s*-{3,}\s*$/;
const FENCE_OPEN_RE = /^\s*(`{3,}|~{3,})/;
const FENCE_CLOSE_RE = /^(`{3,}|~{3,})$/;
const PLAIN_CHARS_RE = /^[\s|:-]+$/;
const INLINE_RE = /(`[^`]+`)|(\*\*[^*]+\*\*)/g;

/** HTML entity escaping for every text node that reaches the output. */
function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Split one line into plain, code, and bold segments. */
function inlineSegments(text) {
  const segments = [];
  let cursor = 0;
  INLINE_RE.lastIndex = 0;
  let match = INLINE_RE.exec(text);
  while (match !== null) {
    if (match.index > cursor) {
      segments.push({ kind: "text", text: text.slice(cursor, match.index) });
    }
    if (match[1] !== undefined) {
      segments.push({ kind: "code", text: match[1].slice(1, -1) });
    } else {
      segments.push({ kind: "bold", text: match[2].slice(2, -2) });
    }
    cursor = match.index + match[0].length;
    match = INLINE_RE.exec(text);
  }
  if (cursor < text.length) {
    segments.push({ kind: "text", text: text.slice(cursor) });
  }
  return segments;
}

/** Inline HTML: code spans and bold, everything else escaped. */
function inline(text) {
  return inlineSegments(text)
    .map((segment) => {
      if (segment.kind === "code") {
        return `<code>${escapeHtml(segment.text)}</code>`;
      }
      if (segment.kind === "bold") {
        return `<strong>${escapeHtml(segment.text)}</strong>`;
      }
      return escapeHtml(segment.text);
    })
    .join("");
}

/** Inline plain text for the METHODS_TEXT export: markers removed. */
function inlineText(text) {
  return inlineSegments(text)
    .map((segment) => segment.text)
    .join("");
}

/** A full pipe separator row: pipes, colons, dashes, spaces, one dash. */
function isTableSeparator(line) {
  return line.includes("|") && line.includes("-") && PLAIN_CHARS_RE.test(line);
}

/** Split a pipe row into trimmed cells, dropping the outer pipes. */
function tableCells(line) {
  let row = line.trim();
  if (row.startsWith("|")) {
    row = row.slice(1);
  }
  if (row.endsWith("|")) {
    row = row.slice(0, -1);
  }
  return row.split("|").map((cell) => cell.trim());
}

/** Compile the canonical markdown into HTML and plain text blocks. */
function compile(source) {
  const lines = source.split(/\r?\n/);
  const html = [];
  const text = [];
  let paragraph = [];
  let lastHeading = "";
  let index = 0;

  function flushParagraph() {
    if (paragraph.length === 0) {
      return;
    }
    const raw = paragraph.join(" ").trim();
    paragraph = [];
    if (raw.length === 0) {
      return;
    }
    html.push(`<p>${inline(raw)}</p>`);
    text.push(inlineText(raw));
  }

  while (index < lines.length) {
    const line = lines[index];

    if (line.trim() === "") {
      flushParagraph();
      index += 1;
      continue;
    }

    // Fenced code block: the fence line and any language tag are dropped.
    const fence = line.match(FENCE_OPEN_RE);
    if (fence !== null) {
      flushParagraph();
      const opener = fence[1];
      const body = [];
      index += 1;
      while (index < lines.length) {
        const current = lines[index].trim();
        const close = current.match(FENCE_CLOSE_RE);
        if (
          close !== null &&
          close[1][0] === opener[0] &&
          close[1].length >= opener.length
        ) {
          index += 1;
          break;
        }
        body.push(lines[index]);
        index += 1;
      }
      const codeText = body.join("\n");
      html.push(`<pre><code>${escapeHtml(codeText)}</code></pre>`);
      text.push(codeText);
      continue;
    }

    const heading = line.match(HEADING_RE);
    if (heading !== null) {
      flushParagraph();
      const level = Math.min(heading[1].length, 3);
      const raw = heading[2].trim();
      lastHeading = inlineText(raw);
      html.push(`<h${level}>${inline(raw)}</h${level}>`);
      text.push(inlineText(raw));
      index += 1;
      continue;
    }

    if (THEMATIC_RE.test(line)) {
      flushParagraph();
      html.push("<hr />");
      index += 1;
      continue;
    }

    // Pipe table: header row, separator row (consumed), body rows.
    if (
      line.includes("|") &&
      index + 1 < lines.length &&
      isTableSeparator(lines[index + 1])
    ) {
      flushParagraph();
      const header = tableCells(line);
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim() !== "") {
        rows.push(tableCells(lines[index]));
        index += 1;
      }
      const label = lastHeading.length > 0 ? `Table: ${lastHeading}` : "Table";
      const headCells = header
        .map((cell) => `<th scope="col">${inline(cell)}</th>`)
        .join("");
      const bodyRows = rows
        .map(
          (row) =>
            `<tr>${row.map((cell) => `<td>${inline(cell)}</td>`).join("")}</tr>`,
        )
        .join("");
      html.push(
        `<div class="table-scroll" tabindex="0" role="region" aria-label="${escapeHtml(label)}">` +
          `<table class="ledger-table"><thead><tr>${headCells}</tr></thead>` +
          `<tbody>${bodyRows}</tbody></table></div>`,
      );
      text.push(
        [header, ...rows].map((row) => row.join(" | ")).join("\n"),
      );
      continue;
    }

    // A separator row that never found its header is consumed, never
    // echoed: echoing it would carry its dashes into the module.
    if (isTableSeparator(line)) {
      index += 1;
      continue;
    }

    const unordered = line.match(UNORDERED_RE);
    const ordered = line.match(ORDERED_RE);
    if (unordered !== null || ordered !== null) {
      flushParagraph();
      const isOrdered = ordered !== null;
      const items = [];
      while (index < lines.length) {
        const current = lines[index];
        const item = isOrdered ? current.match(ORDERED_RE) : current.match(UNORDERED_RE);
        if (item === null) {
          break;
        }
        items.push(isOrdered ? item[2] : item[1]);
        index += 1;
      }
      const tag = isOrdered ? "ol" : "ul";
      html.push(
        `<${tag}>${items.map((item) => `<li>${inline(item)}</li>`).join("")}</${tag}>`,
      );
      text.push(
        items
          .map((item, position) =>
            isOrdered ? `${position + 1}. ${inlineText(item)}` : `- ${inlineText(item)}`,
          )
          .join("\n"),
      );
      continue;
    }

    paragraph.push(line.trim());
    index += 1;
  }

  flushParagraph();
  return { html: html.join("\n"), text: text.join("\n\n") };
}

/** Serialize the generated module: four exports plus a header comment. */
function writeModule({ placeholder, notice, html, text, origin }) {
  const lines = [];
  lines.push("/**");
  lines.push(" * Generated by web/scripts/build-methods.js. Do not edit by hand:");
  lines.push(" * edit docs/methods.md and rerun the site build.");
  if (placeholder) {
    lines.push(" *");
    lines.push(" * PLACEHOLDER MODULE: docs/methods.md does not exist yet, so the");
    lines.push(" * HTML export is empty and the notice string is the honest state.");
  } else {
    lines.push(` * Source: ${origin}`);
  }
  lines.push(" */");
  lines.push(`export const METHODS_PLACEHOLDER = ${placeholder ? "true" : "false"};`);
  lines.push(`export const METHODS_NOTICE = ${JSON.stringify(notice)};`);
  lines.push(`export const METHODS_HTML = ${JSON.stringify(html)};`);
  lines.push(`export const METHODS_TEXT = ${JSON.stringify(text)};`);
  lines.push("");
  return lines.join("\n");
}

function main() {
  mkdirSync(outDir, { recursive: true });
  const origin = relative(repoRoot, sourcePath);

  if (!existsSync(sourcePath)) {
    const moduleText = writeModule({
      placeholder: true,
      notice: PLACEHOLDER_NOTICE,
      html: "",
      text: PLACEHOLDER_NOTICE,
      origin,
    });
    writeFileSync(outFile, moduleText, "utf8");
    console.log(
      `build-methods: ${origin} does not exist yet; wrote placeholder module ` +
        `${relative(repoRoot, outFile)} with an honest notice string`,
    );
    return;
  }

  const source = readFileSync(sourcePath, "utf8");
  const compiled = compile(source);
  const moduleText = writeModule({
    placeholder: false,
    notice: "",
    html: compiled.html,
    text: compiled.text,
    origin,
  });
  writeFileSync(outFile, moduleText, "utf8");
  console.log(
    `build-methods: compiled ${origin} -> ${relative(repoRoot, outFile)} ` +
      `(${Buffer.byteLength(moduleText, "utf8")} bytes, ` +
      `${Buffer.byteLength(compiled.html, "utf8")} bytes of HTML)`,
  );
}

main();
