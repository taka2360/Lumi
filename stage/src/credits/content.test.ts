/**
 * Acceptance criteria for the credits. **Corresponds to test 7 in docs/licensing.md §8.**
 *
 * Credits become a violation only when "something is missing." So what's checked
 * here is **that the necessary things are present** — not appearance.
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
  THIRD_PARTY,
} from "./content";

describe("credits structure", () => {
  it("every section from docs/licensing.md §6 is present", () => {
    expect(SECTIONS).toEqual([
      "lumi",
      "bundled",
      "external",
      "voice",
      "prohibitions",
      "third-party",
      "licenses",
    ]);
  });

  it("states that Lumi itself is MIT", () => {
    expect(LUMI.license).toBe("MIT");
    expect(LICENSES.some((l) => l.id === LUMI.licenseId)).toBe(true);
  });
});

describe("full license texts", () => {
  it("has both LGPL-3.0 and the GPL-3.0 it incorporates", () => {
    const ids = LICENSES.map((l) => l.id);
    expect(ids).toContain("lgpl-3.0");
    expect(ids).toContain("gpl-3.0");
  });

  it("has the full license text for the voice synthesis model", () => {
    const acml = LICENSES.find((l) => l.id === "acml-1.0");
    expect(acml).toBeDefined();
    // Confirms it's the "full text." A summary wouldn't be noticed if it got swapped out.
    expect(acml?.text).toContain("できないこと");
    expect(acml?.text).toContain("免責事項");
  });

  it("no full text is empty or abridged", () => {
    for (const license of LICENSES) {
      expect(license.text.length, license.id).toBeGreaterThan(900);
      expect(license.text, license.id).not.toContain("…");
    }
  });

  it("every referenced license's full text exists (no broken links)", () => {
    const ids = new Set(LICENSES.map((l) => l.id));
    for (const external of EXTERNAL) {
      for (const id of external.licenses) {
        expect(ids.has(id), `${external.name} references ${id} which lacks full text`).toBe(true);
      }
    }
  });
});

describe("things not bundled", () => {
  it("lists the engine names", () => {
    const names = EXTERNAL.map((e) => e.name);
    expect(names.some((n) => n.includes("AivisSpeech"))).toBe(true);
    expect(names.some((n) => n.includes("VOICEVOX"))).toBe(true);
  });

  it("**when it applies** is written for every entry (Phase 0 doesn't know what's in use)", () => {
    for (const external of EXTERNAL) {
      expect(external.appliesWhen.length, external.name).toBeGreaterThan(0);
      expect(external.source, external.name).toMatch(/^https:\/\//);
      expect(external.obligations.length, external.name).toBeGreaterThan(0);
    }
  });

  it("states that the engine fetches a model externally on first run", () => {
    const aivis = EXTERNAL.find((e) => e.name.includes("AivisSpeech"));
    expect(aivis?.obligations.join("")).toContain("AivisHub");
  });
});

describe("voice credits and prohibitions", () => {
  it("has a VOICEVOX-format credit example", () => {
    expect(CREDIT_EXAMPLES.length).toBeGreaterThan(0);
    for (const example of CREDIT_EXAMPLES) {
      expect(example).toMatch(/^VOICEVOX:/);
    }
  });

  it("lists prohibitions from both ACML and VOICEVOX (sublicensing obligation)", () => {
    const sources = PROHIBITIONS.map((p) => p.source);
    expect(sources.some((s) => s.includes("ACML"))).toBe(true);
    expect(sources.some((s) => s.includes("VOICEVOX"))).toBe(true);
    for (const set of PROHIBITIONS) {
      expect(set.items.length, set.source).toBeGreaterThan(0);
    }
  });
});

describe("credits never depend on Core", () => {
  it("nothing under credits/ imports the Core or Shell paths", () => {
    // **Credits must be readable even if Core is down**
    // (docs/licensing.md §8 test 10 / docs/architecture/ui.md).
    // A single import slipping in would silently break this guarantee.
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
      expect(text, `${file} references Core path`).not.toMatch(/from\s+"\.\.\/core\//);
      expect(text, `${file} references Shell path`).not.toMatch(/from\s+"\.\.\/platform\//);
      // The **name** of the dependency is listed (naturally, since this is credits). What's forbidden is the import.
      expect(text, `${file} imports Tauri API`).not.toMatch(/from\s+"@tauri-apps/);
    }
  });
});

describe("bundled OSS", () => {
  it("lists all three of Shell / Stage / Core", () => {
    expect(BUNDLED).toHaveLength(3);
    for (const component of BUNDLED) {
      expect(component.dependencies.length, component.component).toBeGreaterThan(0);
    }
  });

  it("no license identifier is empty", () => {
    for (const component of BUNDLED) {
      for (const dep of component.dependencies) {
        expect(dep.license, dep.name).not.toBe("");
        expect(dep.version, dep.name).not.toBe("");
      }
    }
  });

  it("the Stage's dependencies and versions match package.json", () => {
    // **Credits go stale because adding a dependency goes unnoticed.**
    // If this fails, fix content.ts.
    const stage = BUNDLED.find((c) => c.component.includes("Stage"));
    const listed = new Map(stage?.dependencies.map((d) => [d.name, d.version]));
    for (const [name, range] of Object.entries(pkg.dependencies)) {
      expect(listed.has(name), `${name} is not listed in credits`).toBe(true);
      expect(listed.get(name), name).toBe(range.replace(/^[\^~]/, ""));
    }
    expect(listed.size).toBe(Object.keys(pkg.dependencies).length);
  });
});

describe("the complete third-party list (generated)", () => {
  const all = THIRD_PARTY.ecosystems.flatMap((e) => e.packages);

  it("has all 3 ecosystems plus the ones absent from any dependency graph", () => {
    // **The last one is easy to miss.** CPython, PortAudio, and the PyInstaller
    // bootloader appear in no dependency graph, yet ship in the distributable.
    expect(THIRD_PARTY.ecosystems).toHaveLength(4);
    expect(all.length).toBe(THIRD_PARTY.total);
  });

  it("nothing has an unknown license across all ecosystems", () => {
    // Unknown = obligations can't be determined. **Never ship something that can't be determined.**
    for (const ecosystem of THIRD_PARTY.ecosystems) {
      for (const dep of ecosystem.packages) {
        expect(dep.license, `${ecosystem.name}: ${dep.name}`).not.toBe("");
        expect(dep.license, `${ecosystem.name}: ${dep.name}`).not.toBe("不明");
        expect(dep.license, `${ecosystem.name}: ${dep.name}`).not.toBe("Unknown");
      }
    }
  });

  it("no GPL / AGPL exists other than the one with an exception clause", () => {
    // The Core = MIT boundary (docs/licensing.md §1). The generation script runs
    // the same check, but **a check that looks at the generated output itself**
    // lives here (still fails even if someone forgets to run the script).
    const copyleft = all.filter((dep) => /GPL/i.test(dep.license));
    expect(copyleft.map((dep) => dep.name)).toEqual(["PyInstaller bootloader"]);
    expect(copyleft[0]?.license).toContain("Bootloader-exception");
  });

  it("nothing Lumi depends on heavily is missing", () => {
    const names = new Set(all.map((dep) => dep.name));
    for (const name of ["tauri", "sqlite-vec", "sounddevice", "three", "react", "PortAudio"]) {
      expect(names.has(name), `${name} is missing from the list`).toBe(true);
    }
  });
});
