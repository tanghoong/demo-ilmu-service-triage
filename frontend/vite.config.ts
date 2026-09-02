import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    // The browser only ever talks to our own origin. ILMU is never reachable
    // from here — the key lives in the Python service behind /api.
    proxy: { "/api": { target: "http://127.0.0.1:8100", changeOrigin: true } },
  },
});
