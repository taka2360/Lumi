import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  extractExportedDeclarations,
  extractModuleBindings,
  extractModuleSpecifiers,
  extractWildcardReExports,
  reachableFrom,
  relativeToSrc,
  resolveModule,
  SRC,
} from "./test/imports";

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

describe("extractModuleBindings", () => {
  it("extracts original and local names from static imports and exports", () => {
    const source = `
      import DefaultCommand, * as Protocol from "../protocol";
      import type { OsCommand, OsCapture as Capture } from "../shared";
      export { type OsInput as Input, CoreCommand } from "../commands";
      export type * as OsTypes from "../os-types";
    `;

    expect(extractModuleBindings(source)).toEqual([
      "DefaultCommand",
      "Protocol",
      "OsCommand",
      "OsCapture",
      "Capture",
      "OsInput",
      "Input",
      "CoreCommand",
      "OsTypes",
    ]);
  });
});

describe("extractExportedDeclarations", () => {
  it("extracts names a module declares and exports itself", () => {
    const source = `
      export interface OsCommand { id: string }
      export type OsResult = { ok: boolean };
      export const osChannel = "channel";
      export default class Renderer {}
      export async function connect() {}
      export type { OsInput } from "../commands";
      export * from "./shared";
      const notExported = 1;
    `;

    expect(extractExportedDeclarations(source)).toEqual([
      "OsCommand",
      "OsResult",
      "osChannel",
      "Renderer",
      "connect",
    ]);
  });
});

describe("extractWildcardReExports", () => {
  it("extracts the re-exports that name nothing", () => {
    const source = `
      export * from "./shared";
      export type * from "../protocol";
      export * as OsTypes from "../os-types";
      export { OsInput } from "../commands";
    `;

    expect(extractWildcardReExports(source)).toEqual(["./shared", "../protocol"]);
  });
});

/**
 * A tree the walk can be held to, rather than the real one.
 *
 * The boundaries are only worth what the traversal sees. A specifier the resolver misses
 * looks exactly like a package — the walk stops, and every rule downstream of that module
 * passes by never having looked. Directory indexes are the easy way to lose one, so both
 * spellings are laid out here and the walk is required to reach through them.
 */
describe("walking a directory-index dependency", () => {
  let root: string;

  beforeAll(() => {
    root = mkdtempSync(join(tmpdir(), "lumi-import-walk-"));
    mkdirSync(join(root, "shared"));
    mkdirSync(join(root, "widgets"));
    writeFileSync(
      join(root, "entry.tsx"),
      ['import "./shared";', 'import "./widgets";'].join("\n"),
    );
    writeFileSync(join(root, "shared", "index.ts"), 'import "../leaf";');
    writeFileSync(join(root, "widgets", "index.tsx"), 'import "../leaf";');
    writeFileSync(join(root, "leaf.ts"), "export const leaf = 1;");
  });

  afterAll(() => {
    rmSync(root, { recursive: true, force: true });
  });

  it("resolves both index spellings", () => {
    const entry = join(root, "entry.tsx");
    expect(resolveModule(entry, "./shared")).toBe(join(root, "shared", "index.ts"));
    expect(resolveModule(entry, "./widgets")).toBe(join(root, "widgets", "index.tsx"));
  });

  it("reaches what an index forwards to", () => {
    // Without the `.tsx` candidate the walk ends at `entry.tsx`, and anything `widgets/`
    // imports — Core, the Shell, Tauri — is invisible to every rule built on this walk.
    expect([...reachableFrom(join(root, "entry.tsx")).keys()].sort()).toEqual(
      [
        join(root, "entry.tsx"),
        join(root, "leaf.ts"),
        join(root, "shared", "index.ts"),
        join(root, "widgets", "index.tsx"),
      ].sort(),
    );
  });

  it("still treats an unresolvable specifier as a package", () => {
    expect(resolveModule(join(root, "entry.tsx"), "./nowhere")).toBeNull();
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
