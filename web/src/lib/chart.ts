/**
 * Build time chart assets.
 *
 * The SVGs under src/charts/ are produced by scripts/charts/*.js from the
 * committed payloads before every build and are not committed themselves.
 * Routes reach them through an eager glob rather than a static import so
 * that a failed or skipped generation resolves to an empty set: the route
 * then keeps its honest placeholder instead of breaking the build.
 *
 * Nothing here parses, scales, or transforms a value. This module hands
 * back a URL string; the image it points at is inert markup.
 */
const generated = import.meta.glob("../charts/*.svg", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

/** URL of one generated chart asset, or null when it was not generated. */
export function chartAsset(name: string): string | null {
  const suffix = `/${name}.svg`;
  for (const path of Object.keys(generated).sort()) {
    if (path.endsWith(suffix)) {
      return generated[path];
    }
  }
  return null;
}
