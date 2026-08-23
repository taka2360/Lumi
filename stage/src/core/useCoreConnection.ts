/**
 * Puts the connection to Core onto React's lifecycle.
 *
 * **This is the one place that maps a method name to what it changes.** Every handler
 * does the same two things and nothing else: parse the payload (`payloads.ts`), then
 * write it to the store. No handler decides anything — that is Core's job, and a
 * handler that started branching would be logic leaking into the Stage.
 *
 * | method | kind | meaning |
 * |---|---|---|
 * | `stage.setup.state` | notify | Setup state for all three components changed |
 * | `stage.setup.prompt` | **command** | Asked whether to fetch. **The answer is returned as the result** |
 * | `stage.speech.started` | notify | Speech started. Comes with the mouth timeline |
 * | `stage.speech.ended` | notify | Speech ended |
 * | `stage.user.said` | notify | What Core heard the user say |
 * | `stage.character.model` | notify | Which model to draw (ADR-029) |
 * | `stage.character.expression` | notify | The face changed |
 * | `stage.settings.state` | notify | The effective settings (`locale` is what this window needs) |
 * | `stage.audio.mic` | notify | Whether the microphone is open, and whether it is muted |
 *
 * **The inspector stream is not here any more** (ADR-042). It goes to the inspector
 * window over the `panel` connection, so the per-turn payload no longer shares a socket
 * with speech and barge-in.
 *
 * The outbound direction is `stage.settings.update` and `stage.audio.mute` (ADR-028).
 */

import { useEffect } from "react";

import { connectToCore } from "./connection";
import {
  type CHOICE_INSTALL,
  type CHOICE_SELECT,
  type CHOICE_SKIP,
  METHOD_EXPRESSION,
  METHOD_MIC,
  METHOD_MIC_MUTE,
  METHOD_MODEL,
  METHOD_SETTINGS,
  METHOD_SETUP_PROMPT,
  METHOD_SETUP_RECHECK_OLLAMA,
  METHOD_SETUP_STATE,
  METHOD_SPEECH_ENDED,
  METHOD_SPEECH_STARTED,
  METHOD_USER_SAID,
} from "./methods";
import {
  toCharacterModel,
  toExpression,
  toMicState,
  toSettingsSnapshot,
  toSetupPrompt,
  toSetupSnapshot,
  toSpeech,
  toUserSaid,
} from "./payloads";
import { callCore, clearRequester, setRequester } from "./request";
import { useStageStore } from "./store";

type Answer = typeof CHOICE_INSTALL | typeof CHOICE_SELECT | typeof CHOICE_SKIP;
type AnswerPayload = { choice: Answer; model?: string };

/** The "function that returns an answer," populated only while being asked. Called by a UI button. */
let pendingAnswer: ((answer: AnswerPayload) => void) | null = null;

/** Re-checks the fixed local Ollama endpoint from the setup screen's timer. */
export async function recheckOllama(): Promise<void> {
  await callCore(METHOD_SETUP_RECHECK_OLLAMA);
}

/**
 * Mutes or unmutes the microphone.
 *
 * **The indicator is not changed here.** Core closes the input stream and then broadcasts
 * what it actually did (`stage.audio.mic`); a light that goes out on the press rather than
 * on the change would be the one thing in this feature nobody could trust.
 */
export async function setMicMuted(muted: boolean): Promise<void> {
  await callCore(METHOD_MIC_MUTE, { muted });
}

/** Returns the user's choice to Core. **Core waits until answered** (there is a timeout). */
export function answerSetupPrompt(answer: Answer, model?: string): void {
  const resolve = pendingAnswer;
  pendingAnswer = null;
  useStageStore.getState().setPrompt(null);
  resolve?.({ choice: answer, ...(model ? { model } : {}) });
}

export function useCoreConnection(): void {
  useEffect(() => {
    const store = useStageStore.getState();

    const connection = connectToCore({
      onConnectedChange: (connected) => store.setConnected(connected),
      notifications: {
        [METHOD_SETUP_STATE]: (payload) => store.setSetup(toSetupSnapshot(payload)),
        [METHOD_SETTINGS]: (payload) => store.setSettings(toSettingsSnapshot(payload)),
        // **What the microphone is actually doing**, as opposed to what was asked of it
        [METHOD_MIC]: (payload) => store.setMic(toMicState(payload)),
        // **Which model to draw is Core's decision** (ADR-029). Sent once at startup,
        // including when there is none — the placeholder needs a reason to show
        [METHOD_MODEL]: (payload) => store.setModel(toCharacterModel(payload)),
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
            pendingAnswer = (answer) => resolve(answer);
          });
        },
      },
    });

    setRequester(connection.request, "stage");

    return () => {
      pendingAnswer = null;
      clearRequester();
      connection.close();
    };
  }, []);
}
