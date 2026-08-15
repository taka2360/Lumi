import { describe, expect, it } from "vitest";

import { helloMessage, PROTOCOL_VERSION, parseCoreMessage, resultMessage } from "./protocol";

const envelope = (extra: Record<string, unknown>) =>
  JSON.stringify({ v: PROTOCOL_VERSION, ...extra });

describe("parseCoreMessage", () => {
  it("welcome を受け取る", () => {
    expect(parseCoreMessage(envelope({ kind: "welcome" }))).toEqual({ kind: "welcome" });
  });

  it("stage.* の command を受け取る", () => {
    const raw = envelope({ kind: "command", id: "a", method: "stage.setup.prompt", payload: {} });
    expect(parseCoreMessage(raw)).toEqual({
      kind: "command",
      id: "a",
      method: "stage.setup.prompt",
      payload: {},
    });
  });

  it("stage.* の notify を受け取る", () => {
    const raw = envelope({ kind: "notify", method: "stage.setup.state", payload: { state: "x" } });
    expect(parseCoreMessage(raw)).toEqual({
      kind: "notify",
      method: "stage.setup.state",
      payload: { state: "x" },
    });
  });

  it("os.* は受け取らない（stage は OS 特権を扱わない）", () => {
    const raw = envelope({ kind: "command", id: "a", method: "os.input.click", payload: {} });
    expect(parseCoreMessage(raw)).toBeNull();
  });

  it("壊れたものは捨てる", () => {
    expect(parseCoreMessage("not json")).toBeNull();
    expect(parseCoreMessage(envelope({ kind: "command", method: "stage.x" }))).toBeNull();
    expect(parseCoreMessage(JSON.stringify({ v: 2, kind: "welcome" }))).toBeNull();
    expect(parseCoreMessage(envelope({ kind: "unknown" }))).toBeNull();
  });
});

describe("送信メッセージ", () => {
  it("hello は role=stage を名乗る", () => {
    expect(JSON.parse(helloMessage("t"))).toEqual({
      v: PROTOCOL_VERSION,
      kind: "hello",
      role: "stage",
      token: "t",
    });
  });

  it("失敗した result には必ず理由が付く", () => {
    expect(JSON.parse(resultMessage("c", false))).toMatchObject({
      corr_id: "c",
      ok: false,
      error: "unknown_error",
    });
  });

  it("成功した result に error を付けない", () => {
    expect(JSON.parse(resultMessage("c", true, { choice: "skip" }))).toEqual({
      kind: "result",
      corr_id: "c",
      ok: true,
      payload: { choice: "skip" },
    });
  });
});
