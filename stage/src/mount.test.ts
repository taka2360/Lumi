/**
 * The windows that **never connect to Core** must not be able to reach the code that does.
 *
 * `credits` and `help` are separate pages for exactly this reason
 * (docs/architecture/ui.md §1 "`credits` を Core に繋がない理由"). `credits/content.test.ts`
 * already forbids the import inside `src/credits/`, but that check cannot see through a
 * shared module: once these entries started sharing `mount.tsx`, a Core import added
 * there would reach both pages without either directory changing.
 *
 * **So this walks the actual import graph from the two entry points.**
 */

import { readFileSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = join(__dirname);

/** Static import/export specifiers, including side-effect-only imports. */
function extractStaticSpecifiers(text: string): string[] {
  return Array.from(
    text.matchAll(/(?:import|export)\s+(?:type\s+)?(?:[^"'`;]*?\s+from\s+)?["']([^"']+)["']/g),
    (match) => match[1],
  ).filter((specifier): specifier is string => specifier !== undefined);
}

/** Resolves a relative specifier the way the bundler does, trying each extension. */
function resolveModule(fromFile: string, specifier: string): string | null {
  const base = resolve(dirname(fromFile), specifier);
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, join(base, "index.ts")]) {
    try {
      readFileSync(candidate, "utf8");
      return candidate;
    } catch {
      // Not this one. A specifier that resolves to nothing is a package, not our source.
    }
  }
  return null;
}

/** Every source file reachable from `entry`, following relative imports only. */
function reachableFrom(entry: string): Map<string, string[]> {
  const seen = new Map<string, string[]>();
  const queue = [entry];
  while (queue.length > 0) {
    const file = queue.pop();
    if (file === undefined || seen.has(file)) {
      continue;
    }
    let text: string;
    try {
      text = readFileSync(file, "utf8");
    } catch {
      continue;
    }
    const specifiers = extractStaticSpecifiers(text);
    seen.set(file, specifiers);
    for (const specifier of specifiers) {
      if (!specifier.startsWith(".")) {
        continue;
      }
      const next = resolveModule(file, specifier);
      if (next !== null) {
        queue.push(next);
      }
    }
  }
  return seen;
}

describe("extractStaticSpecifiers", () => {
  it("extracts regular, side-effect, and re-export specifiers", () => {
    const source = `
      import { connect } from "../core/client";
      import "../core/connection";
      export { invoke } from '../platform/shell';
      export * from "./shared";
      const lazy = import("../core/lazy");
    `;

    expect(extractStaticSpecifiers(source)).toEqual([
      "../core/client",
      "../core/connection",
      "../platform/shell",
      "./shared",
    ]);
  });
});

describe.each([
  ["credits", join(SRC, "credits", "main.tsx")],
  ["help", join(SRC, "help", "main.tsx")],
])("the %s window", (name, entry) => {
  it("reaches no module that talks to Core or the Shell", () => {
    const reachable = reachableFrom(entry);
    // The walk found something, or the assertions below would pass vacuously.
    expect(reachable.size, `${name} entry resolved nothing`).toBeGreaterThan(1);

    for (const [file, specifiers] of reachable) {
      const where = file.slice(SRC.length).split(sep).join("/");
      expect(specifiers, `${where} (reachable from ${name}) imports Core`).not.toEqual(
        expect.arrayContaining([expect.stringMatching(/\/core\//)]),
      );
      expect(specifiers, `${where} (reachable from ${name}) imports the Shell`).not.toEqual(
        expect.arrayContaining([expect.stringMatching(/\/platform\//)]),
      );
      expect(specifiers, `${where} (reachable from ${name}) imports Tauri`).not.toEqual(
        expect.arrayContaining([expect.stringMatching(/^@tauri-apps/)]),
      );
    }
  });
});
