/**
 * The speech bubbles. **Lumi's, and what she heard.**
 *
 * Design → docs/architecture/ui.md "The bubble also lives inside the `stage` window"
 *
 * Lumi's bubble deliberately does not accumulate the whole turn: barge-in stops playback
 * mid-utterance, and text left standing for sentences that were never spoken would **put
 * words in Lumi's mouth.**
 *
 * The user's bubble is the opposite — it stays until Lumi answers. **Mishearing and not
 * hearing look identical from the outside**, and the heard text is the only thing that
 * separates them.
 *
 * **Neither is part of the hit region.** Taking the desktop's clicks for the whole time
 * Lumi is talking is pure nuisance, so both stay click-through.
 */

import { useStageStore } from "../core/store";

export function Bubble() {
  const speech = useStageStore((state) => state.speech);
  const userSaid = useStageStore((state) => state.userSaid);

  return (
    <>
      {/* Below the character, on the user's side of the conversation. Rendered first so
          that if both are somehow up, Lumi's sits on top. */}
      {userSaid && (
        <div className="bubble bubble--user" key={userSaid.startedAtMs} aria-live="polite">
          {userSaid.text}
        </div>
      )}
      {/* `key` restarts the entry animation per sentence. Without it, React reuses the
          node and successive sentences appear with no visible change at all. */}
      {speech?.text && (
        <div className="bubble" key={speech.startedAtMs} aria-live="polite">
          {speech.text}
        </div>
      )}
    </>
  );
}
