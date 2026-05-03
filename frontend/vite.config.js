import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev: Vite serves the SPA on 5173 and proxies /api/* to the backend.
// In prod: `npm run build` writes frontend/dist; the backend's FastAPI
// serves both the API (under /api) and the static bundle from a single
// origin. Single-deploy — no separate web tier needed.
//
// IMPORTANT: do NOT strip /api here. Backend routes are mounted under
// /api so dev and prod see the same URL shape.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8001",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
