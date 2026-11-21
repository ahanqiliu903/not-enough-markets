import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "electron-vite";
import type { Plugin } from "vite";

/**
 * Content-Security-Policy for the renderer. The built app gets the strict policy: only
 * bundled scripts, styles and fonts, no network, no inline code. Dev adds what Vite's
 * HMR client and React refresh need, on localhost only.
 */
const CSP_PROD = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "font-src 'self'",
  "img-src 'self'",
  "connect-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "object-src 'none'",
].join("; ");

const CSP_DEV = [
  "default-src 'none'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self'",
  "img-src 'self' data:",
  "connect-src 'self' ws://localhost:* http://localhost:*",
  "base-uri 'none'",
  "form-action 'none'",
  "object-src 'none'",
].join("; ");

function cspPlugin(): Plugin {
  return {
    name: "nem-csp",
    transformIndexHtml: (html, ctx) =>
      html.replaceAll("%NEM_CSP%", ctx.server !== undefined ? CSP_DEV : CSP_PROD),
  };
}

const root = import.meta.dirname;
const shared = resolve(root, "src/shared");

export default defineConfig({
  main: {
    resolve: { alias: { "@shared": shared } },
    build: {
      outDir: "out/main",
      rolldownOptions: { input: { index: resolve(root, "src/main/index.ts") } },
    },
  },
  preload: {
    resolve: { alias: { "@shared": shared } },
    build: {
      outDir: "out/preload",
      // Sandboxed preloads cannot load ES modules, so emit a single CommonJS file.
      rolldownOptions: {
        input: { index: resolve(root, "src/preload/index.ts") },
        output: { format: "cjs", entryFileNames: "[name].cjs" },
      },
    },
  },
  renderer: {
    root: resolve(root, "src/renderer"),
    resolve: { alias: { "@shared": shared, "@renderer": resolve(root, "src/renderer") } },
    plugins: [react(), cspPlugin()],
    build: {
      outDir: "out/renderer",
      // Never inline assets as data: URIs; the strict CSP only allows bundled files.
      assetsInlineLimit: 0,
      minify: true,
      rolldownOptions: { input: { index: resolve(root, "src/renderer/index.html") } },
    },
  },
});
