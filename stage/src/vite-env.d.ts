/// <reference types="vite/client" />

declare global {
  interface Window {
    /** Tauri 2 が初期化スクリプトで注入する。ブラウザで開いたときは undefined。 */
    isTauri?: boolean;
  }
}

export {};
