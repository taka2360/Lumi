/**
 * The screen shown while starting up. **Shows what's happening until the character appears.**
 *
 * Design → docs/architecture/ui.md "Boot phases"
 *
 * A character that's standing there but unresponsive looks broken. Fetching the
 * engine takes minutes and starting it takes a dozen-odd seconds, so this is shown
 * in the meantime instead.
 *
 * **The subject is always Lumi.** The heading always reads "Starting Lumi," with
 * fetching/starting the engine attached below as a detail. Showing just the engine
 * name would make it look like an external engine, not Lumi, is starting.
 *
 * **Core decides the phase.** This component only displays what it's handed —
 * it never judges "it's probably okay to show it now."
 */

import type { SetupSnapshot } from "../core/store";
import { type Locale, translate } from "../i18n";
import { useLocale } from "../i18n/provider";

interface Step {
  step: string;
  note: string;
  /** The bar's value, or `null` for no bar. */
  progress: number | null;
}

function gigabytes(bytes: number): string {
  return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
}

function modelName(model: string | null): string {
  if (model === "qwen3.5:9b") return "Qwen 3.5 9B";
  if (model === "qwen3.5:4b") return "Qwen 3.5 4B";
  return model ?? "";
}

/**
 * Which detail step is currently in progress. **Not the heading — a line attached below it.**
 *
 * `installing` covers two different fetches now, so **which one is running is read from
 * the component states**, never guessed. Saying "fetching the engine" while the speech
 * model downloads would be a plain lie about what the network is doing.
 */
function step(setup: SetupSnapshot, connected: boolean, locale: Locale): Step {
  if (!connected) {
    return { step: translate(locale, "boot.connecting"), note: "", progress: null };
  }
  const engine = setup.tts.engine_name ?? translate(locale, "setup.engine.generic");
  if (setup.boot === "installing") {
    if (setup.llm.state === "model_installing") {
      const progress = setup.llm.progress ?? 0;
      const completed = setup.llm.completed_bytes ?? 0;
      const total = setup.llm.total_bytes ?? 0;
      return {
        step: translate(locale, "boot.llmModel.fetching", {
          model: modelName(setup.llm.model),
          percent: Math.round(progress * 100),
        }),
        note:
          total > 0
            ? translate(locale, "boot.llmModel.bytes", {
                completed: gigabytes(completed),
                total: gigabytes(total),
              })
            : translate(locale, "boot.llmModel.note"),
        progress,
      };
    }
    if (setup.stt.state === "installing") {
      return {
        step: translate(locale, "boot.speechModel.fetching", {
          percent: Math.round((setup.stt.progress ?? 0) * 100),
        }),
        note: translate(locale, "boot.speechModel.note"),
        progress: setup.stt.progress ?? 0,
      };
    }
    return {
      step: translate(locale, "boot.engine.fetching", {
        engine,
        percent: Math.round((setup.tts.progress ?? 0) * 100),
      }),
      note: translate(locale, "boot.engine.note"),
      progress: setup.tts.progress ?? 0,
    };
  }
  if (setup.boot === "starting") {
    if (
      setup.stt.runtime === "starting" ||
      (setup.tts.runtime === "ready" && setup.stt.runtime === "stopped")
    ) {
      return {
        step: translate(locale, "boot.speechModel.starting"),
        note: translate(locale, "boot.speechModel.starting.note"),
        progress: null,
      };
    }
    return {
      step: translate(locale, "boot.engine.starting", { engine }),
      // **States up front that the first run takes a while.** Without this it looks frozen
      // (observed at 2 minutes).
      note: translate(locale, "boot.starting.note"),
      progress: null,
    };
  }
  return { step: translate(locale, "boot.preparing"), note: "", progress: null };
}

export function BootScreen({ setup, connected }: { setup: SetupSnapshot; connected: boolean }) {
  const locale = useLocale();
  const { step: current, note, progress } = step(setup, connected, locale);

  return (
    <div className="boot">
      <div className="boot__spinner" aria-hidden="true" />
      <p className="boot__title">{translate(locale, "boot.title")}</p>
      <p className="boot__step">{current}</p>
      {progress !== null && (
        <div className="boot__bar">
          <div className="boot__bar-fill" style={{ width: `${Math.round(progress * 100)}%` }} />
        </div>
      )}
      {note && <p className="boot__note">{note}</p>}
    </div>
  );
}
