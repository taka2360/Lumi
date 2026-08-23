/**
 * The WS connection to Core (Stage side).
 *
 * Shell tells this where to connect via `shell.core.endpoint` (**only the
 * Stage-specific token** is handed over). Core's port changes on restart, so a
 * notification from Shell triggers a reconnect.
 *
 * **The Stage never makes decisions here.** It just hands received `stage.*` off to handlers.
 */

import { invoke } from "@tauri-apps/api/core";
import { getCurrentWebviewWindow } from "@tauri-apps/api/webviewWindow";

import { isTauri } from "../platform/tauri";
import {
  type CoreMessage,
  type CoreResult,
  type CoreRole,
  helloMessage,
  ProtocolVersionMismatch,
  parseCoreMessage,
  requestMessage,
  resultMessage,
} from "./protocol";

/** The corresponding constant on the Shell side is `shell/src-tauri/src/core_endpoint.rs`. **`wire.json` is authoritative** (ADR-022). */
export const CMD_CORE_ENDPOINT = "shell_core_endpoint";
export const EVENT_CORE_ENDPOINT = "shell:core:endpoint";

/** Wait time before reconnecting. Short enough to wait out a Core restart. */
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
  /**
   * Asks Core to do something (ADR-028). **Rejects rather than resolving on refusal** —
   * a caller that forgets to check `ok` would otherwise report success for a change Core
   * declined to make.
   */
  request(method: string, payload?: Record<string, unknown>): Promise<Record<string, unknown>>;
}

/** How long to wait for Core's answer. **Never waits forever** — the UI has to move on. */
const REQUEST_TIMEOUT_MS = 10_000;

/**
 * Starts the connection and keeps reconnecting if it drops.
 *
 * Does nothing outside Tauri (when opened in a browser). Branches explicitly here
 * **so it never silently breaks**.
 */
export function connectToCore(
  handlers: CoreConnectionHandlers,
  /**
   * Which client this window is (ADR-042). **The token it receives is chosen by Shell
   * from the window's label**, so a window that claims the wrong role here fails to
   * authenticate rather than connecting as something else.
   */
  role: CoreRole = "stage",
): CoreConnection {
  if (!isTauri()) {
    return {
      close: () => {},
      request: () => Promise.reject(new Error("not_connected")),
    };
  }

  let closed = false;
  let socket: WebSocket | null = null;
  //: Set across the `await` that fetches the endpoint. **Without it, a retry timer and an
  //: endpoint event that land together each get past the `socket !== null` check and open
  //: a socket**, and the second one silently replaces the first in `socket`.
  let connecting = false;
  let retryTimer: number | null = null;
  let unlistenEndpoint: (() => void) | null = null;
  let nextRequestId = 0;
  const waiting = new Map<string, (result: CoreResult) => void>();

  /** Fails everything in flight. **A dropped connection must not leave a promise hanging.** */
  const abandonPending = (reason: string) => {
    for (const [id, resolve] of waiting) {
      resolve({ kind: "result", corrId: id, ok: false, payload: {}, error: reason });
    }
    waiting.clear();
  };

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
    if (message.kind === "result") {
      // The answer to something the Stage asked for (ADR-028).
      const pending = waiting.get(message.corrId);
      if (!pending) {
        // Nobody is waiting. **Normal after a reconnect** (the caller was already
        // rejected with `disconnected`), so this is not worth reporting
        return;
      }
      waiting.delete(message.corrId);
      pending(message);
      return;
    }

    const handler = handlers.commands[message.method];
    if (!handler) {
      // **An unknown command is never swallowed — it's returned as a failure.**
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
    if (closed || connecting || socket !== null) {
      return;
    }
    connecting = true;
    let endpoint: CoreEndpoint | null;
    try {
      endpoint = await invoke<CoreEndpoint | null>(CMD_CORE_ENDPOINT);
    } catch {
      // **Shell can refuse too**, and a rejected `invoke` here would otherwise be an
      // unhandled rejection that also ends the retry chain: the window would sit
      // disconnected with nothing left to wake it.
      connecting = false;
      scheduleRetry();
      return;
    } finally {
      connecting = false;
    }
    if (closed) {
      return;
    }
    if (!endpoint) {
      // **Core is not listening yet, so try again.** The Stage is also woken by an
      // endpoint event, but a panel window is not (Shell emits it only to the character
      // window), and one opened while Core was restarting would otherwise sit there
      // disconnected forever with nothing left to wake it.
      scheduleRetry();
      return;
    }

    const ws = new WebSocket(`ws://127.0.0.1:${endpoint.port}`);
    socket = ws;

    ws.addEventListener("open", () => ws.send(helloMessage(endpoint.token, role)));
    ws.addEventListener("message", (event) => {
      if (typeof event.data !== "string") {
        return;
      }
      try {
        const message = parseCoreMessage(event.data, role);
        if (message) {
          handleMessage(message);
        }
      } catch (error: unknown) {
        if (!(error instanceof ProtocolVersionMismatch)) {
          throw error;
        }
        // A frame whose meaning is not agreed on is a protocol error, not an ignorable event.
        // Closing makes the rejection observable through the existing disconnected/reconnect
        // path without interpreting the frame under the wrong schema.
        ws.close(4400, "protocol error");
      }
    });
    ws.addEventListener("close", () => {
      if (socket === ws) {
        socket = null;
      }
      abandonPending("disconnected");
      setConnected(false);
      scheduleRetry();
    });
    ws.addEventListener("error", () => ws.close());
  };

  void getCurrentWebviewWindow()
    .listen(EVENT_CORE_ENDPOINT, () => {
      // The port changed (Core restarted). Discard the current connection and reconnect.
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
      abandonPending("closed");
      socket?.close();
      socket = null;
    },

    request(method, payload = {}) {
      const live = socket;
      if (!live || live.readyState !== WebSocket.OPEN) {
        return Promise.reject(new Error("not_connected"));
      }
      const id = `s${nextRequestId++}`;
      return new Promise((resolve, reject) => {
        const timer = window.setTimeout(() => {
          waiting.delete(id);
          reject(new Error("timeout"));
        }, REQUEST_TIMEOUT_MS);

        waiting.set(id, (result) => {
          window.clearTimeout(timer);
          // **Refusal rejects.** Resolving would let a caller that forgets to check `ok`
          // report success for a change Core declined to make
          if (result.ok) {
            resolve(result.payload);
          } else {
            reject(new Error(result.error ?? "refused"));
          }
        });
        live.send(requestMessage(id, method, payload));
      });
    },
  };
}
