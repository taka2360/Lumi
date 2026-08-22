/**
 * Reading the payloads Core broadcasts. **Never conveniently reinterprets an unknown value.**
 *
 * Design → docs/architecture/ui.md "Boot phases"
 *
 * These are pure functions, so every case here is an input and an expected output —
 * **including the malformed ones**, which are the point. Lumi falls back rather than
 * throwing, so "what a broken payload becomes" is a decision, not an accident, and a
 * decision belongs in a test.
 */

import { describe, expect, it } from "vitest";

import { visemeAt } from "../character/lipsync";
import {
  toCharacterModel,
  toSetupPrompt,
  toSetupSnapshot,
  toSpeech,
  toTtsSnapshot,
  toUserSaid,
} from "./payloads";

describe("boot phase", () => {
  it("holds the phase Core broadcast, unchanged", () => {
    for (const boot of ["setup", "installing", "starting", "blocked", "ready"]) {
      expect(toSetupSnapshot({ boot }).boot).toBe(boot);
    }
  });

  it("never rounds an unknown phase to ready", () => {
    // **fail-closed.** Rounding to ready would show the character before it's ready.
    expect(toSetupSnapshot({ boot: "???" }).boot).toBe("starting");
    expect(toSetupSnapshot({}).boot).toBe("starting");
    expect(toSetupSnapshot({ boot: 1 }).boot).toBe("starting");
  });

  it("reads all three components from one message", () => {
    // **The phase is a function of all three.** Reading them from separate messages
    // could not guarantee ordering.
    const setup = toSetupSnapshot({
      boot: "ready",
      tts: { state: "installed", runtime: "ready" },
      llm: { state: "model_missing", model: "qwen3.5:9b" },
      stt: { state: "not_configured", model: "small", runtime: "starting" },
    });
    expect(setup.tts.state).toBe("installed");
    expect(setup.llm.state).toBe("model_missing");
    expect(setup.llm.model).toBe("qwen3.5:9b");
    expect(setup.stt.state).toBe("not_configured");
    expect(setup.stt.runtime).toBe("starting");
  });

  it("treats a missing component as unknown, never as a failure", () => {
    // An older Core, or one mid-detection. **Unknown is a state, not a fault.**
    const setup = toSetupSnapshot({ boot: "ready" });
    expect(setup.llm.state).toBe("unknown");
    expect(setup.stt.state).toBe("unknown");
    expect(setup.stt.runtime).toBe("stopped");
  });

  it("refuses a state that belongs to a different component", () => {
    // `detected` is TTS-only and `model_missing` is LLM-only. **Cross-assignment
    // falls back to unknown** rather than rendering a sentence that cannot be true.
    expect(toSetupSnapshot({ llm: { state: "installing" } }).llm.state).toBe("unknown");
    expect(toSetupSnapshot({ stt: { state: "detected" } }).stt.state).toBe("unknown");
  });
});

describe("TTS state", () => {
  it("reads installation state and process state separately", () => {
    const snapshot = toTtsSnapshot({
      boot: "starting",
      state: "installed",
      runtime: "starting",
      engine_name: "AivisSpeech Engine",
      version: "1.2.0",
      port: 10101,
      progress: 0.5,
    });
    expect(snapshot.state).toBe("installed");
    expect(snapshot.runtime).toBe("starting");
    expect(snapshot.engine_name).toBe("AivisSpeech Engine");
    expect(snapshot.port).toBe(10101);
    expect(snapshot.progress).toBe(0.5);
  });

  it("falls back an unknown state to unknown / stopped", () => {
    const snapshot = toTtsSnapshot({ state: "???", runtime: "???" });
    expect(snapshot.state).toBe("unknown");
    expect(snapshot.runtime).toBe("stopped");
  });

  it("turns a value of the wrong type into null (never guessed at)", () => {
    const snapshot = toTtsSnapshot({ engine_name: 42, port: "10101", progress: "half" });
    expect(snapshot.engine_name).toBeNull();
    expect(snapshot.port).toBeNull();
    expect(snapshot.progress).toBeNull();
  });
});

describe("speech", () => {
  const spans = [{ viseme: "A", start_ms: 0, duration_ms: 100 }];

  it("reads the text and the mouth timeline", () => {
    const speech = toSpeech({ text: "こんにちは。", spans, total_ms: 100 }, 42);
    expect(speech.text).toBe("こんにちは。");
    expect(speech.timeline.totalMs).toBe(100);
    expect(speech.startedAtMs).toBe(42);
  });

  it("still shows the text when there is no timeline", () => {
    // Core omits `spans` whenever the engine returns no timing. **A still mouth and a
    // blank bubble are different failures** — dropping the event caused both.
    const speech = toSpeech({ text: "聞こえてる？" }, 0);
    expect(speech.text).toBe("聞こえてる？");
    expect(speech.timeline.spans).toEqual([]);
  });

  it("leaves the mouth closed for the whole of an absent timeline", () => {
    const { timeline } = toSpeech({ text: "あ" }, 0);
    for (const at of [0, 100, 10_000]) {
      expect(visemeAt(timeline, at)).toBeNull();
    }
  });

  it("turns a missing text into an empty string (never `undefined` on screen)", () => {
    expect(toSpeech({ spans, total_ms: 100 }, 0).text).toBe("");
    expect(toSpeech({ text: 42 }, 0).text).toBe("");
  });
});

describe("what the user said", () => {
  it("reads the text", () => {
    expect(toUserSaid({ text: "おはよう" }, 7)).toEqual({ text: "おはよう", startedAtMs: 7 });
  });

  it("a drifted payload becomes a blank bubble rather than none", () => {
    // Core only sends this once STT produced something, so an empty string means drift.
    // **A blank bubble says that far more loudly than no bubble at all.**
    expect(toUserSaid({}, 0).text).toBe("");
    expect(toUserSaid({ text: 42 }, 0).text).toBe("");
  });
});

describe("the setup question", () => {
  it("reads which component is being asked about", () => {
    expect(toSetupPrompt({ component: "stt" }).component).toBe("stt");
    expect(toSetupPrompt({ component: "tts", retry: true, reason: "http_error" })).toEqual({
      component: "tts",
      retry: true,
      reason: "http_error",
      model: null,
      alternatives: [],
    });
  });

  it("reads the model identity and byte size without guessing", () => {
    expect(
      toSetupPrompt({
        component: "llm_model",
        model: { model: "qwen3.5:9b", display_name: "Qwen 3.5 9B", size_bytes: 6_600_000_000 },
        alternatives: [
          { model: "qwen3.5:4b", display_name: "Qwen 3.5 4B", size_bytes: 3_400_000_000 },
        ],
      }),
    ).toMatchObject({
      component: "llm_model",
      model: { model: "qwen3.5:9b", size_bytes: 6_600_000_000 },
      alternatives: [{ model: "qwen3.5:4b", size_bytes: 3_400_000_000, installed: false }],
    });
  });

  it("names a component even when the payload does not", () => {
    // **A consent dialog with no subject is worse than one naming the likely subject.**
    expect(toSetupPrompt({}).component).toBe("tts");
    expect(toSetupPrompt({ component: "gpu" }).component).toBe("tts");
  });
});

describe("which model to draw", () => {
  it("reads the path and format Core decided", () => {
    const model = toCharacterModel({
      path: "C:Lumicontentcharacterslumimodel.vrm",
      format: "vrm0",
      credit: { name: "光莉 / ひかり", credit_text: "3Dモデル: 光莉 / ひかり（あわ）" },
    });

    expect(model.path).toBe("C:Lumicontentcharacterslumimodel.vrm");
    expect(model.format).toBe("vrm0");
    expect(model.reason).toBe("");
  });

  it("★ carries the reason when the pack ships no model", () => {
    // **A voice-only Content Pack is a legitimate pack.** The placeholder needs a reason,
    // or it reads as a bug rather than as a state (docs/DESIGN.md「黙って劣化しない」).
    const model = toCharacterModel({ path: null, reason: "model_not_in_pack" });

    expect(model.path).toBeNull();
    expect(model.reason).toBe("model_not_in_pack");
  });

  it("uses the stable fallback reason code when no model reason is provided", () => {
    expect(toCharacterModel({ path: null, reason: "" }).reason).toBe("model_not_in_pack");
    expect(toCharacterModel({ path: "", reason: "" }).reason).toBe("model_not_in_pack");
  });
});
