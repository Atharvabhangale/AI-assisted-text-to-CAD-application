import { defineConfig } from "vite";

/**
 * The experimental page, on its own port.
 *
 * The stable application keeps 5173 and proxies to the stable API on 8000.
 * This one is 5174 and proxies to the experimental API on 8001, so both can
 * run at once and neither can be mistaken for the other.
 *
 * `fs.allow` reaches up one level on purpose: the viewer and the render-model
 * reader are imported from `apps/web/src` rather than copied, so there is one
 * implementation of each and the experiment cannot drift from it.
 */
export default defineConfig({
  server: {
    port: 5174,
    strictPort: true,
    fs: { allow: [".."] },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: { outDir: "dist" },
});
