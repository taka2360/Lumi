/**
 * Core から配られた状態の読み取り。**知らない値を都合よく解釈しない。**
 *
 * 設計 → docs/architecture/ui.md「起動フェーズ」
 */

import { describe, expect, it } from "vitest";

import { toTtsSnapshot } from "./store";

describe("起動フェーズ", () => {
  it("Core が配ったフェーズをそのまま持つ", () => {
    for (const boot of ["setup", "installing", "starting", "ready"]) {
      expect(toTtsSnapshot({ boot, state: "installed" }).boot).toBe(boot);
    }
  });

  it("知らないフェーズは ready にしない", () => {
    // **fail-closed。** ready に丸めると、準備できていないのにキャラクターが出る。
    expect(toTtsSnapshot({ boot: "???", state: "installed" }).boot).toBe("starting");
    expect(toTtsSnapshot({ state: "installed" }).boot).toBe("starting");
    expect(toTtsSnapshot({ boot: 1, state: "installed" }).boot).toBe("starting");
  });
});

describe("TTS の状態", () => {
  it("導入の状態とプロセスの状態を別々に読む", () => {
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

  it("知らない状態は unknown / stopped にする", () => {
    const snapshot = toTtsSnapshot({ state: "???", runtime: "???" });
    expect(snapshot.state).toBe("unknown");
    expect(snapshot.runtime).toBe("stopped");
  });

  it("型の合わない値は null にする（勝手に補完しない）", () => {
    const snapshot = toTtsSnapshot({ engine_name: 42, port: "10101", progress: "half" });
    expect(snapshot.engine_name).toBeNull();
    expect(snapshot.port).toBeNull();
    expect(snapshot.progress).toBeNull();
  });
});
