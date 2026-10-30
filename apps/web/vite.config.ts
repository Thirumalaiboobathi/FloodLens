/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The browser only ever talks to the same-origin /v1 API (CloudFront in production, this proxy in
// development), so no CORS, no AWS credentials and no tokens exist in the frontend.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/v1": { target: process.env.FLOODLENS_API ?? "http://127.0.0.1:8000", changeOrigin: false } },
    fs: { allow: ["../.."] }, // tests read the committed schema fixtures in schemas/v1/fixtures
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 1100, // maplibre-gl alone is ~1 MB minified; it is split out and cached separately
    rollupOptions: { output: { manualChunks: { maplibre: ["maplibre-gl"], charts: ["recharts"], react: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"] } } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
