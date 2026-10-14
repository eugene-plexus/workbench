import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";
import { isKatexCss, katexWoff2Only } from "./src/lib/katexFonts.ts";

// KaTeX's faces as woff2 only (src/lib/katexFonts.ts), before Vite reads the
// stylesheet's url()s and copies each file it names.
const katexFonts: Plugin = {
  name: "katex-woff2-only",
  enforce: "pre",
  transform(code, id) {
    return isKatexCss(id) ? { code: katexWoff2Only(code), map: null } : null;
  },
};

// The build lands in the Python package, which serves it (workbench-v1.md
// W9). `npm run dev` proxies the API to a running Workbench on 8190.
export default defineConfig({
  plugins: [katexFonts, react(), tailwindcss()],
  build: {
    outDir: "../src/eugene_plexus_workbench/static",
    emptyOutDir: true,
    // The policy allows no inline script, and a module preload polyfill is one.
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
  },
  // The formula worker (src/lib/mathWorker.ts) is a module, like the page.
  worker: { format: "es" },
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8190", changeOrigin: false },
      "/signin": { target: "http://127.0.0.1:8190", changeOrigin: false },
      "/oidc": { target: "http://127.0.0.1:8190", changeOrigin: false },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
