import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Tauri が dev サーバを待つので、ポートは固定する（外れると起動しない）。
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    // Shell（Tauri）からしか読まれない。外部に晒さない。
    host: "127.0.0.1",
  },
  build: {
    // Tauri の WebView2 に合わせる
    target: "chrome110",
    sourcemap: true,
  },
});
