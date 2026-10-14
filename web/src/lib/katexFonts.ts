/**
 * KaTeX's stylesheet names each of its 20 faces three times (woff2, woff,
 * ttf), and the build would ship all 60 files. Every browser the page
 * supports reads woff2, so the other two are taken out before the build sees
 * them (vite.config.ts). Build-time only; the page never imports this.
 */

const FALLBACK = /,\s*url\([^)]*\.(?:woff|ttf)\)\s*format\(["'](?:woff|truetype)["']\)/g;

export function katexWoff2Only(css: string): string {
  return css.replace(FALLBACK, "");
}

/** Whether `id` (a module id, perhaps with a query) is KaTeX's stylesheet. */
export function isKatexCss(id: string): boolean {
  return /[\\/]katex[\\/]dist[\\/]katex(?:\.min)?\.css$/.test(id.split("?")[0] ?? "");
}
