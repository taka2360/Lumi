/**
 * The Stage → Core direction. **ADR-028.**
 *
 * The asymmetry is the thing being protected: Core → Stage is a `command` (Core decided),
 * Stage → Core is a `request` (Core decides). **If the two ever became one shape, the
 * boundary would stop being readable.**
 */

import { describe, expect, it } from "vitest";

import {
  PROTOCOL_VERSION,
  ProtocolVersionMismatch,
  parseCoreMessage,
  requestMessage,
} from "./protocol";

describe("sending a request", () => {
  it("is a request, never a command", () => {
    const frame = JSON.parse(requestMessage("r1", "stage.settings.update", { a: 1 }));
    expect(frame.kind).toBe("request");
    expect(frame.id).toBe("r1");
    expect(frame.method).toBe("stage.settings.update");
    expect(frame.payload).toEqual({ a: 1 });
    expect(frame.v).toBe(PROTOCOL_VERSION);
  });
});

describe("reading Core's answer", () => {
  function result(over: Record<string, unknown> = {}): string {
    return JSON.stringify({
      v: PROTOCOL_VERSION,
      kind: "result",
      corr_id: "r1",
      ok: true,
      payload: {},
      ...over,
    });
  }

  it("reads a successful answer", () => {
    const message = parseCoreMessage(result({ payload: { applied_at_next_start: true } }));
    expect(message).toEqual({
      kind: "result",
      corrId: "r1",
      ok: true,
      payload: { applied_at_next_start: true },
      error: null,
    });
  });

  it("carries the refusal reason", () => {
    // **"It failed" with no reason is unactionable**, so the reason travels back.
    const message = parseCoreMessage(result({ ok: false, error: "SettingsUnreadable" }));
    expect(message).toMatchObject({ ok: false, error: "SettingsUnreadable" });
  });

  it("refuses an answer with no correlation", () => {
    // Without it there is no way to know which request was answered.
    expect(parseCoreMessage(result({ corr_id: 42 }))).toBeNull();
    expect(parseCoreMessage(result({ ok: "yes" }))).toBeNull();
  });

  it("still refuses a version mismatch", () => {
    expect(() => parseCoreMessage(result({ v: 999 }))).toThrow(ProtocolVersionMismatch);
  });
});
