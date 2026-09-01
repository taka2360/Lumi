/**
 * The boundaries `stage/` is held to. **Static checks over the module graph.**
 *
 * | # | Rule | Where it comes from |
 * |---|---|---|
 * | 1 | Only `platform/tauri.ts` imports Tauri | docs/interfaces/shell.md, ui.md §6 |
 * | 2 | `platform/` does not reach `core/` | ui.md §6 |
 * | 3 | `stage/` never names `os.*` | authority-matrix.md check #4, ui.md §7 test #4 |
 *
 * 1 and 3 were previously asserted only for the credits window (`credits/content.test.ts`),
 * which is the one place they happened to be written down — so the rest of the tree was
 * free to drift, and `core/connection.ts` had in fact done so.
 *
 * The graph itself, and the limits of what it can see, are in `../test/imports`.
 */

import { describe, expect, it } from "vitest";

import { reachableFrom, readStageModules } from "../test/imports";

const modules = readStageModules();

/**
 * **Guards the guard.** Every rule below is of the form "no module does X", which a walk
 * that found nothing would satisfy perfectly. So first: the walk found the tree.
 */
describe("the walk sees the tree", () => {
  it("reads the production modules", () => {
    expect(modules.size).toBeGreaterThan(20);
    expect([...modules.values()].map((module) => module.where)).toContain("/platform/tauri.ts");
  });

  it("resolves every relative import", () => {
    // A specifier that resolves to nothing looks exactly like a package: the walk stops,
    // and every rule downstream of that module passes by never having looked. Directory
    // indexes are the easy way to lose one, so an unresolved relative specifier is a
    // failure rather than a silently narrowed check.
    const dangling = [...modules.values()].flatMap((module) =>
      module.unresolved.map((specifier) => `${module.where} -> ${specifier}`),
    );
    expect(dangling).toEqual([]);
  });

  it("finds no dependency it cannot follow", () => {
    // `new Worker(new URL("./w.ts", import.meta.url))` and a computed `import(name)` both
    // reach a module the import graph cannot follow. The Stage has neither, and the
    // boundaries are documented over the import graph — so these are refused outright
    // rather than modelled with a home-grown bundler resolver. Adding one has to widen
    // ../test/imports and the guarantee in docs/architecture/ui.md §6 together.
    const opaque = [...modules.values()].flatMap((module) =>
      module.unfollowable.map((what) => `${module.where}: ${what}`),
    );
    expect(opaque).toEqual([]);
  });
});

describe("only platform/tauri.ts knows which shell is underneath", () => {
  const tauriImporters = [...modules.values()]
    .filter((module) => module.specifiers.some((specifier) => specifier.startsWith("@tauri-apps")))
    .map((module) => module.where);

  it("no other module imports Tauri", () => {
    // **The Electron escape route is the point** (docs/interfaces/shell.md): `PlatformShell`
    // is only worth having if replacing it is genuinely all that a port has to do.
    expect(tauriImporters).toEqual(["/platform/tauri.ts"]);
  });
});

describe("platform does not depend on Core", () => {
  it("reaches no core module", () => {
    // Transitively. A `platform/` module that goes through a shared helper into `core/`
    // depends on Core just as much as one that imports it directly.
    const offenders: string[] = [];
    for (const module of modules.values()) {
      if (!module.where.startsWith("/platform/")) {
        continue;
      }
      for (const reached of reachableFrom(modules, module.file)) {
        if (reached.where.startsWith("/core/")) {
          offenders.push(`${module.where} -> ${reached.where}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

/**
 * **`os.*` is Core's channel to Shell, and the Stage is not on it** (Invariant 1).
 *
 * Checked two ways, because a leak has two shapes. The rule as written in
 * `authority-matrix.md` is about the *types*, which live in Core and Rust — so an import
 * check alone would pass no matter what the Stage did. What a leak actually looks like
 * here is either an `Os*` name in the source or an `os.*` method name on the wire.
 *
 * The name check is over *identifiers*, not over the text, so it does not care how the
 * name was reached: a named import, an alias, a wildcard re-export resolved elsewhere in
 * the tree, `import("../protocol").OsCommand`, `Protocol.OsCommand`, or a local
 * declaration all put the same identifier in the syntax tree. Comments and strings are
 * other kinds of node, so a rule *discussing* `os.*` — as this one does — is not a
 * violation of it.
 */
describe("the Stage cannot reach os.*", () => {
  it("names no os.* type", () => {
    const offenders = [...modules.values()].flatMap((module) =>
      module.identifiers
        .filter((name) => /^Os[A-Z]/.test(name))
        .map((name) => `${module.where}: ${name}`),
    );
    expect(offenders).toEqual([]);
  });

  it("names no os.* method", () => {
    // `invoke("os.foo")`, a method constant, or anything else that would put the name on
    // the wire. **The `os.` prefix is the rule, not a whole method name**: a composed name
    // like `invoke(`os.${operation}`)` never exists as one literal, but its head does, and
    // the request API takes any string. Matching the prefix catches both, and the Stage has
    // no legitimate string under that namespace for it to catch by accident.
    const offenders = [...modules.values()].flatMap((module) =>
      module.strings
        .filter((text) => /^os\./i.test(text))
        .map((text) => `${module.where}: "${text}"`),
    );
    expect(offenders).toEqual([]);
  });

  it("imports no os.* module", () => {
    const offenders = [...modules.values()].flatMap((module) =>
      module.specifiers
        .filter((specifier) => /(^|\/)os\./.test(specifier))
        .map((specifier) => `${module.where}: ${specifier}`),
    );
    expect(offenders).toEqual([]);
  });
});
