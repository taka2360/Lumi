/**
 * The speech bubble. **Shows the sentence being spoken right now.**
 *
 * Design → docs/architecture/ui.md "The bubble also lives inside the `stage` window"
 *
 * Deliberately not accumulating the whole turn: barge-in stops playback mid-utterance,
 * and text left standing for sentences that were never spoken would **put words in
 * Lumi's mouth.**
 *
 * **Not part of the hit region.** Taking the desktop's clicks for the whole time Lumi
 * is talking is pure nuisance, so this stays click-through.
 */

import { useStageStore } from "../core/store";

export function Bubble() {
  const speech = useStageStore((state) => state.speech);

  if (!speech?.text) {
    return null;
  }

  return (
    // `key` restarts the entry animation per sentence. Without it, React reuses the
    // node and successive sentences appear with no visible change at all.
    <div className="bubble" key={speech.startedAtMs} aria-live="polite">
      {speech.text}
    </div>
  );
}
