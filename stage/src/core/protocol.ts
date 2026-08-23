/**
 * The WS protocol with Core (Stage side) — **pure types and parsing only**.
 *
 * The corresponding definition on the Core side is `core/lumi/transport/protocol.py`.
 * **Fix both at the same time.**
 *
 * A window receives only its own namespace: the character window `stage.*`, the settings,
 * inspector and memory windows `panel.*` (ADR-042). `os.*` never arrives, and even if it
 * did it would never be interpreted (**a window must never request OS privileges** →
 * docs/architecture/core.md §3).
 */

export const PROTOCOL_VERSION = 1;

/**
 * Which client this window is. **Not a choice the window makes at runtime**: Shell hands
 * out a different token per role, so claiming the wrong one here fails authentication
 * rather than succeeding as somebody else.
 */
export type CoreRole = "stage" | "panel";

/** The namespace a role may receive and send. Mirrors Core's `NAMESPACE_BY_ROLE`. */
export const NAMESPACE_BY_ROLE: Record<CoreRole, string> = {
  stage: "stage.",
  panel: "panel.",
};

export class ProtocolVersionMismatch extends Error {
  constructor(received: unknown) {
    super(`protocol version mismatch: received ${String(received)}`);
    this.name = "ProtocolVersionMismatch";
  }
}

export interface CoreCommand {
  kind: "command";
  id: string;
  method: string;
  payload: Record<string, unknown>;
}

export interface CoreNotify {
  kind: "notify";
  method: string;
  payload: Record<string, unknown>;
}

export interface CoreWelcome {
  kind: "welcome";
}

/** Core's answer to a `request` the Stage sent (ADR-028). */
export interface CoreResult {
  kind: "result";
  corrId: string;
  ok: boolean;
  payload: Record<string, unknown>;
  error: string | null;
}

export type CoreMessage = CoreCommand | CoreNotify | CoreWelcome | CoreResult;

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/**
 * Parses a received message. **`null` if it can't be parsed** (never executes anything silently).
 * A version mismatch throws `ProtocolVersionMismatch` so the connection can reject it visibly.
 *
 * A method outside `role`'s namespace is discarded. Core shouldn't send one, but
 * **even if it did, both the type and the implementation guarantee it never arrives.**
 */
export function parseCoreMessage(raw: string, role: CoreRole = "stage"): CoreMessage | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  const message = asRecord(value);
  if (message.v !== PROTOCOL_VERSION) {
    throw new ProtocolVersionMismatch(message.v);
  }

  switch (message.kind) {
    case "welcome":
      return { kind: "welcome" };
    case "command": {
      const { id, method } = message;
      if (
        typeof id !== "string" ||
        typeof method !== "string" ||
        !method.startsWith(NAMESPACE_BY_ROLE[role])
      ) {
        return null;
      }
      return { kind: "command", id, method, payload: asRecord(message.payload) };
    }
    case "result": {
      // The answer to something **the Stage asked for** (ADR-028).
      const { corr_id: corrId, ok } = message;
      if (typeof corrId !== "string" || typeof ok !== "boolean") {
        return null;
      }
      return {
        kind: "result",
        corrId,
        ok,
        payload: asRecord(message.payload),
        error: typeof message.error === "string" ? message.error : null,
      };
    }
    case "notify": {
      const { method } = message;
      if (typeof method !== "string" || !method.startsWith(NAMESPACE_BY_ROLE[role])) {
        return null;
      }
      return { kind: "notify", method, payload: asRecord(message.payload) };
    }
    default:
      return null;
  }
}

export function helloMessage(token: string, role: CoreRole = "stage"): string {
  return JSON.stringify({ v: PROTOCOL_VERSION, kind: "hello", role, token });
}

export function resultMessage(
  corrId: string,
  ok: boolean,
  payload: Record<string, unknown> = {},
  error?: string,
): string {
  return JSON.stringify({
    v: PROTOCOL_VERSION,
    kind: "result",
    corr_id: corrId,
    ok,
    payload,
    ...(ok ? {} : { error: error ?? "unknown_error" }),
  });
}

/**
 * A request from the Stage to Core (ADR-028).
 *
 * **Deliberately not called a command.** Core → Stage is a command (Core decided); this
 * direction is a request (Core decides). The names keep that asymmetry readable.
 */
export function requestMessage(
  id: string,
  method: string,
  payload: Record<string, unknown>,
): string {
  return JSON.stringify({ v: PROTOCOL_VERSION, kind: "request", id, method, payload });
}
