/**
 * First-run setup, and the status of the three inference components.
 *
 * Design → docs/architecture/setup.md §2 / §2b, [ADR-034]
 *
 * ## This file only decides which screen is on
 *
 * **Only ever one of them**, and showing two would describe the same situation twice.
 *
 * | when | screen |
 * |---|---|
 * | a question is on screen | `screens/BulkFetchPrompt` / `ModelPrompt` / `ComponentPrompt` |
 * | `boot` is `blocked`, waiting on Ollama | `screens/OllamaBlocked` |
 * | `boot` is `blocked` | `screens/SetupBlocked` |
 * | otherwise | a compact status strip, or nothing at all |
 *
 * The compact strip is the fail-safe: once all three have to be usable for the character
 * to appear, a running Lumi has nothing to report. **It stays because a drift between
 * Core's phase and the component states should be visible**, not swallowed.
 *
 * The wording lives in `status.ts` as pure functions — **which sentence each state gets
 * is the part worth testing**, and it is not testable from in here.
 *
 * **Presents fetch / don't-fetch as equal choices** (ADR-019 principle 2) and **never
 * silently degrades** (principle 4): both "didn't fetch" and "failed" stay visible, and
 * neither of them lets the character out (ADR-034). Each screen holds its own half of
 * that; this file only picks between them.
 */

import { useStageStore } from "../core/store";
import { useLocale } from "../i18n/provider";
import { useQuit } from "../platform/useStageShell";
import { StatusText } from "./StatusText";
import { BulkFetchPrompt } from "./screens/BulkFetchPrompt";
import { ComponentPrompt } from "./screens/ComponentPrompt";
import { ModelPrompt } from "./screens/ModelPrompt";
import { OllamaBlocked } from "./screens/OllamaBlocked";
import { SetupBlocked } from "./screens/SetupBlocked";
import { statusLines } from "./status";
import { useOllamaState } from "./useOllamaState";

export function SetupPanel() {
  const locale = useLocale();
  const setup = useStageStore((state) => state.setup);
  const prompt = useStageStore((state) => state.prompt);
  const quit = useQuit();
  // **Called unconditionally**, before any of the branches below: it owns the re-check
  // timer, and a hook that only sometimes runs is a hook that changes order.
  const ollama = useOllamaState(setup, prompt !== null);

  if (prompt) {
    if (prompt.component === "all") {
      return <BulkFetchPrompt prompt={prompt} locale={locale} />;
    }
    if (prompt.component === "llm_model") {
      return <ModelPrompt prompt={prompt} locale={locale} />;
    }
    return <ComponentPrompt prompt={prompt} component={prompt.component} locale={locale} />;
  }

  const lines = statusLines(setup, locale);

  if (setup.boot === "blocked" && (ollama.waiting || ollama.checking)) {
    return <OllamaBlocked setup={setup} ollama={ollama} locale={locale} onQuit={quit} />;
  }

  if (setup.boot === "blocked") {
    return <SetupBlocked lines={lines} locale={locale} onQuit={quit} />;
  }

  if (lines.length === 0) {
    // **Nothing to say.** All three are fine, so no panel is drawn at all
    return null;
  }

  return (
    <div className="panel panel--compact">
      {lines.map((line) => (
        <StatusText key={line.text} line={line} />
      ))}
    </div>
  );
}
