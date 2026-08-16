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

import type { TtsSetupSnapshot } from "../core/store";

const TITLE = "Lumi を起動しています…";

function percent(progress: number | null): string {
  return `${Math.round((progress ?? 0) * 100)}%`;
}

/** Which detail step is currently in progress. **Not the heading — a line attached below it.** */
function step(tts: TtsSetupSnapshot, connected: boolean): { step: string; note: string } {
  if (!connected) {
    return { step: "Lumi Core に接続しています…", note: "" };
  }
  const engine = tts.engine_name ?? "音声合成エンジン";
  switch (tts.boot) {
    case "installing":
      return {
        step: `${engine} を取得しています… ${percent(tts.progress)}`,
        note: "公式の配布元から取得しています。約 200MB あります。",
      };
    case "starting":
      return {
        step: `${engine} を起動しています…`,
        // **States up front that the first run takes a while.** Without this it looks frozen (observed at 2 minutes).
        note: "初回はエンジンが音声モデルを取得するため、数分かかることがあります。",
      };
    default:
      return { step: "準備しています…", note: "" };
  }
}

export function BootScreen({ tts, connected }: { tts: TtsSetupSnapshot; connected: boolean }) {
  const { step: current, note } = step(tts, connected);
  const progress = connected && tts.boot === "installing" ? (tts.progress ?? 0) : null;

  return (
    <div className="boot">
      <div className="boot__spinner" aria-hidden="true" />
      <p className="boot__title">{TITLE}</p>
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
