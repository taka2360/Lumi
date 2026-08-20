import { afterEach, describe, expect, it, vi } from "vitest";

const { toAssetUrl } = vi.hoisted(() => ({
  toAssetUrl: vi.fn((path: string) => `asset://test/${encodeURIComponent(path)}`),
}));

vi.mock("../platform/useStageShell", () => ({
  getPlatformShell: () => ({ toAssetUrl }),
}));

import { CHARACTER_MODEL_REASONS } from "../core/methods";
import { loadCharacter, modelReasonText } from "./loadCharacter";

describe("loadCharacter", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("asks PlatformShell to convert the Core-provided model path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    vi.stubGlobal("fetch", fetchMock);
    const path = "C:\\content\\characters\\lumi\\model.vrm";

    const loaded = await loadCharacter({ path, reason: "" });

    expect(toAssetUrl).toHaveBeenCalledWith(path);
    expect(fetchMock).toHaveBeenCalledWith(`asset://test/${encodeURIComponent(path)}`, {
      method: "HEAD",
    });
    expect(loaded.model.kind).toBe("placeholder");
  });

  it("turns an asset URL conversion failure into the existing placeholder", async () => {
    toAssetUrl.mockImplementationOnce(() => {
      throw new Error("shell unavailable");
    });
    vi.stubGlobal("fetch", vi.fn());

    const loaded = await loadCharacter({ path: "model.vrm", reason: "" });

    expect(loaded.model.kind).toBe("placeholder");
    expect(loaded.fallbackReason).toContain("shell unavailable");
  });
});

describe("why there is no model", () => {
  it("translates every reason Core can send, in both locales", async () => {
    // **Core sends a code; the wording is ours** (ADR-036). Before this, Core put a
    // Japanese sentence on the wire and it reached the screen untranslated — the one
    // line that a language change could not move.
    for (const reason of CHARACTER_MODEL_REASONS) {
      for (const locale of ["ja", "en"] as const) {
        const text = modelReasonText(reason, locale);
        expect(text).not.toBe("");
        expect(text).not.toBe(reason);
      }
    }
  });

  it("gives ja and en different wording", () => {
    // A missing entry in one table would silently fall through to the other's text.
    for (const reason of CHARACTER_MODEL_REASONS) {
      expect(modelReasonText(reason, "ja")).not.toBe(modelReasonText(reason, "en"));
    }
  });

  it("★ shows an unknown code verbatim rather than blanking it", () => {
    // **Forward compatibility.** Core may ship a new reason before the Stage learns its
    // wording. A raw code on screen is legible and searchable; an empty caption is a
    // placeholder with no explanation, which is exactly what the reason exists to prevent.
    expect(modelReasonText("future_reason", "ja")).toBe("future_reason");
    expect(modelReasonText("future_reason", "en")).toBe("future_reason");
  });

  it("says nothing when there is nothing to say", () => {
    expect(modelReasonText("", "ja")).toBe("");
  });

  it("reaches the placeholder caption", async () => {
    const loaded = await loadCharacter({ path: null, reason: "model_not_in_pack" });

    expect(loaded.model.kind).toBe("placeholder");
    expect(loaded.fallbackReason).not.toBe("model_not_in_pack");
    expect(loaded.fallbackReason).toBeTruthy();
  });
});
