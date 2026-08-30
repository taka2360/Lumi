import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Tauri waits for the dev server, so the port is fixed (won't start if it's taken).
export default defineConfig({
  plugins: [react()],
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
      // Credits and help get their own pages. **So they never load the Stage code that
      // connects to Core** (docs/architecture/ui.md "Why `credits` doesn't connect to Core").
      // Help has the same requirement for a reason of its own: it explains the gestures
      // that reach the setup screen, which is shown when Core has not come up.
      //
      // The three panels get their own pages for a related but different reason
      // (ADR-042): they do connect, as `panel`, and loading the character's entry point
      // would start a second client claiming `stage`.
      input: {
        main: "index.html",
        credits: "credits.html",
        help: "help.html",
        settings: "settings.html",
        inspector: "inspector.html",
        memory: "memory.html",
      },
    },
  },
});
