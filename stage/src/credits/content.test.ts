/**
 * クレジットの受け入れ条件。**docs/licensing.md §8 のテスト 7 に対応する。**
 *
 * クレジットは「欠けていること」だけが違反になる。したがってここで検査するのは
 * **必要なものが在ること**であって、見栄えではない。
 */

import { describe, expect, it } from "vitest";

import pkg from "../../package.json" with { type: "json" };
import {
  BUNDLED,
  CREDIT_EXAMPLES,
  EXTERNAL,
  LICENSES,
  LUMI,
  PROHIBITIONS,
  SECTIONS,
} from "./content";

describe("クレジットの構成", () => {
  it("docs/licensing.md §6 の節がすべて在る", () => {
    expect(SECTIONS).toEqual(["lumi", "bundled", "external", "voice", "prohibitions", "licenses"]);
  });

  it("Lumi 本体が MIT だと書いてある", () => {
    expect(LUMI.license).toBe("MIT");
    expect(LICENSES.some((l) => l.id === LUMI.licenseId)).toBe(true);
  });
});

describe("ライセンス全文", () => {
  it("LGPL-3.0 と、それが取り込む GPL-3.0 の両方が入っている", () => {
    const ids = LICENSES.map((l) => l.id);
    expect(ids).toContain("lgpl-3.0");
    expect(ids).toContain("gpl-3.0");
  });

  it("音声合成モデルのライセンス全文が入っている", () => {
    const acml = LICENSES.find((l) => l.id === "acml-1.0");
    expect(acml).toBeDefined();
    // 「全文」であることの確認。要約だと差し替わったときに気づけない。
    expect(acml?.text).toContain("できないこと");
    expect(acml?.text).toContain("免責事項");
  });

  it("全文が空でも省略でもない", () => {
    for (const license of LICENSES) {
      expect(license.text.length, license.id).toBeGreaterThan(900);
      expect(license.text, license.id).not.toContain("…");
    }
  });

  it("参照されているライセンス全文がすべて存在する（リンク切れが無い）", () => {
    const ids = new Set(LICENSES.map((l) => l.id));
    for (const external of EXTERNAL) {
      for (const id of external.licenses) {
        expect(ids.has(id), `${external.name} が参照する ${id} の全文が無い`).toBe(true);
      }
    }
  });
});

describe("同梱していないもの", () => {
  it("エンジン名が載っている", () => {
    const names = EXTERNAL.map((e) => e.name);
    expect(names.some((n) => n.includes("AivisSpeech"))).toBe(true);
    expect(names.some((n) => n.includes("VOICEVOX"))).toBe(true);
  });

  it("**いつ適用されるか**が全部に書いてある（Phase 0 は使用中のものを知らない）", () => {
    for (const external of EXTERNAL) {
      expect(external.appliesWhen.length, external.name).toBeGreaterThan(0);
      expect(external.source, external.name).toMatch(/^https:\/\//);
      expect(external.obligations.length, external.name).toBeGreaterThan(0);
    }
  });

  it("エンジンが初回起動時に外部からモデルを取ることが書いてある", () => {
    const aivis = EXTERNAL.find((e) => e.name.includes("AivisSpeech"));
    expect(aivis?.obligations.join("")).toContain("AivisHub");
  });
});

describe("音源のクレジットと禁止事項", () => {
  it("VOICEVOX 形式のクレジット例がある", () => {
    expect(CREDIT_EXAMPLES.length).toBeGreaterThan(0);
    for (const example of CREDIT_EXAMPLES) {
      expect(example).toMatch(/^VOICEVOX:/);
    }
  });

  it("ACML と VOICEVOX の両方の禁止事項が載っている（再許諾義務）", () => {
    const sources = PROHIBITIONS.map((p) => p.source);
    expect(sources.some((s) => s.includes("ACML"))).toBe(true);
    expect(sources.some((s) => s.includes("VOICEVOX"))).toBe(true);
    for (const set of PROHIBITIONS) {
      expect(set.items.length, set.source).toBeGreaterThan(0);
    }
  });
});

describe("クレジットは Core に依存しない", () => {
  it("credits 配下から Core・Shell の経路を import していない", () => {
    // **Core が落ちていてもクレジットは読めなければならない**
    // （docs/licensing.md §8 テスト10 / docs/architecture/ui.md）。
    // import が1つ入るだけで、この保証は黙って壊れる。
    const sources = import.meta.glob("./*.{ts,tsx}", {
      query: "?raw",
      import: "default",
      eager: true,
    }) as Record<string, string>;

    expect(Object.keys(sources).length).toBeGreaterThan(1);
    for (const [file, text] of Object.entries(sources)) {
      if (file.endsWith(".test.ts")) {
        continue;
      }
      expect(text, `${file} が Core の経路を参照している`).not.toMatch(/from\s+"\.\.\/core\//);
      expect(text, `${file} が Shell の経路を参照している`).not.toMatch(/from\s+"\.\.\/platform\//);
      // 依存の**名前**は載る（クレジットなので当然）。禁止するのは import。
      expect(text, `${file} が Tauri API を import している`).not.toMatch(/from\s+"@tauri-apps/);
    }
  });
});

describe("同梱している OSS", () => {
  it("Shell / Stage / Core の3つとも載っている", () => {
    expect(BUNDLED).toHaveLength(3);
    for (const component of BUNDLED) {
      expect(component.dependencies.length, component.component).toBeGreaterThan(0);
    }
  });

  it("ライセンス識別子が空のものが無い", () => {
    for (const component of BUNDLED) {
      for (const dep of component.dependencies) {
        expect(dep.license, dep.name).not.toBe("");
        expect(dep.version, dep.name).not.toBe("");
      }
    }
  });

  it("Stage の依存とバージョンが package.json と一致する", () => {
    // **クレジットが古くなるのは、依存を足したときに気づかないから。**
    // ここで落ちたら content.ts を直す。
    const stage = BUNDLED.find((c) => c.component.includes("Stage"));
    const listed = new Map(stage?.dependencies.map((d) => [d.name, d.version]));
    for (const [name, range] of Object.entries(pkg.dependencies)) {
      expect(listed.has(name), `${name} がクレジットに載っていない`).toBe(true);
      expect(listed.get(name), name).toBe(range.replace(/^[\^~]/, ""));
    }
    expect(listed.size).toBe(Object.keys(pkg.dependencies).length);
  });
});
