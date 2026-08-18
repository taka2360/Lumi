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
 * | `stage.user.said` | notify | What Core heard the user say |
 */

import { useEffect } from "react";

import { connectToCore } from "./connection";
import {
  toExpression,
  toInspectorSnapshot,
  toSettingsSnapshot,
  toSetupPrompt,
  toSetupSnapshot,
  toSpeech,
  toUserSaid,
  useStageStore,
} from "./store";

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
export const METHOD_USER_SAID = "stage.user.said";
export const METHOD_EXPRESSION = "stage.character.expression";
export const METHOD_INSPECTOR = "stage.inspector.state";
export const METHOD_SETTINGS = "stage.settings.state";

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
        [METHOD_SETUP_STATE]: (payload) => store.setSetup(toSetupSnapshot(payload)),
        [METHOD_INSPECTOR]: (payload) => store.setInspector(toInspectorSnapshot(payload)),
        [METHOD_SETTINGS]: (payload) => store.setSettings(toSettingsSnapshot(payload)),
        // **Time advances on the Stage's own clock** (docs/interfaces/renderer.md).
        // An unreadable timeline leaves the mouth still; **the text is shown regardless**
        [METHOD_SPEECH_STARTED]: (payload) => store.setSpeech(toSpeech(payload, performance.now())),
        [METHOD_SPEECH_ENDED]: () => store.setSpeech(null),
        // **Not cleared on a timer.** `setSpeech` drops it when Lumi answers; until then
        // what was heard stays readable, which is the whole point when it was misheard
        [METHOD_USER_SAID]: (payload) => store.setUserSaid(toUserSaid(payload, performance.now())),
        // **An unreadable payload leaves the face as it is.** Resetting to neutral on
        // drift would look like a working expression and hide the drift entirely
        [METHOD_EXPRESSION]: (payload) => {
          const expression = toExpression(payload, performance.now());
          if (expression) {
            store.setExpression(expression);
          }
        },
      },
      commands: {
        [METHOD_SETUP_PROMPT]: (payload) => {
          store.setPrompt(toSetupPrompt(payload));
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
