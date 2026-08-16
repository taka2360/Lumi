import { describe, expect, it } from "vitest";

import { helloMessage, PROTOCOL_VERSION, parseCoreMessage, resultMessage } from "./protocol";

const envelope = (extra: Record<string, unknown>) =>
  JSON.stringify({ v: PROTOCOL_VERSION, ...extra });

describe("parseCoreMessage", () => {
  it("receives welcome", () => {
    expect(parseCoreMessage(envelope({ kind: "welcome" }))).toEqual({ kind: "welcome" });
  });

  it("receives a stage.* command", () => {
    const raw = envelope({ kind: "command", id: "a", method: "stage.setup.prompt", payload: {} });
    expect(parseCoreMessage(raw)).toEqual({
      kind: "command",
      id: "a",
      method: "stage.setup.prompt",
      payload: {},
    });
  });

  it("receives a stage.* notify", () => {
    const raw = envelope({ kind: "notify", method: "stage.setup.state", payload: { state: "x" } });
    expect(parseCoreMessage(raw)).toEqual({
      kind: "notify",
      method: "stage.setup.state",
      payload: { state: "x" },
    });
  });

  it("never receives os.* (the stage never handles OS privileges)", () => {
    const raw = envelope({ kind: "command", id: "a", method: "os.input.click", payload: {} });
    expect(parseCoreMessage(raw)).toBeNull();
  });

  it("discards malformed input", () => {
    expect(parseCoreMessage("not json")).toBeNull();
    expect(parseCoreMessage(envelope({ kind: "command", method: "stage.x" }))).toBeNull();
    expect(parseCoreMessage(JSON.stringify({ v: 2, kind: "welcome" }))).toBeNull();
    expect(parseCoreMessage(envelope({ kind: "unknown" }))).toBeNull();
  });
});

describe("outgoing messages", () => {
  it("hello identifies as role=stage", () => {
    expect(JSON.parse(helloMessage("t"))).toEqual({
      v: PROTOCOL_VERSION,
      kind: "hello",
      role: "stage",
      token: "t",
    });
  });

  it("a failed result always carries a reason", () => {
    expect(JSON.parse(resultMessage("c", false))).toMatchObject({
      corr_id: "c",
      ok: false,
      error: "unknown_error",
    });
  });

  it("a successful result never carries error", () => {
    expect(JSON.parse(resultMessage("c", true, { choice: "skip" }))).toEqual({
      kind: "result",
      corr_id: "c",
      ok: true,
      payload: { choice: "skip" },
    });
  });
});
