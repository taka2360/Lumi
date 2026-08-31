/**
 * The boundaries `stage/` is held to. **Static checks over the source tree.**
 *
 * | # | Rule | Where it comes from |
 * |---|---|---|
 * | 1 | Only `platform/tauri.ts` imports Tauri | docs/interfaces/shell.md |
 * | 2 | `stage/` never references `os.*` | authority-matrix.md check #4, ui.md §7 test #4 |
 * | 3 | `platform/` does not import `core/` | ui.md §6 |
 *
 * Both were previously only asserted for the credits window (`credits/content.test.ts`),
 * which is the one place they happened to be written down — so the rest of the tree was
 * free to drift, and `core/connection.ts` had in fact done so.
 */

import { describe, expect, it } from "vitest";

import {
  extractExportedDeclarations,
  extractModuleBindings,
  extractModuleSpecifiers,
  extractWildcardReExports,
  productionSources,
  reachableFrom,
  relativeToSrc,
  resolveModule,
  withoutComments,
} from "../test/imports";

describe("platform does not depend on Core", () => {
  it("reaches no core module", () => {
    const offenders: string[] = [];
    for (const [file] of productionSources()) {
      const where = relativeToSrc(file);
      if (!where.startsWith("/platform/")) {
        continue;
      }
      for (const target of reachableFrom(file).keys()) {
        const dependency = relativeToSrc(target);
        if (dependency.startsWith("/core/")) {
          offenders.push(`${where} -> ${dependency}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe("only platform/tauri.ts knows which shell is underneath", () => {
  it("no other module imports Tauri", () => {
    const offenders: string[] = [];
    for (const [file, text] of productionSources()) {
      const where = relativeToSrc(file);
      const importsTauri = extractModuleSpecifiers(text).some((specifier) =>
        specifier.startsWith("@tauri-apps"),
      );
      if (importsTauri && where !== "/platform/tauri.ts") {
        offenders.push(where);
      }
    }
    // **The Electron escape route is the point** (docs/interfaces/shell.md): `PlatformShell`
    // is only worth having if replacing it is genuinely all that a port has to do.
    expect(offenders).toEqual([]);
  });

  it("finds the one file that is allowed to", () => {
    // Guards the guard: if the walk ever stopped seeing sources, the test above would
    // pass by finding nothing at all.
    const tauriImporters = [...productionSources()]
      .filter(([, text]) =>
        extractModuleSpecifiers(text).some((specifier) => specifier.startsWith("@tauri-apps")),
      )
      .map(([file]) => relativeToSrc(file));
    expect(tauriImporters).toContain("/platform/tauri.ts");
  });
});

/**
 * **`os.*` is Core's channel to Shell, and the Stage is not on it** (Invariant 1).
 *
 * Checked several ways on purpose. The rule as written in `authority-matrix.md` is about
 * the *types*, but those live in Core and Rust, so an import check alone would pass today
 * no matter what the Stage did — green while guarding nothing. What a leak would actually
 * look like here is a method name on the wire, so the names are checked too. And a name
 * can arrive without being imported — declared here, or forwarded by a wildcard re-export
 * — so declarations are checked, and wildcards are held to modules this walk can read.
 */
describe("the Stage cannot reach os.*", () => {
  it("imports, re-exports or declares no os.* type", () => {
    for (const [file, text] of productionSources()) {
      const names = [...extractModuleBindings(text), ...extractExportedDeclarations(text)];
      for (const name of names) {
        expect(name, `${relativeToSrc(file)} names an os.* type`).not.toMatch(/^Os[A-Z]/);
      }
      for (const specifier of extractModuleSpecifiers(text)) {
        expect(specifier, `${relativeToSrc(file)} imports an os.* module`).not.toMatch(
          /(^|\/)os\./,
        );
      }
    }
  });

  it("forwards nothing it cannot name", () => {
    // `export * from "./x"` puts names on a module's surface without writing them down, so
    // the check above can only see them where they are written: in `./x`. That holds as long
    // as `./x` is a file this walk reads. A wildcard out to a package is a blind spot, and a
    // blind spot in a boundary check is worse than no check — so it is refused outright.
    for (const [file, text] of productionSources()) {
      for (const specifier of extractWildcardReExports(text)) {
        expect(
          resolveModule(file, specifier),
          `${relativeToSrc(file)} re-exports all of "${specifier}", which this walk cannot read`,
        ).not.toBeNull();
      }
    }
  });

  it("names no os.* method", () => {
    // `invoke("os.foo")`, a method constant, or anything else that would put the name on
    // the wire. Comments are stripped first: they discuss `os.*` legitimately and often.
    for (const [file, text] of productionSources()) {
      expect(withoutComments(text), `${relativeToSrc(file)} names an os.* method`).not.toMatch(
        /["'`]os\.[a-z_]+/i,
      );
    }
  });
});
