import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useStageStore } from "../core/store";
import { LocaleProvider } from "../i18n/provider";
import { Memory } from "./Memory";
import { SEARCH_DEBOUNCE_MS } from "./useMemorySearch";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const callCore = vi.fn();
vi.mock("../core/request", () => ({
  callCore: (method: string, payload?: Record<string, unknown>) => callCore(method, payload),
}));
vi.mock("../platform/useStageShell", () => ({
  getPlatformShell: () => ({ setLocale: async () => {} }),
}));

function memory(overrides: Record<string, unknown> = {}) {
  return {
    id: "m1",
    type: "semantic",
    subject: "user.hobby",
    content: "ユーザーは Factorio が好き",
    assertion_mode: "user_stated",
    trust_level: "trusted",
    confidence: 0.9,
    effective_salience: 0.5,
    valid_from: "2026-08-23T12:00:00+00:00",
    archived: false,
    superseded_by: null,
    ...overrides,
  };
}

/**
 * Lets the effects that load a page settle before assertions.
 *
 * **Advances past the search debounce without depending on CI scheduling.** The promises
 * returned by Core's mock are flushed by the async timer advancement too.
 */
async function settle(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS);
  });
}

describe("memory window", () => {
  let root: ReturnType<typeof createRoot> | null = null;
  let container: HTMLDivElement | null = null;

  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.setItem("lumi.locale", "ja");
    useStageStore.setState({ connected: true, memoryRevision: 0 });
    callCore.mockResolvedValue({ items: [memory()], total: 1 });
  });

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container?.remove();
    container = null;
    callCore.mockReset();
    useStageStore.setState({ connected: false, memoryRevision: 0 });
    vi.useRealTimers();
  });

  async function render(): Promise<HTMLDivElement> {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root?.render(
        <LocaleProvider>
          <Memory />
        </LocaleProvider>,
      );
    });
    await settle();
    return container;
  }

  it("shows what Core sent, and how it is grounded", async () => {
    const view = await render();

    expect(view.querySelector(".memory__content")?.textContent).toBe("ユーザーは Factorio が好き");
    expect(view.querySelector(".memory__subject")?.textContent).toBe("user.hobby");
    expect(view.querySelector(".memory__mode")?.textContent).toBe("本人が言った");
  });

  it("marks a memory whose trust is not the user's own", async () => {
    // ★ **Invariant 7 is why this badge exists.** A belief assembled from outside text
    // looks exactly like one the user said, and the difference decides what Lumi may
    // act on.
    callCore.mockResolvedValue({ items: [memory({ trust_level: "tainted" })], total: 1 });

    const view = await render();

    expect(view.querySelector(".memory__taint")?.textContent).toBe("外部由来（未確認）");
  });

  it("does not offer to confirm what is already confirmed", async () => {
    callCore.mockResolvedValue({
      items: [memory({ assertion_mode: "user_confirmed" })],
      total: 1,
    });

    const view = await render();

    const labels = [...view.querySelectorAll(".memory__actions button")].map(
      (button) => button.textContent,
    );
    expect(labels).not.toContain("これで合っている");
  });

  it("asks before forgetting, and re-reads rather than removing the row itself", async () => {
    // ★ **Deletion is physical and cannot be undone** (privacy.md §5). It is also not
    // done here: the row goes away because Core's next answer no longer contains it.
    const view = await render();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    callCore.mockClear();
    callCore.mockResolvedValue({ deleted: 1 });

    await act(async () => {
      [...view.querySelectorAll("button")]
        .find((button) => button.textContent === "忘れさせる")
        ?.click();
    });
    await settle();

    expect(confirm).toHaveBeenCalled();
    expect(callCore.mock.calls[0]).toEqual(["panel.memory.forget", { id: "m1" }]);
    // The reload that follows the change, rather than a local splice.
    expect(callCore.mock.calls.some(([method]) => method === "panel.memory.search")).toBe(true);
    confirm.mockRestore();
  });

  it("keeps the memory when the confirmation is declined", async () => {
    const view = await render();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    callCore.mockClear();

    await act(async () => {
      [...view.querySelectorAll("button")]
        .find((button) => button.textContent === "忘れさせる")
        ?.click();
    });

    expect(callCore).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("never erases without showing what goes first", async () => {
    // ★ **The confirmation token comes from the preview** (ui.md §5b). The erase request
    // cannot be assembled without having asked Core what would be deleted, so there is no
    // path from a stray click to an empty database.
    const view = await render();
    callCore.mockClear();
    callCore.mockResolvedValue({
      targets: [
        { target: "memory_records", count: 1 },
        { target: "episodes", count: 0 },
      ],
      confirmation: "erase-everything",
    });

    await act(async () => {
      [...view.querySelectorAll("button")]
        .find((button) => button.textContent === "全部消す")
        ?.click();
    });
    await settle();

    expect(callCore.mock.calls[0]?.[0]).toBe("panel.memory.erase_preview");
    const rows = [...view.querySelectorAll(".memory__erase li")].map((row) => row.textContent);
    // **The empty row is listed too.** "There is none of that" and "that was left out"
    // must not look the same on the screen someone is about to trust.
    expect(rows).toEqual(["記憶: 1 件", "会話の記録: 0 件"]);

    callCore.mockClear();
    callCore.mockResolvedValue({ targets: [] });
    await act(async () => {
      [...view.querySelectorAll<HTMLButtonElement>(".memory__erase button")]
        .find((button) => button.textContent === "全部消す")
        ?.click();
    });
    await settle();

    expect(callCore.mock.calls[0]).toEqual([
      "panel.memory.erase",
      { confirmation: "erase-everything" },
    ]);
  });

  it("★ the erase dialog can be left by keyboard, and starts on Cancel", async () => {
    // This is the one screen in Lumi whose default action cannot be undone. Opening it
    // with focus on nothing means the first Enter goes wherever the browser decides.
    const view = await render();
    callCore.mockResolvedValue({ targets: [], confirmation: "erase-everything" });

    await act(async () => {
      [...view.querySelectorAll("button")]
        .find((button) => button.textContent === "全部消す")
        ?.click();
    });
    await settle();

    expect(document.activeElement?.textContent).toBe("やめる");

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });

    expect(view.querySelector(".memory__erase")).toBeNull();
  });

  it("warns that an export is readable before it is written", async () => {
    const view = await render();

    expect(view.querySelector(".memory__warning")?.textContent).toBe(
      "書き出したファイルは暗号化されません。誰でも読めます。",
    );
  });

  it("re-reads when Core says memory changed underneath it", async () => {
    await render();
    callCore.mockClear();

    await act(async () => {
      useStageStore.getState().nudgeMemory();
    });
    await settle();

    expect(callCore.mock.calls.some(([method]) => method === "panel.memory.search")).toBe(true);
  });
});
