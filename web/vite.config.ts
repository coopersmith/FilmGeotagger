import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `filmgeo serve` mounts web/dist at `/` and owns `/api`; in development Vite proxies `/api`
// to the running API so the app is the same code either way.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
