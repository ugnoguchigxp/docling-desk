import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === "build" ? "/static/frontend/" : "/",
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      ["/api", "/files", "/view", "/static", "/legacy"].map((path) => [
        path,
        {
          target: process.env.UI_BACKEND || "http://127.0.0.1:8765",
          changeOrigin: false,
        },
      ]),
    ),
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rolldownOptions: { input: { index: "index.html", embed: "embed.html" } },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test-setup.ts"],
  },
}));
