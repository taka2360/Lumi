/**
 * What the setup panel says. **The wording is the feature.**
 *
 * Design → docs/architecture/setup.md §2 / §2b
 *
 * Three components can each be missing for different reasons, and each reason asks
 * something different of the user. Getting one wrong sends them to fix something that
 * isn't broken — which is how someone ends up doubting their own machine rather than Lumi.
 */

import { describe, expect, it } from "vitest";

import type { LlmSetupSnapshot, SttSetupSnapshot, TtsSetupSnapshot } from "../core/store";
import { UNKNOWN_SETUP } from "../core/store";
import { failureText, llmStatus, statusLines, sttStatus, ttsStatus } from "./status";

function tts(over: Partial<TtsSetupSnapshot> = {}): TtsSetupSnapshot {
  return { ...UNKNOWN_SETUP.tts, ...over };
}

function llm(over: Partial<LlmSetupSnapshot> = {}): LlmSetupSnapshot {
  return { ...UNKNOWN_SETUP.llm, ...over };
}

function stt(over: Partial<SttSetupSnapshot> = {}): SttSetupSnapshot {
  return { ...UNKNOWN_SETUP.stt, ...over };
}

describe("nothing to say", () => {
  it("a working setup produces no lines at all", () => {
    // **The panel is not drawn when everything is fine.** A status area that is always
    // present trains people to stop reading it.
    const lines = statusLines({
      boot: "ready",
      tts: tts({ state: "installed", runtime: "ready" }),
      llm: llm({ state: "detected", runtime: "ready" }),
      stt: stt({ state: "installed", runtime: "ready" }),
    });
    expect(lines).toEqual([]);
  });

  it("says nothing while it still knows nothing", () => {
    // **`unknown` is "not checked yet."** Reporting it as a problem would flash a
    // complaint on every startup.
    expect(statusLines(UNKNOWN_SETUP)).toEqual([]);
  });
});

describe("TTS", () => {
  it("not set up is stated as a limitation, not an error", () => {
    // **Still `normal`, not `bad`** (ADR-034 changed the consequence, not the tone):
    // setup being unfinished is a state, and only an actual failure is worded as one.
    const line = ttsStatus(tts({ state: "not_configured" }));
    expect(line?.tone).toBe("normal");
    expect(line?.text).toContain("喋れません");
  });

  it("installed-but-will-not-start is not painted over by installed", () => {
    // ★ **The process state is checked first.** Otherwise the user is told everything
    // is installed while nothing speaks (docs/architecture/setup.md).
    const line = ttsStatus(
      tts({ state: "installed", runtime: "failed", engine_name: "AivisSpeech" }),
    );
    expect(line?.tone).toBe("bad");
    expect(line?.text).toContain("入ってはいますが");
  });

  it("a failed fetch says what happened, not just that it failed", () => {
    expect(ttsStatus(tts({ state: "failed", reason: "hash_mismatch" }))?.text).toBe(
      "取得したファイルの内容が想定と違いました",
    );
  });
});

describe("LLM", () => {
  it("missing Ollama asks the user to install it themselves", () => {
    // **Lumi never fetches Ollama** (ADR-023), so this is the one message that asks
    // the user to go and do something.
    const line = llmStatus(llm({ state: "not_configured" }));
    expect(line?.text).toContain("返事ができません");
    expect(line?.hint).toContain("ollama.com");
  });

  it("a missing model gives the exact command", () => {
    const line = llmStatus(llm({ state: "model_missing", model: "qwen3.5:9b" }));
    expect(line?.hint).toBe("ollama pull qwen3.5:9b");
  });

  it("★ never tells someone to install what they already have", () => {
    // "not installed" and "installed but not running" are indistinguishable over HTTP,
    // which is exactly why they must not share a sentence
    // (docs/architecture/setup.md §2b).
    const stopped = llmStatus(llm({ state: "detected", runtime: "stopped" }));
    expect(stopped?.text).toContain("起動していません");
    expect(stopped?.text).not.toContain("見つかりません");
    expect(stopped?.hint).toBeUndefined();
  });

  it("a working Ollama says nothing", () => {
    expect(llmStatus(llm({ state: "detected", runtime: "ready" }))).toBeNull();
  });

  it("no LLM is never coloured as a fault", () => {
    // **"Listens and says nothing" is a state Lumi is allowed to be in.**
    for (const state of ["not_configured", "model_missing"] as const) {
      expect(llmStatus(llm({ state }))?.tone).toBe("normal");
    }
  });
});

describe("STT", () => {
  it("not set up is stated as a limitation, not an error", () => {
    const line = sttStatus(stt({ state: "not_configured" }));
    expect(line?.tone).toBe("normal");
    expect(line?.text).toContain("聞き取れません");
  });

  it("shows fetch progress", () => {
    expect(sttStatus(stt({ state: "installing", progress: 0.42 }))?.text).toContain("42%");
  });

  it("a failed fetch keeps the reason", () => {
    const line = sttStatus(stt({ state: "failed", reason: "network_unreachable" }));
    expect(line?.tone).toBe("bad");
    expect(line?.text).toBe("ネットワークに接続できませんでした");
  });

  it("an installed model that cannot load is a runtime failure", () => {
    const line = sttStatus(stt({ state: "installed", runtime: "failed" }));
    expect(line?.tone).toBe("bad");
    expect(line?.text).toContain("取得済みですが");
    expect(line?.text).not.toContain("取得に失敗");
  });
});

describe("several at once", () => {
  it("says every one of them", () => {
    // **A first run with nothing set up has three separate things to say.** Showing
    // only the first would make the next one appear out of nowhere later.
    const lines = statusLines({
      boot: "ready",
      tts: tts({ state: "not_configured" }),
      llm: llm({ state: "not_configured" }),
      stt: stt({ state: "not_configured" }),
    });
    expect(lines).toHaveLength(3);
  });
});

describe("failure reasons", () => {
  it("an unknown reason is still shown", () => {
    // **Never swallowed.** A reason nobody has written wording for is still a reason.
    expect(failureText("something_new")).toContain("something_new");
  });

  it("no reason at all still says it failed", () => {
    expect(failureText(null)).toBe("取得に失敗しました");
  });

  it("localizes known reasons and preserves unknown reason ids in English", () => {
    expect(failureText("hash_mismatch", "en")).toBe(
      "The downloaded file did not match the expected contents",
    );
    expect(failureText("something_new", "en")).toContain("something_new");
  });
});
