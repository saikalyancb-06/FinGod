import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Two separate backends: FinLens (B2C) on 8001, Money Mule (B2B) on 8002.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // prefixes must not collide with page routes (/finlens, /institution)
      "/x-fin/": { target: "http://127.0.0.1:8001", rewrite: (p) => p.replace(/^\/x-fin/, "") },
      "/x-inst/": { target: "http://127.0.0.1:8002", ws: true, rewrite: (p) => p.replace(/^\/x-inst/, "") },
    },
  },
});
