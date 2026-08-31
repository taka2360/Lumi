import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { connectToCore } from "./connection";

const shell = vi.hoisted(() => ({
  coreEndpoint: vi.fn(),
  onCoreEndpointChanged: vi.fn(),
}));

vi.mock("../platform/useStageShell", () => ({
  getPlatformShell: () => shell,
}));

describe("Core endpoint subscription", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    shell.coreEndpoint.mockReset();
    shell.onCoreEndpointChanged.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("reports registration failure without stopping retries or making close unsafe", async () => {
    const failure = new Error("listen failed");
    const report = vi.spyOn(console, "error").mockImplementation(() => {});
    shell.onCoreEndpointChanged.mockRejectedValue(failure);
    shell.coreEndpoint.mockResolvedValue(null);

    const connection = connectToCore({ commands: {}, notifications: {} });
    await vi.advanceTimersByTimeAsync(0);

    expect(report).toHaveBeenCalledWith("Failed to subscribe to Core endpoint changes", failure);
    expect(shell.coreEndpoint).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(500);

    expect(shell.coreEndpoint).toHaveBeenCalledTimes(2);
    expect(() => connection.close()).not.toThrow();
    expect(vi.getTimerCount()).toBe(0);
  });
});
