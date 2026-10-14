/**
 * What the page needs to show a laid-out formula: KaTeX's stylesheet and the
 * reader that makes its markup into elements. Loaded with the first formula
 * (mathClient.ts), so an answer with none loads neither. The fonts are
 * fetched by the browser for the faces a formula uses, and only as woff2
 * (vite.config.ts).
 */

import "katex/dist/katex.min.css";

export { toReact } from "./mathMarkup";
