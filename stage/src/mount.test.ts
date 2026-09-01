/**
 * **`credits` and `help` never load the code that talks to Core** (authority-matrix #20).
 *
 * They are separate Vite entry points for that reason (`vite.config.ts`), and the reason
 * is stated in `docs/architecture/ui.md`: help explains the gestures that reach the setup
 * screen, which is shown precisely when Core has *not* come up. A single import added to
 * `mount.tsx` would pull the connection code into both.
 *
 * The graph, and the limits of what it can see, are in `./test/imports`; the reader is
 * held to a fixture tree in `./test/imports.test.ts`.
 */

import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { reachableFrom, readStageModules, SRC } from "./test/imports";

const modules = readStageModules();

describe.each([
  ["credits", join(SRC, "credits", "main.tsx")],
  ["help", join(SRC, "help", "main.tsx")],
])("the %s window", (name, entry) => {
  it("reaches no module that talks to Core or the Shell", () => {
    const reachable = reachableFrom(modules, entry);
    // The walk found something, or the assertions below would pass vacuously.
    expect(reachable.length, `${name} entry resolved nothing`).toBeGreaterThan(1);

    const offenders: string[] = [];
    for (const module of reachable) {
      if (module.where.startsWith("/core/") || module.where.startsWith("/platform/")) {
        offenders.push(`${name} reaches ${module.where}`);
      }
      for (const specifier of module.specifiers) {
        if (specifier.startsWith("@tauri-apps")) {
          offenders.push(`${module.where} (reachable from ${name}) imports ${specifier}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
