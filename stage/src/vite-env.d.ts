/// <reference types="vite/client" />

declare global {
  interface Window {
    /** Injected by Tauri 2's initialization script. `undefined` when opened in a browser. */
    isTauri?: boolean;
  }
}

export {};
