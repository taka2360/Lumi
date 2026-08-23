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
const { answerSetupPrompt, recheckOllama, quit, openOllamaSite } = vi.hoisted(() => ({
  answerSetupPrompt: vi.fn(),
  recheckOllama: vi.fn(async () => {}),
  quit: vi.fn(),
  openOllamaSite: vi.fn(),
}));
vi.mock("../core/useCoreConnection", () => ({
  answerSetupPrompt,
  recheckOllama,
}));

vi.mock("../platform/useStageShell", () => ({
  useQuit: () => quit,
  useOpenOllamaSite: () => openOllamaSite,
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
    embedding: { ...UNKNOWN_SETUP.embedding, state: "installed" },
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
    openOllamaSite.mockReset();
    recheckOllama.mockClear();
    answerSetupPrompt.mockClear();
    vi.clearAllTimers();
    vi.useRealTimers();
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

    expect(lines).toHaveLength(2);
    expect(view.textContent).toContain("音声合成エンジン");
    expect(view.textContent).toContain("Ollama");
    expect(view.textContent).toContain("音声認識モデル");
  });

  it("guides a missing Ollama install without a manual recheck action", () => {
    const view = render(working({ llm: { ...UNKNOWN_SETUP.llm, state: "not_configured" } }));
    const buttons = [...view.querySelectorAll("button")];

    expect(view.textContent).toContain("Ollama が見つかりません");
    expect(view.textContent).toContain("AI モデルを PC 上で動かすために使用します");
    expect(buttons.map((button) => button.textContent)).toEqual(["公式サイトを開く", "終了"]);

    act(() => buttons[0]?.click());
    expect(openOllamaSite).toHaveBeenCalledOnce();
  });

  it("rechecks Ollama every second while the missing screen is visible", async () => {
    vi.useFakeTimers();
    render(working({ llm: { ...UNKNOWN_SETUP.llm, state: "not_configured" } }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(recheckOllama).toHaveBeenCalledOnce();
  });

  it("waits automatically while an installed Ollama may still be starting", () => {
    const view = render(
      working({
        llm: {
          ...UNKNOWN_SETUP.llm,
          state: "detected",
          runtime: "starting",
          reason: "ollama_starting",
        },
      }),
    );

    expect(view.textContent).toContain("Ollama のセットアップを完了しています");
    expect(view.textContent).toContain("Ollama の起動を待っています");
    expect(view.querySelector(".boot__spinner")).not.toBeNull();
    expect([...view.querySelectorAll("button")].map((button) => button.textContent)).toEqual([
      "終了",
    ]);
  });

  it("only asks the user to start Ollama after the grace period", () => {
    const view = render(
      working({
        llm: { ...UNKNOWN_SETUP.llm, state: "detected", runtime: "stopped" },
      }),
    );

    expect(view.textContent).toContain("Ollama はインストールされていますが");
    expect(view.textContent).toContain("ローカル API が応答していません");
    expect(view.textContent).toContain("Ollama を起動してください");
    expect(view.textContent).not.toContain("公式サイトを開く");
  });

  it("shows detection while the model is being confirmed", () => {
    const view = render(
      working({
        llm: { ...UNKNOWN_SETUP.llm, state: "detected", runtime: "starting" },
      }),
    );

    expect(view.textContent).toContain("Ollama を検出しました");
    expect(view.textContent).toContain("モデルを確認しています");
    expect(view.querySelector(".boot__spinner")).not.toBeNull();
  });

  it("offers the missing model through an explicit consent prompt", () => {
    const view = render(
      working({ llm: { ...UNKNOWN_SETUP.llm, state: "model_missing", model: "qwen3:8b" } }),
    );

    expect(view.textContent).toContain("Lumiのセットアップから取得できます");
    expect(view.querySelector(".panel__hint")).toBeNull();
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
        prompt: {
          component: "stt",
          retry,
          reason: retry ? "network_unreachable" : null,
          model: null,
          alternatives: [],
          items: [],
          totalBytes: 0,
        },
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
    answerSetupPrompt.mockClear();
  });

  it("names and sizes the recommended model before downloading it", () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      useStageStore.setState({
        setup: working(),
        prompt: {
          component: "llm_model",
          retry: false,
          reason: null,
          model: {
            model: "qwen3.5:9b",
            display_name: "Qwen 3.5 9B",
            size_bytes: 6_600_000_000,
            installed: false,
          },
          alternatives: [
            {
              model: "qwen3.5:4b",
              display_name: "Qwen 3.5 4B",
              size_bytes: 3_400_000_000,
              installed: false,
            },
          ],
          items: [],
          totalBytes: 0,
        },
      });
      root?.render(
        <LocaleProvider>
          <SetupPanel />
        </LocaleProvider>,
      );
    });

    expect(container.textContent).toContain("Qwen 3.5 9B");
    expect(container.textContent).toContain("6.6 GB");
    expect([...container.querySelectorAll("button")].map((button) => button.textContent)).toEqual([
      "Qwen 3.5 9B（約 6.6 GB）をダウンロード",
      "別のモデルを選ぶ",
      "今は取得しない",
    ]);

    act(() => container?.querySelectorAll("button")[1]?.click());
    expect(container.textContent).toContain("Qwen 3.5 4B（約 3.4 GB）をダウンロード");

    act(() => container?.querySelector("button")?.click());
    expect(answerSetupPrompt).toHaveBeenCalledWith("install", "qwen3.5:4b");
  });

  it("shows local Ollama models and selects them without a download", () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      useStageStore.setState({
        setup: working(),
        prompt: {
          component: "llm_model",
          retry: false,
          reason: null,
          model: {
            model: "qwen3.5:9b",
            display_name: "Qwen 3.5 9B",
            size_bytes: 6_600_000_000,
            installed: false,
          },
          alternatives: [
            {
              model: "llama3.1:8b",
              display_name: "llama3.1:8b",
              size_bytes: 4_200_000_000,
              installed: true,
            },
          ],
          items: [],
          totalBytes: 0,
        },
      });
      root?.render(
        <LocaleProvider>
          <SetupPanel />
        </LocaleProvider>,
      );
    });

    act(() => {
      [...(container?.querySelectorAll("button") ?? [])]
        .find((button) => button.textContent === "別のモデルを選ぶ")
        ?.click();
    });

    expect(container.textContent).toContain("使用するAIモデルを選択");
    expect(container.textContent).toContain("Ollamaで利用できるモデルを選択してください");
    expect(container.textContent).toContain("llama3.1:8b（約 4.2 GB・ローカル）を使用");

    act(() => {
      [...(container?.querySelectorAll("button") ?? [])]
        .find((button) => button.textContent?.includes("ローカル"))
        ?.click();
    });
    expect(answerSetupPrompt).toHaveBeenCalledWith("select", "llama3.1:8b");
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

describe("the question that comes before the others", () => {
  let root: ReturnType<typeof createRoot> | null = null;
  let container: HTMLDivElement | null = null;

  function render(): HTMLDivElement {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      useStageStore.setState({
        setup: working(),
        prompt: {
          component: "all",
          retry: false,
          reason: null,
          model: null,
          alternatives: [],
          items: [
            { component: "tts", name: "AivisSpeech Engine", size_bytes: 300_000_000 },
            { component: "llm_model", name: "Qwen 3.5 9B", size_bytes: 6_600_000_000 },
            { component: "embedding", name: "harrier-oss-v1-270m", size_bytes: 196_000_000 },
          ],
          totalBytes: 7_096_000_000,
        },
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
    answerSetupPrompt.mockClear();
    useStageStore.setState({ prompt: null });
  });

  it("shows the total and what makes it up", () => {
    // ★ **The total is the number the decision is made on**, and an itemisation nobody
    // can add up is decoration. Both are on screen, in units that keep the difference
    // between 196 MB and 6.6 GB visible.
    const view = render();

    expect(view.querySelector(".panel__body")?.textContent).toContain("7.1 GB");
    const rows = [...view.querySelectorAll(".panel__fetch-list li")].map((row) => row.textContent);
    expect(rows).toEqual([
      "AivisSpeech Engine300 MB",
      "Qwen 3.5 9B6.6 GB",
      "harrier-oss-v1-270m196 MB",
    ]);
  });

  it("offers all three answers, and they are three different answers", () => {
    // **"one at a time" is not "no".** Collapsing them would mean the only way to pick
    // and choose is to decline everything and hope to be asked again.
    const view = render();
    const buttons = [...view.querySelectorAll<HTMLButtonElement>(".panel__button")];

    expect(buttons.map((button) => button.textContent)).toEqual([
      "まとめて取得する（7.1 GB）",
      "個別に選ぶ",
      "今は取得しない",
    ]);

    act(() => buttons[0]?.click());
    act(() => buttons[1]?.click());
    act(() => buttons[2]?.click());
    expect(answerSetupPrompt.mock.calls).toEqual([["install"], ["individually"], ["skip"]]);
  });
});
