import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const appBase = env.VITE_APP_BASENAME || "/";
  const normalizedBase = appBase === "/" ? "/" : `${appBase.replace(/\/$/, "")}/`;

  return {
    base: normalizedBase,
    plugins: [react(), tailwindcss()],
    // Vitest + Testing Library (npm test); Tests liegen neben dem Code als *.test.jsx.
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.js"],
      css: false,
    },
    server: {
      port: 3000,
      proxy: {
        "/api": {
          target: "http://localhost:8000",
          changeOrigin: true,
        },
        "/health": {
          target: "http://localhost:8000",
          changeOrigin: true,
        },
      },
    },
  };
});
