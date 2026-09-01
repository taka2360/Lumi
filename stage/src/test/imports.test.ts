/**
 * The module reader, held to a tree laid out on purpose.
 *
 * The boundaries are only worth what the reader sees. A dependency it misses looks
 * exactly like no dependency at all — the walk stops, and every rule downstream of that
 * module passes by never having looked. So the shapes that could lose one are written
 * out here and the reader is required to find them.
 *
 * The fixture is a real TypeScript project because the reader is a real TypeScript
 * program: it is the compiler, not a regex, that answers what a module specifier is.
 */

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { reachableFrom, readModules, type StageModule } from "./imports";

const TSCONFIG = JSON.stringify({
  compilerOptions: {
    target: "ES2022",
    module: "ESNext",
    moduleResolution: "bundler",
    allowImportingTsExtensions: true,
    noEmit: true,
  },
  include: ["src"],
});

/** The fixture's files, keyed by path below `src`. */
const FILES: Record<string, string> = {
  // Directory indexes in both spellings, an asset, and a dynamic import with a literal.
  "entry.tsx": [
    'import "./shared";',
    'import "./widgets";',
    'import "./styles.css";',
    'const lazy = import("./lazy");',
    "export { lazy };",
  ].join("\n"),
  "shared/index.ts": 'import "../leaf";',
  "widgets/index.tsx": 'import "../leaf";',
  "leaf.ts": "export const leaf = 1;",
  "lazy.ts": "export const lazy = 1;",

  // The four ways an `Os*` name reaches a module without being written in an import
  // clause as itself. The regex parser this replaced missed the last three in turn.
  "types.ts": "export interface OsCommand { id: string }",
  "barrel.ts": 'export * from "./types";',
  "names.ts": [
    'import type { OsCapture as Capture } from "./capture";',
    'import * as Protocol from "./types";',
    'export type Inline = import("./types").OsCommand;',
    "export type Qualified = Protocol.OsCommand;",
    "export type Local = Capture;",
  ].join("\n"),
  "capture.ts": "export interface OsCapture { id: string }",

  // Comment markers inside a string used to truncate the line and hide what followed.
  "literals.ts": [
    "// os.capture, discussed in a comment",
    "/* os.probe, discussed in a block comment */",
    'export const values = ["https://help", "os.capture"];',
  ].join("\n"),

  // The shapes that are refused rather than modelled.
  "worker.ts":
    'export const w = new Worker(new URL("./leaf.ts", import.meta.url), { type: "module" });',
  "computed.ts": ["export const name = './leaf';", "export const mod = import(name);"].join("\n"),
  "dangling.ts": 'import "./nowhere";',
};

describe("readModules", () => {
  let root: string;
  let modules: Map<string, StageModule>;
  const at = (where: string): StageModule => {
    const module = modules.get(`${root}/src/${where}`);
    if (module === undefined) {
      throw new Error(`fixture module ${where} was not read`);
    }
    return module;
  };

  beforeAll(() => {
    root = mkdtempSync(join(tmpdir(), "lumi-imports-")).replaceAll("\\", "/");
    mkdirSync(join(root, "src", "shared"), { recursive: true });
    mkdirSync(join(root, "src", "widgets"), { recursive: true });
    writeFileSync(join(root, "tsconfig.json"), TSCONFIG);
    writeFileSync(join(root, "src", "styles.css"), "body { margin: 0 }");
    for (const [where, text] of Object.entries(FILES)) {
      writeFileSync(join(root, "src", where), `${text}\n`);
    }
    modules = readModules(root);
  });

  afterAll(() => {
    rmSync(root, { recursive: true, force: true });
  });

  it("reads every production module", () => {
    expect([...modules.values()].map((module) => module.where).sort()).toEqual(
      Object.keys(FILES)
        .map((where) => `/${where}`)
        .sort(),
    );
  });

  it("reaches through directory indexes in either spelling", () => {
    // Without the `.tsx` candidate the walk ends at `entry.tsx`, and anything `widgets/`
    // imports — Core, the Shell, Tauri — is invisible to every rule built on this walk.
    expect(
      reachableFrom(modules, at("entry.tsx").file)
        .map((module) => module.where)
        .sort(),
    ).toEqual(["/entry.tsx", "/lazy.ts", "/leaf.ts", "/shared/index.ts", "/widgets/index.tsx"]);
  });

  it("treats a literal dynamic import as a dependency", () => {
    expect(at("entry.tsx").specifiers).toContain("./lazy");
  });

  it("does not mistake an asset for a missing module", () => {
    expect(at("entry.tsx").specifiers).toContain("./styles.css");
    expect(at("entry.tsx").unresolved).toEqual([]);
  });

  it("refuses a relative specifier that resolves to nothing", () => {
    expect(at("dangling.ts").unresolved).toEqual(["./nowhere"]);
  });

  it("follows a wildcard re-export to where the name is declared", () => {
    // `export * from "./types"` names nothing, so the forwarded `OsCommand` can only be
    // seen where it is declared. It is — as long as the wildcard resolves inside the tree,
    // which "refuses a relative specifier that resolves to nothing" is what insists on.
    expect(at("barrel.ts").identifiers).not.toContain("OsCommand");
    expect(at("types.ts").identifiers).toContain("OsCommand");
    expect(at("barrel.ts").dependencies).toEqual([`${root}/src/types.ts`]);
  });

  it("sees an Os* name however it is spelled", () => {
    // An alias hides the original name from the local scope but not from the source; an
    // inline import type and a qualified reference name it without an import clause.
    expect(at("names.ts").identifiers).toEqual(
      expect.arrayContaining(["OsCapture", "Capture", "OsCommand", "Protocol"]),
    );
  });

  it("keeps a string whole and leaves comments out of it", () => {
    const literals = at("literals.ts");
    expect(literals.strings).toEqual(["https://help", "os.capture"]);
    // The `//` in the URL used to end the line for the comment stripper, hiding the
    // `os.capture` beside it. And a comment is not a string, so discussing `os.probe`
    // in prose is not a violation.
    expect(literals.strings).not.toContain("os.probe");
  });

  it("refuses a worker entry point", () => {
    expect(at("worker.ts").unfollowable).toEqual(["new Worker(…)"]);
  });

  it("refuses a computed dynamic import", () => {
    expect(at("computed.ts").unfollowable).toEqual(["import(<computed>)"]);
    expect(at("computed.ts").specifiers).toEqual([]);
  });
});
