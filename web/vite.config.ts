import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The build lands in the Python package, which serves it (workbench-v1.md
// W9). `npm run dev` proxies the API to a running Workbench on 8190.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../src/eugene_plexus_workbench/static",
    emptyOutDir: true,
    // The policy allows no inline script, and a module preload polyfill is one.
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
  },
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
