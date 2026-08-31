/**
 * Reading the import graph out of the source tree. **For the boundary tests.**
 *
 * Several rules in `docs/` are about what a file is allowed to *reach*, not about what it
 * does — `credits` and `help` must not reach Core, and only `platform/` may reach Tauri.
 * Those are checked by walking the sources, so the walking itself lives in one place.
 *
 * **Matching specifiers, not raw text.** A substring search would trip over the string
 * `"@tauri-apps/api"` in the credits data, which is a package *name being displayed*
 * rather than an import.
 */

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";

/** The `src` directory, wherever this test happens to run from. */
export const SRC = resolve(__dirname, "..");

/**
 * Literal module specifiers from static imports/exports and dynamic `import()` calls.
 *
 * Computed dynamic imports cannot be resolved by this source-tree walk, but a string or
 * no-substitution template literal is a concrete dependency and must not bypass a boundary.
 */
export function extractModuleSpecifiers(text: string): string[] {
  const found: Array<{ index: number; specifier: string }> = [];
  const addMatches = (pattern: RegExp, specifierGroup: number): void => {
    for (const match of text.matchAll(pattern)) {
      const specifier = match[specifierGroup];
      if (specifier !== undefined) {
        found.push({ index: match.index, specifier });
      }
    }
  };

  addMatches(/(?:import|export)\s+(?:type\s+)?(?:[^"'`;]*?\s+from\s+)?["']([^"']+)["']/g, 1);
  addMatches(/\bimport\s*\(\s*(["'])([^"']+)\1\s*(?:,|\))/g, 2);

  for (const match of text.matchAll(/\bimport\s*\(\s*`([^`]*)`\s*(?:,|\))/g)) {
    const specifier = match[1];
    if (specifier !== undefined && !specifier.includes("${")) {
      found.push({ index: match.index, specifier });
    }
  }

  return found.sort((left, right) => left.index - right.index).map(({ specifier }) => specifier);
}

/** Resolves a relative specifier the way the bundler does, trying each extension. */
export function resolveModule(fromFile: string, specifier: string): string | null {
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

/** A path relative to `src`, with forward slashes, for readable assertion messages. */
export function relativeToSrc(file: string): string {
  return file.slice(SRC.length).split(sep).join("/");
}

/**
 * Every production source file under `src`.
 *
 * **Tests are excluded deliberately.** A boundary test naturally mentions the very
 * imports it forbids, and `src/test/` exists to be imported by tests.
 */
export function productionSources(): Map<string, string> {
  const found = new Map<string, string>();
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "test") {
          walk(full);
        }
      } else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
        found.set(full, readFileSync(full, "utf8"));
      }
    }
  };
  walk(SRC);
  return found;
}
