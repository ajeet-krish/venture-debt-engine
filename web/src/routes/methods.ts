/**
 * Methods route: renders the HTML that scripts/build-methods.js
 * compiled from docs/methods.md at build time, through the shared page
 * shell (framing line, nav, footer stamps). No markdown parsing runs in
 * the browser: the module under src/content is a build artifact holding
 * a pre-rendered string, and this route only mounts it.
 *
 * The generated string is trusted because it comes from this
 * repository's own build step, not from user input. While
 * docs/methods.md does not exist yet, the module marks itself with
 * METHODS_PLACEHOLDER and the page shows the honest notice instead of
 * an empty document.
 */
import "../styles/main.css";
import { mountShell } from "../components/page";
import {
  METHODS_HTML,
  METHODS_NOTICE,
  METHODS_PLACEHOLDER,
} from "../content/methods";

const { main } = mountShell();

const host = main.querySelector("[data-methods]");
if (host === null) {
  throw new Error('methods route: missing [data-methods] mount point in methods.html');
}

if (METHODS_PLACEHOLDER) {
  const notice = document.createElement("div");
  notice.className = "notice";
  notice.setAttribute("role", "note");
  const head = document.createElement("p");
  head.className = "notice-head";
  head.textContent = "Canonical document not authored yet";
  const body = document.createElement("p");
  body.textContent = METHODS_NOTICE;
  notice.append(head, body);
  host.textContent = "";
  host.append(notice);
} else {
  host.innerHTML = METHODS_HTML;
}
