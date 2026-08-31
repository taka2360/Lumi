import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { extractModuleSpecifiers, reachableFrom, relativeToSrc, SRC } from "./test/imports";

describe("extractModuleSpecifiers", () => {
  it("extracts static and literal dynamic module specifiers", () => {
    const source = `
      import { connect } from "../core/client";
      import "../core/connection";
      export { invoke } from '../platform/shell';
      export * from "./shared";
      const lazy = import("../core/lazy");
      const lazyTemplate = import(\`../core/lazy-template\`);
      const computedName = "../core/computed";
      const computed = import(computedName);
    `;

    expect(extractModuleSpecifiers(source)).toEqual([
      "../core/client",
      "../core/connection",
      "../platform/shell",
      "./shared",
      "../core/lazy",
      "../core/lazy-template",
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
