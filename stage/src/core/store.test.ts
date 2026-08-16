/**
 * Reading state broadcast by Core. **Never conveniently reinterprets an unknown value.**
 *
 * Design → docs/architecture/ui.md "Boot phases"
 */

import { describe, expect, it } from "vitest";

import { toTtsSnapshot } from "./store";

describe("boot phase", () => {
  it("holds the phase Core broadcast, unchanged", () => {
    for (const boot of ["setup", "installing", "starting", "ready"]) {
      expect(toTtsSnapshot({ boot, state: "installed" }).boot).toBe(boot);
    }
  });

  it("never rounds an unknown phase to ready", () => {
    // **fail-closed.** Rounding to ready would show the character before it's ready.
    expect(toTtsSnapshot({ boot: "???", state: "installed" }).boot).toBe("starting");
    expect(toTtsSnapshot({ state: "installed" }).boot).toBe("starting");
    expect(toTtsSnapshot({ boot: 1, state: "installed" }).boot).toBe("starting");
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
