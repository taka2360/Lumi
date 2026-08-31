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

/** Source with comments stripped, so a rule *discussing* a construct is not read as one. */
export function withoutComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
}

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

/**
 * Names introduced or forwarded by static imports and exports.
 *
 * Both sides of an alias are returned. A boundary must still see `OsCommand` in
 * `import type { OsCommand as Command } from "./protocol"`, even though the local name
 * is harmless-looking. This intentionally parses only the binding clause; module paths
 * remain the responsibility of `extractModuleSpecifiers` above.
 *
 * **A bare `export * from` names nothing, so nothing is returned for it.** The forwarded
 * names live in the other module, and are found there instead — by this function if that
 * module re-imports them, by `extractExportedDeclarations` if it declares them. That only
 * holds while the wildcard resolves inside the tree, which is what `extractWildcardReExports`
 * exists to let a caller insist on.
 */
export function extractModuleBindings(text: string): string[] {
  const found: string[] = [];
  const addBinding = (binding: string): void => {
    const withoutType = binding.trim().replace(/^type\s+/, "");
    const names = withoutType.split(/\s+as\s+/);
    for (const name of names) {
      if (/^[A-Za-z_$][\w$]*$/.test(name)) {
        found.push(name);
      }
    }
  };

  const declarations = withoutComments(text).matchAll(
    /\b(?:import|export)\s+(?:type\s+)?([^"'`;]*?)\s+from\s+["'][^"']+["']/g,
  );
  for (const declaration of declarations) {
    const clause = declaration[1];
    if (clause === undefined) {
      continue;
    }

    const named = clause.match(/\{([\s\S]*?)\}/);
    if (named?.[1] !== undefined) {
      for (const binding of named[1].split(",")) {
        addBinding(binding);
      }
    }

    const outsideNamed = clause.replace(/\{[\s\S]*?\}/, "");
    for (const binding of outsideNamed.split(",")) {
      const namespace = binding.trim().match(/^\*\s+as\s+([A-Za-z_$][\w$]*)$/);
      if (namespace?.[1] !== undefined) {
        found.push(namespace[1]);
      } else {
        addBinding(binding);
      }
    }
  }

  return found;
}

/**
 * Names a module declares and exports itself, as opposed to forwarding from elsewhere.
 *
 * Needed because a re-export can hide a name: `export * from "./protocol"` puts every one
 * of that module's exports onto this module's surface without writing any of them down.
 * The name is written down where it is *declared*, so that is where a boundary sees it.
 */
export function extractExportedDeclarations(text: string): string[] {
  const found: string[] = [];
  const declarations = withoutComments(text).matchAll(
    /\bexport\s+(?:declare\s+)?(?:default\s+)?(?:abstract\s+)?(?:async\s+)?(?:const\s+enum|class|function\s*\*?|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)/g,
  );
  for (const declaration of declarations) {
    const name = declaration[1];
    if (name !== undefined) {
      found.push(name);
    }
  }
  return found;
}

/**
 * Specifiers of the re-exports that name nothing: `export * from "./x"`, `export type * from "./x"`.
 *
 * `export * as Name from "./x"` is not one of them — it does name what it introduces, and
 * `extractModuleBindings` returns that name.
 */
export function extractWildcardReExports(text: string): string[] {
  const found: string[] = [];
  const reExports = withoutComments(text).matchAll(
    /\bexport\s+(?:type\s+)?\*\s+from\s+["']([^"']+)["']/g,
  );
  for (const reExport of reExports) {
    const specifier = reExport[1];
    if (specifier !== undefined) {
      found.push(specifier);
    }
  }
  return found;
}

/**
 * Resolves a relative specifier the way the bundler does, trying each extension.
 *
 * **Directory indexes are included, `.tsx` as well as `.ts`.** A specifier that resolves
 * to nothing is treated as a package and the walk stops there, so a candidate this misses
 * is not an error — it is a boundary check that quietly inspects less than it claims.
 */
export function resolveModule(fromFile: string, specifier: string): string | null {
  const base = resolve(dirname(fromFile), specifier);
  for (const candidate of [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    join(base, "index.ts"),
    join(base, "index.tsx"),
  ]) {
    try {
      readFileSync(candidate, "utf8");
      return candidate;
    } catch {
      // Not this one. A specifier that resolves to nothing is a package, not our source.
    }
  }
  return null;
}

/** Every source module reachable from `entry`, following resolved relative imports. */
export function reachableFrom(entry: string): Map<string, string[]> {
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
    const specifiers = extractModuleSpecifiers(text);
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
