import { createReadStream, existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

/** Where the dev character asset lives. **Never placed in `public/`.** */
const DEV_ASSETS = "dev-assets";

/**
 * Serves `dev-assets/character.*` as `/character.vrm`, only during development.
 *
 * **Placing it in `public/` would ship it in the distributable.** Vite copies
 * `public/` wholesale, and a 33 MB VRM being tried locally ended up in the
 * installer (observed 2026-08-15). Since which model is allowed to be bundled is
 * still undecided (docs/licensing.md §7 open item #5), it's placed **somewhere
 * that structurally can't be included**, served only by the dev server.
 *
 * With `apply: "serve"`, this code path doesn't even exist in the production build.
 */
function devCharacter(): Plugin {
  return {
    name: "lumi-dev-character",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url?.split("?")[0] !== "/character.vrm" || !existsSync(DEV_ASSETS)) {
          return next();
        }
        // Never served if disabled by renaming the extension (e.g. `character.disabled`).
        const found = readdirSync(DEV_ASSETS).find((name) => name === "character.vrm");
        if (!found) {
          return next();
        }
        res.setHeader("Content-Type", "model/gltf-binary");
        createReadStream(join(DEV_ASSETS, found)).pipe(res);
      });
    },
  };
}

// Tauri waits for the dev server, so the port is fixed (won't start if it's taken).
export default defineConfig({
  plugins: [react(), devCharacter()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    // Only ever read by Shell (Tauri). Never exposed externally.
    host: "127.0.0.1",
  },
  build: {
    // Matches Tauri's WebView2
    target: "chrome110",
    // No source maps in the distributable (observed at 5 MB. No reason to ship originals in the distributable either).
    sourcemap: false,
    rollupOptions: {
      // Credits get their own page. **So it never loads the Stage code that
      // connects to Core** (docs/architecture/ui.md "Why `credits` doesn't connect to Core").
      input: {
        main: "index.html",
        credits: "credits.html",
      },
    },
  },
});
