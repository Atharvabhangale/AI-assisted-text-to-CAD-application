// `vitest/config` re-exports Vite's own `defineConfig`, widened with the
// `test` block, so one file configures the dev server and the suite.
import { defineConfig } from "vitest/config";

/**
 * The API base path is `/api`, and the dev server proxies it to the backend.
 *
 * That keeps the browser on **one origin**, so no CORS is needed and none was
 * added to the backend: the page and its API calls are both same-origin as
 * far as the browser is concerned, and the proxy strips the `/api` prefix
 * before forwarding. See `docs/web-application.md`.
 *
 * `CAD_API_ORIGIN` names the backend for development. It defaults to
 * localhost so a checkout works with no configuration, and it is neither a
 * production domain nor a machine-specific address.
 */
const apiOrigin = process.env.CAD_API_ORIGIN ?? "http://127.0.0.1:8000";

export default defineConfig({
  server: {
    proxy: {
      "/api": {
        target: apiOrigin,
        changeOrigin: false,
        rewrite: (path: string) => path.replace(/^\/api/, ""),
      },
    },
  },
  preview: {
    proxy: {
      "/api": {
        target: apiOrigin,
        changeOrigin: false,
        rewrite: (path: string) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts"],
    globals: true,
  },
});
