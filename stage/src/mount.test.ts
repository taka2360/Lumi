import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { extractStaticSpecifiers, relativeToSrc, resolveModule, SRC } from "./test/imports";

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
      const where = relativeToSrc(file);
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
