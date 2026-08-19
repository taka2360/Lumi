/**
 * The WS protocol with Core (Stage side) — **pure types and parsing only**.
 *
 * The corresponding definition on the Core side is `core/lumi/transport/protocol.py`.
 * **Fix both at the same time.**
 *
 * The Stage receives only `stage.*`. `os.*` never arrives, and even if it did it
 * would never be interpreted (**`stage.*` must never request OS privileges** → docs/architecture/core.md §3).
 */

export const PROTOCOL_VERSION = 1;

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
 *
 * A method not starting with `stage.` is discarded. Core shouldn't send one, but
 * **even if it did, both the type and the implementation guarantee the Stage never receives it.**
 */
export function parseCoreMessage(raw: string): CoreMessage | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  const message = asRecord(value);
  if (message.v !== PROTOCOL_VERSION) {
    return null;
  }

  switch (message.kind) {
    case "welcome":
      return { kind: "welcome" };
    case "command": {
      const { id, method } = message;
      if (typeof id !== "string" || typeof method !== "string" || !method.startsWith("stage.")) {
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
      if (typeof method !== "string" || !method.startsWith("stage.")) {
        return null;
      }
      return { kind: "notify", method, payload: asRecord(message.payload) };
    }
    default:
      return null;
  }
}

export function helloMessage(token: string): string {
  return JSON.stringify({ v: PROTOCOL_VERSION, kind: "hello", role: "stage", token });
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
