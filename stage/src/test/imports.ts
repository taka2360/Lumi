/**
 * The Stage's module graph, read out of the TypeScript program. **For the boundary tests.**
 *
 * Several rules in `docs/` are about what a file may *reach* rather than what it does:
 * `credits` and `help` must not reach Core, only `platform/tauri.ts` may reach Tauri,
 * `platform/` must not reach `core/`, and nothing in the Stage may name `os.*`. All of
 * them are decided by reading the sources, so the reading lives here in one place.
 *
 * **The reading is TypeScript's, not ours.** An earlier version matched the source text
 * with regular expressions and grew one special case per review round — dynamic
 * `import()`, `export * from`, `import("./x").OsCommand`, directory `index.tsx`, and a
 * comment stripper that cut `const help = ["https://…"]` in half and hid the rest of the
 * line. Each of those is a question a parser has already answered, so the parser answers
 * them here: the project is loaded through the TypeScript API and every fact below is
 * read off the syntax tree.
 *
 * Two consequences are worth naming, because they are the whole reason for the change:
 *
 * - `SourceFile.imports` is TypeScript's own list of module specifiers. Static imports,
 *   side-effect imports, `export … from`, `export type * from`, `import = require`,
 *   literal `import()`, and the `import("…")` inside an inline import type are all in it.
 *   A spelling we have not thought of arrives with the compiler, not with a regex.
 * - Identifiers and string literals are nodes. A comment can never be mistaken for
 *   either, and a `//` inside a string can never swallow the rest of a line.
 *
 * ## What this guarantees, and what it does not
 *
 * The graph is the **import** graph, which is what `docs/architecture/ui.md` states the
 * boundaries over. It is not a model of the bundler and does not try to be one.
 *
 * Rather than leave the difference as a silent blind spot, the ways a module edge could
 * exist without being followable are refused instead of modelled — see
 * {@link StageModule.unfollowable} and {@link StageModule.unresolved}. So the graph is
 * either complete or the boundary test fails. It never quietly inspects less than it claims.
 */

import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import {
  isCallExpression,
  isIdentifier,
  isNoSubstitutionTemplateLiteral,
  isStringLiteral,
  isTemplateHead,
  isTemplateMiddleOrTail,
  type Node,
  type SourceFile,
  SyntaxKind,
} from "typescript/unstable/ast";
import { API } from "typescript/unstable/sync";

/** Absolute paths, always with forward slashes: the TypeScript API speaks that way. */
function toPosix(path: string): string {
  return path.replaceAll("\\", "/");
}

/** The `stage` package root, wherever this test happens to run from. */
export const STAGE_ROOT = toPosix(resolve(__dirname, "../.."));

/** The `src` directory of that root. */
export const SRC = `${STAGE_ROOT}/src`;

/** One production module, and everything the boundary rules ask about it. */
export interface StageModule {
  /** Absolute path, forward slashes. */
  readonly file: string;
  /** Path below `src`, e.g. `/platform/tauri.ts`. For readable assertion messages. */
  readonly where: string;
  /** Every literal module specifier, as written. */
  readonly specifiers: readonly string[];
  /** The specifiers that resolved to another module of this project. */
  readonly dependencies: readonly string[];
  /**
   * Relative specifiers that resolved to neither a module nor a file on disk.
   *
   * **A blind spot in a boundary check is worse than no check**, so these are a failure
   * rather than a shrug: an unresolved specifier means the walk stopped early, and every
   * rule downstream of that module passed by never having looked.
   */
  readonly unresolved: readonly string[];
  /**
   * What would create a dependency this walk cannot follow, named for a message.
   *
   * A worker entry point — `new Worker(new URL("./w.ts", import.meta.url))` — and a
   * computed `import(name)` both reach a module without naming it where the import graph
   * can see it. The Stage has neither, and the boundaries are documented over the import
   * graph, so rather than grow a bundler resolver for a case that does not exist, these
   * are refused. Introducing one is then a deliberate decision that has to widen this
   * walk and the documented guarantee together, instead of silently punching a hole
   * through the boundary the walk was built to hold.
   *
   * **The worker constructors are refused by name, not by call shape.** `new Worker(…)`,
   * `new window.Worker(…)`, `new globalThis.SharedWorker(…)` and `const W = Worker` are
   * the same intent wearing different syntax, and matching shapes means adding one per
   * spelling. The name itself is what the Stage has no use for, so the name is the rule.
   */
  readonly unfollowable: readonly string[];
  /** Every identifier written in the source. Not comments, not strings. */
  readonly identifiers: readonly string[];
  /** The text of every string and template literal. Not comments. */
  readonly strings: readonly string[];
}

/** The literal text of a module specifier node, or `null` if it is computed. */
function literalText(node: Node | undefined): string | null {
  if (node === undefined) {
    return null;
  }
  return isStringLiteral(node) || isNoSubstitutionTemplateLiteral(node) ? node.text : null;
}

/**
 * Resolves a relative specifier against the modules the project actually contains.
 *
 * The candidates are the bundler's: the path itself, `.ts`/`.tsx`, and a directory index
 * in either spelling. Existence is decided by the TypeScript program rather than by
 * probing the filesystem, so this agrees with what the compiler resolved.
 */
function resolveRelative(fromFile: string, specifier: string, modules: Set<string>): string | null {
  const base = toPosix(resolve(dirname(fromFile), specifier));
  const candidates = [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`];
  return candidates.find((candidate) => modules.has(candidate)) ?? null;
}

/** Whether a relative specifier points at a non-module asset: `./x.css`, `./x.txt?raw`. */
function isAsset(fromFile: string, specifier: string): boolean {
  return existsSync(resolve(dirname(fromFile), specifier.replace(/[?#].*$/, "")));
}

/** The constructors that start a module the import graph cannot see. */
const WORKER_CONSTRUCTORS = new Set(["Worker", "SharedWorker"]);

/** Collects, in one pass, everything the rules ask about a single module's syntax. */
function readFacts(source: SourceFile): {
  identifiers: string[];
  strings: string[];
  unfollowable: string[];
} {
  const identifiers: string[] = [];
  const strings: string[] = [];
  const unfollowable: string[] = [];

  const visit = (node: Node): void => {
    if (isIdentifier(node)) {
      identifiers.push(node.text);
      if (WORKER_CONSTRUCTORS.has(node.text) && !unfollowable.includes(node.text)) {
        unfollowable.push(node.text);
      }
    } else if (
      isStringLiteral(node) ||
      isNoSubstitutionTemplateLiteral(node) ||
      isTemplateHead(node) ||
      isTemplateMiddleOrTail(node)
    ) {
      strings.push(node.text);
    } else if (
      isCallExpression(node) &&
      node.expression.kind === SyntaxKind.ImportKeyword &&
      literalText(node.arguments[0]) === null
    ) {
      unfollowable.push("import(<computed>)");
    }
    node.forEachChild(visit);
  };
  source.forEachChild(visit);

  return { identifiers, strings, unfollowable };
}

/**
 * Reads every production module of a project below `<root>/src`.
 *
 * **Tests are left out deliberately.** A boundary test naturally mentions the very
 * imports it forbids, and `src/test/` exists to be imported by tests.
 *
 * Takes a `root` so the fixtures in `imports.test.ts` can hold this reader to a tree laid
 * out on purpose, rather than only to the tree it happens to run in.
 */
export function readModules(root: string): Map<string, StageModule> {
  const projectRoot = toPosix(root);
  const src = `${projectRoot}/src`;
  const api = new API({ cwd: projectRoot });
  try {
    const snapshot = api.updateSnapshot({ openProjects: [`${projectRoot}/tsconfig.json`] });
    const project = snapshot.getProjects()[0];
    if (project === undefined) {
      throw new Error(`No TypeScript project at ${projectRoot}/tsconfig.json`);
    }

    const sources = new Map<string, SourceFile>();
    for (const name of project.program.getSourceFileNames()) {
      const file = toPosix(name);
      const isProduction =
        file.startsWith(`${src}/`) &&
        /\.tsx?$/.test(file) &&
        !file.endsWith(".d.ts") &&
        !/\.test\.tsx?$/.test(file) &&
        !file.startsWith(`${src}/test/`);
      const source = isProduction ? project.program.getSourceFile(name) : undefined;
      if (source !== undefined) {
        sources.set(file, source);
      }
    }

    const known = new Set(sources.keys());
    const modules = new Map<string, StageModule>();
    for (const [file, source] of sources) {
      const specifiers: string[] = [];
      const dependencies: string[] = [];
      const unresolved: string[] = [];
      for (const node of source.imports) {
        const specifier = literalText(node);
        if (specifier === null) {
          continue;
        }
        specifiers.push(specifier);
        if (!specifier.startsWith(".")) {
          continue;
        }
        const resolved = resolveRelative(file, specifier, known);
        if (resolved !== null) {
          dependencies.push(resolved);
        } else if (!isAsset(file, specifier)) {
          unresolved.push(specifier);
        }
      }
      modules.set(file, {
        file,
        where: file.slice(src.length),
        specifiers,
        dependencies,
        unresolved,
        ...readFacts(source),
      });
    }
    return modules;
  } finally {
    api.close();
  }
}

let stageModules: Map<string, StageModule> | null = null;

/** The Stage's own modules. Read once per test worker — loading the project is not free. */
export function readStageModules(): Map<string, StageModule> {
  stageModules ??= readModules(STAGE_ROOT);
  return stageModules;
}

/** Every module reachable from `entry`, `entry` included, following resolved imports. */
export function reachableFrom(
  modules: ReadonlyMap<string, StageModule>,
  entry: string,
): StageModule[] {
  const reached = new Map<string, StageModule>();
  const queue = [toPosix(entry)];
  while (queue.length > 0) {
    const file = queue.pop();
    if (file === undefined || reached.has(file)) {
      continue;
    }
    const module = modules.get(file);
    if (module === undefined) {
      continue;
    }
    reached.set(file, module);
    queue.push(...module.dependencies);
  }
  return [...reached.values()];
}
