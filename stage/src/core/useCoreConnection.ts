/**
 * Puts the connection to Core onto React's lifecycle.
 *
 * Only two `stage.*` methods are handled here (Phase 0):
 *
 * | method | kind | meaning |
 * |---|---|---|
 * | `stage.setup.state` | notify | TTS setup state changed |
 * | `stage.setup.prompt` | command | Asked whether to fetch. **The answer is returned as the result** |
 * | `stage.speech.started` | notify | Speech started. Comes with the mouth timeline |
 * | `stage.speech.ended` | notify | Speech ended |
 */

import { useEffect } from "react";

import { parseTimeline } from "../character/lipsync";
import { connectToCore } from "./connection";
import { type SetupPrompt, toTtsSnapshot, useStageStore } from "./store";

/**
 * The `stage.*` method names Core sends. **`docs/contracts/wire.json` is authoritative** (→ ADR-022).
 *
 * The corresponding constants on the Core side are `METHOD_STATE` / `METHOD_PROMPT`
 * in `setup/coordinator.py` and `METHOD_SPEECH_*` in `greeting.py`. Writing these
 * directly as handler keys means **a typo gets silently dropped as an "unknown
 * method"** (`unhandled_method`).
 */
export const METHOD_SETUP_STATE = "stage.setup.state";
export const METHOD_SETUP_PROMPT = "stage.setup.prompt";
export const METHOD_SPEECH_STARTED = "stage.speech.started";
export const METHOD_SPEECH_ENDED = "stage.speech.ended";

/** The answer to whether to fetch. Core only compares against `CHOICE_INSTALL` (anything else means "don't"). */
export const CHOICE_INSTALL = "install";
export const CHOICE_SKIP = "skip";

type Answer = typeof CHOICE_INSTALL | typeof CHOICE_SKIP;

/** The "function that returns an answer," populated only while being asked. Called by a UI button. */
let pendingAnswer: ((answer: Answer) => void) | null = null;

/** Returns the user's choice to Core. **Core waits until answered** (there is a timeout). */
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
        [METHOD_SETUP_STATE]: (payload) => store.setTts(toTtsSnapshot(payload)),
        [METHOD_SPEECH_STARTED]: (payload) => {
          const timeline = parseTimeline(payload);
          if (!timeline) {
            // Never moves the mouth for an unreadable timeline. **The sound itself is played by Core.**
            return;
          }
          store.setSpeech({
            text: typeof payload.text === "string" ? payload.text : "",
            timeline,
            // **Time advances on the Stage's own clock** (docs/interfaces/renderer.md).
            startedAtMs: performance.now(),
          });
        },
        [METHOD_SPEECH_ENDED]: () => store.setSpeech(null),
      },
      commands: {
        [METHOD_SETUP_PROMPT]: (payload) => {
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
