import { defineConfig } from "vite";

// Multi-page build: one HTML entry per site route, no client router.
// The base path is the GitHub Pages project site:
// https://ajeet-krish.github.io/venture-debt-engine/
const routeNames = [
  "index",
  "sizing",
  "structures",
  "returns",
  "methods",
  "verification",
] as const;

const input: Record<string, string> = {};
for (const name of routeNames) {
  input[name] = new URL(`./${name}.html`, import.meta.url).pathname;
}

export default defineConfig({
  base: "/venture-debt-engine/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // No runtime fetch anywhere in the shipped bundle: the payload is a
    // static import, and Vite's module preload polyfill (the only fetch in
    // a default build) is disabled to keep the zero fetch claim literal.
    modulePreload: {
      polyfill: false,
    },
    // Chart SVGs ship as files rather than inlined data URIs, so the built
    // site carries them as inspectable assets under dist/assets/.
    assetsInlineLimit: 0,
    // The committed payloads are inlined intentionally under the zero
    // runtime fetch rule, so the page bundle is large by design.
    chunkSizeWarningLimit: 2000,
    rollupOptions: {
      input,
    },
  },
});
