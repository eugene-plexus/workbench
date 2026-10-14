import { defineConfig } from "vite";

// An artifact's preview (src/preview/runtime.ts): one classic script, built
// apart from the page after it. The preview's page has no origin of its own,
// and a module script is fetched as another origin's, which needs CORS; a
// classic script is not. Nothing of it is shared with the page or loaded by it.
export default defineConfig({
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  // The page's own files (public/) are the page build's, not this one's.
  publicDir: false,
  build: {
    outDir: "../src/eugene_plexus_workbench/static/preview",
    emptyOutDir: true,
    lib: {
      entry: "src/preview/runtime.ts",
      formats: ["iife"],
      name: "WorkbenchPreview",
      fileName: () => "runtime.js",
    },
  },
});
