/**
 * The WS connection to Core (Stage side).
 *
 * Shell tells this where to connect via `shell.core.endpoint` (**only the
 * Stage-specific token** is handed over). Core's port changes on restart, so a
 * notification from Shell triggers a reconnect.
 *
 * **The Stage never makes decisions here.** It just hands received `stage.*` off to handlers.
 */

import type { CoreEndpoint, Disposable } from "../platform/PlatformShell";
import { getPlatformShell } from "../platform/useStageShell";
import { PendingRequests } from "./pending";
import {
  type CoreMessage,
  type CoreRole,
  helloMessage,
  ProtocolVersionMismatch,
  parseCoreMessage,
  requestMessage,
  resultMessage,
} from "./protocol";

/** Wait time before reconnecting. Short enough to wait out a Core restart. */
const RECONNECT_DELAY_MS = 500;

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

/**
 * Starts the connection and keeps reconnecting if it drops.
 *
 * **Reaches Shell only through `PlatformShell`.** Where Core is listening and when that
 * changes are things Shell knows; this file used to call Tauri directly for both, which
 * quietly made `platform/tauri.ts` not the only file an Electron port would touch.
 *
 * Outside Tauri the no-op shell reports no endpoint, so this retries instead of
 * connecting — the same path taken while Core is still starting.
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
  const shell = getPlatformShell();

  let closed = false;
  let socket: WebSocket | null = null;
  //: Set across the `await` that fetches the endpoint. **Without it, a retry timer and an
  //: endpoint event that land together each get past the `socket !== null` check and open
  //: a socket**, and the second one silently replaces the first in `socket`.
  let connecting = false;
  let retryTimer: number | null = null;
  let endpointSubscription: Disposable | null = null;
  const pending = new PendingRequests();

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
      // The answer to something the Stage asked for (ADR-028). Nobody waiting is
      // **normal after a reconnect** (the caller was already rejected), so the return
      // value is deliberately not checked here.
      pending.settle(message);
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
      endpoint = await shell.coreEndpoint();
    } catch {
      // **Shell can refuse too**, and a rejected request here would otherwise be an
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
      pending.abandon("disconnected");
      setConnected(false);
      scheduleRetry();
    });
    ws.addEventListener("error", () => ws.close());
  };

  void shell
    .onCoreEndpointChanged(() => {
      // The port changed (Core restarted). Discard the current connection and reconnect.
      socket?.close();
      socket = null;
      void openSocket();
    })
    .then((subscription) => {
      // **`close()` may already have run.** Subscribing is asynchronous, so a connection
      // closed while this was in flight would otherwise keep a live listener for a
      // connection nobody is using any more.
      if (closed) {
        subscription.dispose();
      } else {
        endpointSubscription = subscription;
      }
    })
    .catch((error: unknown) => {
      // The socket's own retry path remains usable without this listener, but losing the
      // restart nudge must stay observable instead of becoming an unhandled rejection.
      // biome-ignore lint/suspicious/noConsole: Stage has no shared telemetry sink yet.
      console.error("Failed to subscribe to Core endpoint changes", error);
    });

  void openSocket();

  return {
    close() {
      closed = true;
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
      }
      endpointSubscription?.dispose();
      pending.abandon("closed");
      socket?.close();
      socket = null;
    },

    request(method, payload = {}) {
      const live = socket;
      if (!live || live.readyState !== WebSocket.OPEN) {
        return Promise.reject(new Error("not_connected"));
      }
      const { id, answered } = pending.open();
      try {
        live.send(requestMessage(id, method, payload));
      } catch (error: unknown) {
        // **The request never left, so nothing will ever answer it.** Settling it here
        // rather than letting it time out keeps the failure attached to the cause, and
        // stops the pending timer rejecting a promise the caller never received.
        pending.fail(id, error instanceof Error ? error.message : "send_failed");
      }
      return answered;
    },
  };
}
