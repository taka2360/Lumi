/**
 * Reading what the character says and does — speech, what Core heard, the model, the face.
 *
 * **Time advances on the Stage's own clock** (docs/interfaces/renderer.md): these carry
 * the moment they were received, and the renderer measures from it.
 */

import type { ExpressionState } from "../../character/expression";
import { parseExpression } from "../../character/expression";
import { parseTimeline, type VisemeTimeline } from "../../character/lipsync";
import type { CharacterModel, Speech, UserSaid } from "../store";

/**
 * A timeline that moves nothing. Used when Core sends no `spans`.
 *
 * `visemeAt` returns `null` at every point, so **the mouth simply stays closed** —
 * which is what "no timeline" should mean.
 */
const NO_TIMELINE: VisemeTimeline = { spans: [], totalMs: 0 };

/**
 * Reads a `stage.speech.started` payload.
 *
 * **A missing timeline never suppresses the text.** Core omits `spans` whenever the
 * engine returns no timing (docs/interfaces/renderer.md: better a still mouth than a
 * mouth on bogus timing) — but **not moving the mouth and not saying what was said are
 * different failures.** Dropping the whole event left the bubble blank for exactly the
 * utterances that most needed reading.
 */
export function toSpeech(payload: Record<string, unknown>, startedAtMs: number): Speech {
  return {
    text: typeof payload.text === "string" ? payload.text : "",
    timeline: parseTimeline(payload) ?? NO_TIMELINE,
    startedAtMs,
  };
}

/**
 * Reads a `stage.user.said` payload.
 *
 * **Empty text is still an event.** Core only sends this once STT produced something, so
 * an empty string here means the payload drifted — and a blank bubble says that far more
 * loudly than no bubble at all.
 */
export function toUserSaid(payload: Record<string, unknown>, startedAtMs: number): UserSaid {
  return {
    text: typeof payload.text === "string" ? payload.text : "",
    startedAtMs,
  };
}

/**
 * Reads a `stage.character.model` payload (ADR-029).
 *
 * **A missing path is a state, not a parse failure.** A Content Pack without a model is a
 * legitimate pack, and saying so is what keeps the placeholder from looking like a bug.
 * A pack that declares a model it doesn't ship never reaches here — Core refuses to load it.
 *
 * The payload also carries the model's credit, and this deliberately ignores it. The
 * credits window is static and doesn't connect to Core by design (docs/architecture/ui.md
 * "Why credits is not connected to Core"), so the credit obligation is met there, not here.
 * Parsing a field nothing renders would be an abstraction waiting for a use.
 */
export function toCharacterModel(payload: Record<string, unknown>): CharacterModel {
  const path = typeof payload.path === "string" && payload.path ? payload.path : null;
  const reason = typeof payload.reason === "string" ? payload.reason : "";
  if (!path) {
    return { path: null, format: "", reason: reason || "model_not_in_pack" };
  }
  return {
    path,
    format: typeof payload.format === "string" ? payload.format : "",
    reason: "",
  };
}

/**
 * Reads a `stage.character.expression` payload. **`null` for an unknown emotion**, which
 * leaves the current face alone rather than quietly resetting it.
 */
export function toExpression(
  payload: Record<string, unknown>,
  startedAtMs: number,
): ExpressionState | null {
  const intent = parseExpression(payload);
  return intent ? { intent, startedAtMs } : null;
}
