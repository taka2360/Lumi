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
  extractStaticSpecifiers,
  productionSources,
  relativeToSrc,
  resolveModule,
} from "../test/imports";

describe("platform does not depend on Core", () => {
  it("imports no core module", () => {
    const offenders: string[] = [];
    for (const [file, text] of productionSources()) {
      const where = relativeToSrc(file);
      if (!where.startsWith("/platform/")) {
        continue;
      }
      for (const specifier of extractStaticSpecifiers(text)) {
        const target = resolveModule(file, specifier);
        if (target !== null && relativeToSrc(target).startsWith("/core/")) {
          offenders.push(`${where} -> ${relativeToSrc(target)}`);
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
      const importsTauri = extractStaticSpecifiers(text).some((specifier) =>
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
        extractStaticSpecifiers(text).some((specifier) => specifier.startsWith("@tauri-apps")),
      )
      .map(([file]) => relativeToSrc(file));
    expect(tauriImporters).toContain("/platform/tauri.ts");
  });
});

/**
 * **`os.*` is Core's channel to Shell, and the Stage is not on it** (Invariant 1).
 *
 * Checked two ways on purpose. The rule as written in `authority-matrix.md` is about the
 * *types*, but those live in Core and Rust, so an import check alone would pass today no
 * matter what the Stage did — green while guarding nothing. What a leak would actually
 * look like here is a method name on the wire, so the names are checked too.
 */
describe("the Stage cannot reach os.*", () => {
  it("imports no os.* type", () => {
    for (const [file, text] of productionSources()) {
      for (const specifier of extractStaticSpecifiers(text)) {
        expect(specifier, `${relativeToSrc(file)} imports an os.* module`).not.toMatch(
          /(^|\/)os\./,
        );
      }
    }
  });

  it("names no os.* method", () => {
    // `invoke("os.foo")`, a method constant, or anything else that would put the name on
    // the wire. Comments are stripped first: they discuss `os.*` legitimately and often.
    for (const [file, text] of productionSources()) {
      const code = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
      expect(code, `${relativeToSrc(file)} names an os.* method`).not.toMatch(/["'`]os\.[a-z_]+/i);
    }
  });
});
