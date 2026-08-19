import { afterEach, describe, expect, it, vi } from "vitest";

const { toAssetUrl } = vi.hoisted(() => ({
  toAssetUrl: vi.fn((path: string) => `asset://test/${encodeURIComponent(path)}`),
}));

vi.mock("../platform/useStageShell", () => ({
  getPlatformShell: () => ({ toAssetUrl }),
}));

import { loadCharacter } from "./loadCharacter";

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
