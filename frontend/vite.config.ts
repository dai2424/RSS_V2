import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: process.env.RSS_FRONTEND_DIST || "../runtime/frontend",
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname.replace(/^\/(\w:)/, "$1"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
