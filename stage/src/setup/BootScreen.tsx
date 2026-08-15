/**
 * 起動中の画面。**キャラクターが出るまでの間、何が起きているかを出す。**
 *
 * 設計 → docs/architecture/ui.md「起動フェーズ」
 *
 * 立っているのに反応しないキャラクターは壊れて見える。エンジンの取得に数分、
 * 起動に十数秒かかるので、その間は代わりにこれを出す。
 *
 * **主語は Lumi。** 見出しは常に「Lumi を起動しています」で、エンジンの取得・起動は
 * その内訳として下に添える。エンジン名だけを出すと、Lumi ではなく外部エンジンを
 * 起動しているように見える。
 *
 * **フェーズを決めるのは Core。** ここは配られたものを表示するだけで、
 * 「そろそろ出してよいだろう」という判断を持たない。
 */

import type { TtsSetupSnapshot } from "../core/store";

const TITLE = "Lumi を起動しています…";

function percent(progress: number | null): string {
  return `${Math.round((progress ?? 0) * 100)}%`;
}

/** 今どの内訳を進めているか。**見出しではなく、その下に添える1行。** */
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
        // **初回が長いことを先に言う。** 言わないと固まったように見える（実測 2 分）。
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
