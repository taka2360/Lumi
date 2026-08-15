/**
 * Core への接続を React のライフサイクルに載せる。
 *
 * ここで扱う `stage.*` は2つだけ（Phase 0）:
 *
 * | method | 種類 | 意味 |
 * |---|---|---|
 * | `stage.setup.state` | notify | TTS セットアップ状態が変わった |
 * | `stage.setup.prompt` | command | 取得するかを尋ねられた。**答えを result で返す** |
 * | `stage.speech.started` | notify | 発話が始まった。口のタイムラインが付いてくる |
 * | `stage.speech.ended` | notify | 発話が終わった |
 */

import { useEffect } from "react";

import { parseTimeline } from "../character/lipsync";
import { connectToCore } from "./connection";
import { type SetupPrompt, toTtsSnapshot, useStageStore } from "./store";

type Answer = "install" | "skip";

/** 尋ねられている間だけ入る「答えを返す関数」。UI のボタンがこれを呼ぶ。 */
let pendingAnswer: ((answer: Answer) => void) | null = null;

/** ユーザーの選択を Core に返す。**答えるまで Core は待っている**（timeout はある）。 */
export function answerSetupPrompt(answer: Answer): void {
  const resolve = pendingAnswer;
  pendingAnswer = null;
  useStageStore.getState().setPrompt(null);
  resolve?.(answer);
}

export function useCoreConnection(): void {
  useEffect(() => {
    const store = useStageStore.getState();

    const connection = connectToCore({
      onConnectedChange: (connected) => store.setConnected(connected),
      notifications: {
        "stage.setup.state": (payload) => store.setTts(toTtsSnapshot(payload)),
        "stage.speech.started": (payload) => {
          const timeline = parseTimeline(payload);
          if (!timeline) {
            // 読めないタイムラインで口を動かさない。**音は Core が鳴らしている。**
            return;
          }
          store.setSpeech({
            text: typeof payload.text === "string" ? payload.text : "",
            timeline,
            // **時刻は Stage の時計で進める**（docs/interfaces/renderer.md）。
            startedAtMs: performance.now(),
          });
        },
        "stage.speech.ended": () => store.setSpeech(null),
      },
      commands: {
        "stage.setup.prompt": (payload) => {
          const prompt: SetupPrompt = {
            retry: payload.retry === true,
            reason: typeof payload.reason === "string" ? payload.reason : null,
          };
          store.setPrompt(prompt);
          return new Promise((resolve) => {
            pendingAnswer = (answer) => resolve({ choice: answer });
          });
        },
      },
    });

    return () => {
      pendingAnswer = null;
      connection.close();
    };
  }, []);
}
