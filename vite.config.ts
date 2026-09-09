import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "web-dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // Local dev: proxy API calls to the running backend (docker-compose dev).
    proxy: {
      "/api": "http://localhost:8082",
    },
  },
});
