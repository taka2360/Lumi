import { createReadStream, existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

/** 開発用キャラクター実体の置き場所。**`public/` に置かない。** */
const DEV_ASSETS = "dev-assets";

/**
 * 開発中だけ `dev-assets/character.*` を `/character.vrm` として配る。
 *
 * **`public/` に置くと配布物に入ってしまう。** Vite は `public/` を丸ごとコピーするため、
 * ローカルで試していた 33 MB の VRM がインストーラに入っていた（2026-08-15 実測）。
 * 同梱してよいモデルは未決なので（docs/licensing.md §7 未確認 #5）、
 * **構造的に入らない場所**に置き、dev サーバだけが配る。
 *
 * `apply: "serve"` なので、本番ビルドにはこの経路自体が存在しない。
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
        // 拡張子を変えて無効化してある場合は配らない（`character.disabled` など）。
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

// Tauri が dev サーバを待つので、ポートは固定する（外れると起動しない）。
export default defineConfig({
  plugins: [react(), devCharacter()],
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
    // 配布物にソースマップを入れない（実測で 5 MB。配布物に原本を載せる意味も無い）。
    sourcemap: false,
    rollupOptions: {
      // クレジットは別ページにする。**Core に繋がる Stage のコードを読み込ませない**
      // ため（docs/architecture/ui.md「`credits` を Core に繋がない理由」）。
      input: {
        main: "index.html",
        credits: "credits.html",
      },
    },
  },
});
