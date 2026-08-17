/**
 * First-run setup, and the status of the three inference components.
 *
 * Design → docs/architecture/setup.md §2 / §2b
 *
 * **Presents fetch / don't-fetch as equal choices** (ADR-019 principle 2).
 * Both buttons are laid out the same size and weight — neither is grayed out.
 * **The default is "don't fetch"** (there's no way to close without pressing
 * either, but "don't" is placed first in order).
 *
 * **Never silently degrades** (principle 4). Both "didn't fetch" and "failed" stay visible.
 *
 * The wording lives in `status.ts` as pure functions — **which sentence each state gets
 * is the part worth testing**, and it is not testable from in here.
 */

import type { ReactNode } from "react";

import { type SetupComponent, useStageStore } from "../core/store";
import { answerSetupPrompt } from "../core/useCoreConnection";
import { failureText, statusLines } from "./status";

/** What is being asked about. **The subject of consent is never left implicit.** */
const PROMPTS: Record<SetupComponent, { title: string; body: ReactNode; note: ReactNode }> = {
  tts: {
    title: "音声合成エンジンを取得しますか？",
    body: (
      <>
        Lumi が声を出すには <b>AivisSpeech Engine</b>（LGPL-3.0）が必要です。 Lumi
        には同梱していないため、<b>公式の配布元から取得します</b>。 取得しない場合も Lumi
        は起動します（喋らないだけです）。
      </>
    ),
    note: (
      <>
        これは Lumi が外部へ通信する最初のタイミングです。取得しなければ通信は発生しません。
        エンジン本体は約 200MB です。
        <b>また、エンジンは初回起動時に、エンジン自身が音声モデルを AivisHub から取得します</b>
        （この取得は Lumi の検証の対象外です）。
      </>
    ),
  },
  stt: {
    title: "音声認識モデルを取得しますか？",
    body: (
      <>
        Lumi が声を聞き取るには <b>faster-whisper</b> のモデル（MIT）が必要です。 Lumi
        には同梱していないため、<b>公式の配布元から取得します</b>。 取得しない場合も Lumi
        は起動します（聞き取れないだけです）。
      </>
    ),
    note: (
      <>
        取得しなければ通信は発生しません。モデルは約 480MB です。
        取得したファイルは、あらかじめ決めてある大きさと内容（SHA-256）に一致するかを
        確認してから使います。
      </>
    ),
  },
};

export function SetupPanel() {
  const setup = useStageStore((state) => state.setup);
  const prompt = useStageStore((state) => state.prompt);

  if (prompt) {
    const { title, body, note } = PROMPTS[prompt.component];
    return (
      <div className="panel">
        <h1 className="panel__title">{title}</h1>
        {prompt.retry && (
          <p className="panel__status panel__status--bad">{failureText(prompt.reason)}</p>
        )}
        <p className="panel__body">{body}</p>
        <p className="panel__note">{note}</p>
        <div className="panel__actions">
          <button type="button" className="panel__button" onClick={() => answerSetupPrompt("skip")}>
            取得しない
          </button>
          <button
            type="button"
            className="panel__button"
            onClick={() => answerSetupPrompt("install")}
          >
            取得する
          </button>
        </div>
      </div>
    );
  }

  const lines = statusLines(setup);
  if (lines.length === 0) {
    // **Nothing to say.** All three are fine, so no panel is drawn at all
    return null;
  }

  return (
    <div className="panel panel--compact">
      {lines.map((line) => (
        <p
          key={line.text}
          className={line.tone === "bad" ? "panel__status panel__status--bad" : "panel__status"}
        >
          {line.text}
          {line.hint && (
            <>
              <br />
              <code className="panel__hint">{line.hint}</code>
            </>
          )}
        </p>
      ))}
    </div>
  );
}
