/**
 * Core への WS 接続（Stage 側）。
 *
 * 接続先は Shell が `shell.core.endpoint` で教える（**Stage 用の token だけ**が渡ってくる）。
 * Core が再起動するとポートが変わるので、Shell からの通知で繋ぎ直す。
 *
 * **Stage はここで判断しない。** 受け取った `stage.*` をハンドラに渡すだけ。
 */

import { invoke } from "@tauri-apps/api/core";
import { getCurrentWebviewWindow } from "@tauri-apps/api/webviewWindow";

import { isTauri } from "../platform/tauri";
import { type CoreMessage, helloMessage, parseCoreMessage, resultMessage } from "./protocol";

/** 対応する Shell 側の定数は `shell/src-tauri/src/core_endpoint.rs`。**正は `wire.json`**（ADR-022）。 */
export const CMD_CORE_ENDPOINT = "shell_core_endpoint";
export const EVENT_CORE_ENDPOINT = "shell:core:endpoint";

/** 再接続の待ち時間。Core の再起動を待てる程度に短く。 */
const RECONNECT_DELAY_MS = 500;

interface CoreEndpoint {
  port: number;
  token: string;
}

export type CommandHandler = (payload: Record<string, unknown>) => Promise<Record<string, unknown>>;
export type NotifyHandler = (payload: Record<string, unknown>) => void;

export interface CoreConnectionHandlers {
  commands: Record<string, CommandHandler>;
  notifications: Record<string, NotifyHandler>;
  onConnectedChange?: (connected: boolean) => void;
}

export interface CoreConnection {
  close(): void;
}

/**
 * 接続を開始し、切れたら繋ぎ直し続ける。
 *
 * Tauri の外（ブラウザで開いたとき）では何もしない。**黙って壊れないため**に明示的に分岐する。
 */
export function connectToCore(handlers: CoreConnectionHandlers): CoreConnection {
  if (!isTauri()) {
    return { close: () => {} };
  }

  let closed = false;
  let socket: WebSocket | null = null;
  let retryTimer: number | null = null;
  let unlistenEndpoint: (() => void) | null = null;

  const setConnected = (value: boolean) => handlers.onConnectedChange?.(value);

  const handleMessage = (message: CoreMessage) => {
    if (message.kind === "welcome") {
      setConnected(true);
      return;
    }
    if (message.kind === "notify") {
      handlers.notifications[message.method]?.(message.payload);
      return;
    }

    const handler = handlers.commands[message.method];
    if (!handler) {
      // **知らない command は握りつぶさず、失敗として返す。**
      socket?.send(resultMessage(message.id, false, {}, "unhandled_method"));
      return;
    }
    handler(message.payload)
      .then((payload) => socket?.send(resultMessage(message.id, true, payload)))
      .catch((error: unknown) => {
        const reason = error instanceof Error ? error.message : "handler_failed";
        socket?.send(resultMessage(message.id, false, {}, reason));
      });
  };

  const scheduleRetry = () => {
    if (closed || retryTimer !== null) {
      return;
    }
    retryTimer = window.setTimeout(() => {
      retryTimer = null;
      void openSocket();
    }, RECONNECT_DELAY_MS);
  };

  const openSocket = async () => {
    if (closed || socket !== null) {
      return;
    }
    const endpoint = await invoke<CoreEndpoint | null>(CMD_CORE_ENDPOINT);
    if (closed || !endpoint) {
      // Core がまだ listen していない。ポートが決まればイベントで起こされる。
      return;
    }

    const ws = new WebSocket(`ws://127.0.0.1:${endpoint.port}`);
    socket = ws;

    ws.addEventListener("open", () => ws.send(helloMessage(endpoint.token)));
    ws.addEventListener("message", (event) => {
      if (typeof event.data !== "string") {
        return;
      }
      const message = parseCoreMessage(event.data);
      if (message) {
        handleMessage(message);
      }
    });
    ws.addEventListener("close", () => {
      if (socket === ws) {
        socket = null;
      }
      setConnected(false);
      scheduleRetry();
    });
    ws.addEventListener("error", () => ws.close());
  };

  void getCurrentWebviewWindow()
    .listen(EVENT_CORE_ENDPOINT, () => {
      // ポートが変わった（Core が再起動した）。今の接続は捨てて繋ぎ直す。
      socket?.close();
      socket = null;
      void openSocket();
    })
    .then((unlisten) => {
      if (closed) {
        unlisten();
      } else {
        unlistenEndpoint = unlisten;
      }
    });

  void openSocket();

  return {
    close() {
      closed = true;
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
      }
      unlistenEndpoint?.();
      socket?.close();
      socket = null;
    },
  };
}
