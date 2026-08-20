/**
 * The incomplete-setup screen. **Tests 16 / 23 from docs/architecture/ui.md §7 and
 * docs/architecture/setup.md §8.**
 *
 * The wording of individual lines is `status.test.ts`'s job. What is tested here is the
 * part that only exists once they are assembled: **that every missing thing is listed**,
 * that there is a way out of the screen, and that the way out actually quits.
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type SetupSnapshot, UNKNOWN_SETUP, useStageStore } from "../core/store";
import { LocaleProvider } from "../i18n/provider";
import { SetupPanel } from "./SetupPanel";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
vi.mock("../core/useCoreConnection", () => ({ answerSetupPrompt: vi.fn() }));

const quit = vi.fn();
vi.mock("../platform/useStageShell", () => ({
  useQuit: () => quit,
  getPlatformShell: () => ({ setLocale: async () => {} }),
}));

// **The wording is the feature**, so the tests read it in one fixed language rather than
// in whatever the host happens to report (jsdom says `en-US`). `LocaleProvider` reads this
// cache when Core has not sent a locale setting yet.
localStorage.setItem("lumi.locale", "ja");

/** Everything works. Individual tests take one thing away. */
function working(over: Partial<SetupSnapshot> = {}): SetupSnapshot {
  return {
    boot: "blocked",
    tts: { ...UNKNOWN_SETUP.tts, state: "installed", runtime: "ready", engine_name: "AivisSpeech" },
    llm: { ...UNKNOWN_SETUP.llm, state: "detected", runtime: "ready" },
    stt: {
      ...UNKNOWN_SETUP.stt,
      state: "installed",
      model: "large-v3-turbo",
      runtime: "ready",
    },
    ...over,
  };
}

describe("the incomplete-setup screen", () => {
  let root: ReturnType<typeof createRoot> | null = null;
  let container: HTMLDivElement | null = null;

  function render(setup: SetupSnapshot): HTMLDivElement {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      useStageStore.setState({ setup, prompt: null });
      root?.render(
        <LocaleProvider>
          <SetupPanel />
        </LocaleProvider>,
      );
    });
    return container;
  }

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container?.remove();
    container = null;
    useStageStore.setState({ setup: UNKNOWN_SETUP, prompt: null });
    quit.mockReset();
  });

  it("lists every missing component, not just the first", () => {
    // ★ **Fixing what one line asks for and then being handed the next** is how a
    // two-minute setup turns into three restarts (docs/architecture/ui.md §7 test 16).
    const view = render(
      working({
        tts: { ...UNKNOWN_SETUP.tts, state: "not_configured" },
        llm: { ...UNKNOWN_SETUP.llm, state: "not_configured" },
        stt: { ...UNKNOWN_SETUP.stt, state: "not_configured" },
      }),
    );
    const lines = view.querySelectorAll(".panel__status");

    expect(lines).toHaveLength(3);
    expect(view.textContent).toContain("音声合成エンジン");
    expect(view.textContent).toContain("Ollama");
    expect(view.textContent).toContain("音声認識モデル");
  });

  it("shows how to fix what Lumi will not fix itself", () => {
    // Lumi neither fetches nor starts Ollama (ADR-023), so the screen has to hand over
    // the command instead of a button.
    const view = render(
      working({ llm: { ...UNKNOWN_SETUP.llm, state: "model_missing", model: "qwen3:8b" } }),
    );

    expect(view.querySelector(".panel__hint")?.textContent).toBe("ollama pull qwen3:8b");
  });

  it("says a failed download failed, and why", () => {
    // ★ **A fetch that failed must never read as setup being done** (ADR-034). Before the
    // phase existed, the character came out and this line was a footnote beside it.
    const view = render(
      working({ stt: { ...UNKNOWN_SETUP.stt, state: "failed", reason: "hash_mismatch" } }),
    );

    expect(view.querySelector(".panel__status--bad")?.textContent).toContain(
      "内容が想定と違いました",
    );
  });

  it("says the work is not lost, and offers a way out", () => {
    const view = render(working({ stt: { ...UNKNOWN_SETUP.stt, state: "not_configured" } }));
    const buttons = view.querySelectorAll("button");

    expect(view.textContent).toContain("次回起動時にセットアップを再開できます");
    expect(buttons).toHaveLength(1);
    expect(buttons[0]?.textContent).toBe("終了");
  });

  it("quitting asks Shell to quit", () => {
    // **The only reachable exit.** `stage` is frameless and hidden from the taskbar, and
    // someone stopped here has never met the tray (docs/architecture/ui.md "Tray menu").
    const view = render(working({ stt: { ...UNKNOWN_SETUP.stt, state: "not_configured" } }));

    act(() => {
      view.querySelector("button")?.click();
    });

    expect(quit).toHaveBeenCalledOnce();
  });

  it("never claims setup is incomplete without saying what is incomplete", () => {
    // Should be unreachable — every blocking state produces a line. **If it ever shows,
    // Core's phase and the component states have drifted**, and that must be visible.
    const view = render({ ...UNKNOWN_SETUP, boot: "blocked" });

    expect(view.querySelectorAll(".panel__status")).toHaveLength(1);
    expect(view.textContent).toContain("セットアップの状態を確認できませんでした");
    expect(view.querySelectorAll("button")).toHaveLength(1);
  });

  it("does not list an engine that is merely warming up", () => {
    // ★ Regression (observed 2026-08-20): declining the speech model used to hand the user
    // "starting AivisSpeech…" for two minutes before admitting setup was incomplete. Core
    // now says `blocked` immediately — and **the engine coming up in the background must
    // not reappear here as a fourth thing to wait for.** It needs nothing from the user.
    const view = render(
      working({
        tts: { ...UNKNOWN_SETUP.tts, state: "installed", runtime: "starting" },
        stt: { ...UNKNOWN_SETUP.stt, state: "not_configured" },
      }),
    );
    const lines = view.querySelectorAll(".panel__status");

    expect(lines).toHaveLength(1);
    expect(view.textContent).toContain("音声認識モデル");
    expect(view.textContent).not.toContain("起動しています");
  });

  it("still says so when the engine turns out to be broken", () => {
    // The other half: **not waiting is not the same as not looking.** The engine is still
    // started in the background, and a failure to start is a second thing to fix — found
    // now rather than on the next run.
    const view = render(
      working({
        tts: {
          ...UNKNOWN_SETUP.tts,
          state: "installed",
          runtime: "failed",
          engine_name: "AivisSpeech",
        },
        stt: { ...UNKNOWN_SETUP.stt, state: "not_configured" },
      }),
    );

    expect(view.querySelectorAll(".panel__status")).toHaveLength(2);
    expect(view.querySelector(".panel__status--bad")?.textContent).toContain(
      "起動できませんでした",
    );
  });

  it("draws nothing at all once everything works", () => {
    const view = render(working({ boot: "ready" }));

    expect(view.querySelector(".panel")).toBeNull();
  });
});

describe("the question", () => {
  let root: ReturnType<typeof createRoot> | null = null;
  let container: HTMLDivElement | null = null;

  function render(retry: boolean): HTMLDivElement {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      useStageStore.setState({
        setup: working(),
        prompt: { component: "stt", retry, reason: retry ? "network_unreachable" : null },
      });
      root?.render(
        <LocaleProvider>
          <SetupPanel />
        </LocaleProvider>,
      );
    });
    return container;
  }

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container?.remove();
    container = null;
    useStageStore.setState({ setup: UNKNOWN_SETUP, prompt: null });
  });

  it("offers to fetch, and to not fetch, as equals", () => {
    // **ADR-019 principle 2.** The wording of the refusal changed to "not now" (ADR-034);
    // the two choices being presented as equals did not.
    const buttons = render(false).querySelectorAll("button");

    expect([...buttons].map((button) => button.textContent)).toEqual([
      "今は取得しない",
      "取得する",
    ]);
  });

  it("after a failure, says what failed and offers to try again", () => {
    // ★ **The failure is handed back with its reason**, never smoothed over. "Download"
    // would read as though the first attempt had not happened.
    const view = render(true);
    const buttons = view.querySelectorAll("button");

    expect(view.querySelector(".panel__status--bad")?.textContent).toContain(
      "ネットワークに接続できませんでした",
    );
    expect([...buttons].map((button) => button.textContent)).toEqual(["今は取得しない", "再試行"]);
  });
});
