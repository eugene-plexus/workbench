import "@testing-library/jest-dom/vitest";
import { setMathEngine } from "./lib/mathClient";

// jsdom has no worker: the tests lay formulas out on the page, with the same
// KaTeX call the worker makes (lib/mathTypeset.ts).
setMathEngine({
  async typeset(tex, display) {
    const { typeset } = await import("./lib/mathTypeset");
    return typeset(tex, display);
  },
});
