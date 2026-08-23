/**
 * Whether the microphone is open, and the way to close it (docs/architecture/ui.md §5b).
 *
 * **On the character window, always visible, always in the hit region.** "Is it listening?"
 * must not live inside a window that can be closed, and it must not live behind a hover:
 * the state it would spend its life in is "not on screen", which is the answer nobody can
 * check. A microphone indicator that is sometimes there is worse than none, because it
 * teaches people that its absence means silence.
 *
 * **Nothing here is optimistic.** Pressing it sends a request; the light changes when
 * Core says the stream actually closed.
 */

import { useCallback, useState } from "react";
import { useStageStore } from "../core/store";
import { setMicMuted } from "../core/useCoreConnection";
import { translate } from "../i18n";
import { useLocale } from "../i18n/provider";

export function MicIndicator() {
  const locale = useLocale();
  const mic = useStageStore((state) => state.mic);
  const [failed, setFailed] = useState(false);

  const toggle = useCallback(() => {
    if (!mic) {
      return;
    }
    setFailed(false);
    void setMicMuted(!mic.muted).catch(() => setFailed(true));
  }, [mic]);

  // **`null` is not "closed".** Core has not said yet — during boot, or while the
  // connection is down — and drawing a confident "not listening" would be a claim
  // nobody made.
  if (!mic) {
    return null;
  }

  // ★ **Three states, not two.** `{open: false, muted: false}` is a real answer from
  // Core: no capture device, or listening has not started. Folding it into "open" —
  // which is what `muted ? … : open` does — puts a live microphone icon on screen for a
  // microphone that is not there, and this control exists to be believed.
  const unavailable = !mic.open && !mic.muted;
  const state = unavailable ? "mic.unavailable" : mic.muted ? "mic.muted" : "mic.open";
  const action = mic.muted ? "mic.unmute" : "mic.mute";
  const className = unavailable ? "mic mic--off" : mic.muted ? "mic mic--muted" : "mic mic--open";

  return (
    <button
      type="button"
      className={className}
      onClick={toggle}
      // **Not pressable when there is nothing to mute.** Core refuses it anyway
      // (`no_microphone`), and a button that only ever fails is worse than a disabled one.
      disabled={unavailable}
      title={
        unavailable
          ? translate(locale, state)
          : `${translate(locale, state)} — ${translate(locale, action)}`
      }
      aria-label={translate(locale, unavailable ? state : action)}
      aria-pressed={unavailable ? undefined : mic.muted}
    >
      <span aria-hidden="true">{mic.open ? "🎙️" : "🔇"}</span>
      {failed && <span className="mic__error">{translate(locale, "mic.failed")}</span>}
    </button>
  );
}
