/**
 * Core との WS プロトコル（Stage 側）— **純粋な型と解釈だけ**。
 *
 * Core 側の定義は `core/lumi/transport/protocol.py`。**両方を同時に直す。**
 *
 * Stage が受け取るのは `stage.*` だけ。`os.*` は届かないし、届いても解釈しない
 * （**`stage.*` は絶対に OS 特権を要求しない** → docs/architecture/core.md §3）。
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

export type CoreMessage = CoreCommand | CoreNotify | CoreWelcome;

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/**
 * 受信メッセージを解釈する。**解釈できないものは null**（黙って何かを実行しない）。
 *
 * `stage.` で始まらない method は捨てる。Core は送ってこないはずだが、
 * **送られてきても Stage が受け取らない**ことを型と実装の両方で担保する。
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
    kind: "result",
    corr_id: corrId,
    ok,
    payload,
    ...(ok ? {} : { error: error ?? "unknown_error" }),
  });
}
