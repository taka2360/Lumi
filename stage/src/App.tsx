/**
 * Stage — **expression only**. Holds no business logic.
 *
 * The criterion: every value readable from the store is something Core broadcast
 * via `stage.*`. If the Stage is computing its own state, logic has leaked in.
 * → docs/architecture/ui.md §2
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ActionPalette, type PalettePoint } from "./actions/ActionPalette";
import { MicIndicator } from "./audio/MicIndicator";
import { CharacterCanvas, type CharacterStatus } from "./character/CharacterCanvas";
import { useStageStore } from "./core/store";
import { useCoreConnection } from "./core/useCoreConnection";
import { type CssRect, sameHitRegion } from "./platform/geometry";
import { useElementRect, useViewportRect } from "./platform/useElementRect";
import { useHitRegionReporter, useHoverState, useWindowGestures } from "./platform/useStageShell";
import { BootScreen } from "./setup/BootScreen";
import { SetupPanel } from "./setup/SetupPanel";
import { Bubble } from "./speech/Bubble";

export function App() {
  useCoreConnection();

  const hover = useHoverState();
  const reportHitRegion = useHitRegionReporter();
  // **The same gestures on every surface this window shows** (ADR-047): a plain left
  // press is reserved for the character, the wheel resizes, `alt` + left and the middle
  // button move. The loading and setup cards stand in for the character while it cannot
  // be shown, so they behave like it rather than like a dialog.
  const gestures = useWindowGestures();

  // **Core decides whether the character may be shown** (docs/architecture/ui.md "Boot phases").
  const setup = useStageStore((state) => state.setup);
  const connected = useStageStore((state) => state.connected);
  const prompt = useStageStore((state) => state.prompt);
  // **Which model is Core's decision** (ADR-029). `null` = not told yet, which is not the
  // same as "there is no model" — waiting avoids showing the placeholder for a moment and
  // then swapping it, which would read as a glitch rather than as a state
  const model = useStageStore((state) => state.model);
  const showCharacter = connected && setup.boot === "ready" && model !== null;
  // **Setup is unfinished, so there is no character to show** (ADR-034). Not a loading
  // state: nothing is in progress, and what happens next is up to the user. `connected`
  // is required because `blocked` can only ever come from Core.
  const blocked = connected && setup.boot === "blocked";
  const showBootScreen = !showCharacter && !prompt && !blocked;

  const [status, setStatus] = useState<CharacterStatus>({ kind: null, fallbackReason: null });
  const onStatus = useCallback((next: CharacterStatus) => setStatus(next), []);

  const [characterRect, setCharacterRect] = useState<CssRect | null>(null);
  const onBounds = useCallback((rect: CssRect | null) => setCharacterRect(rect), []);

  // Click-through must be disabled over the panel.
  // **The union of the character and the panel** is passed as the hit region (docs/architecture/ui.md).
  const [panel, setPanel] = useState<HTMLDivElement | null>(null);
  const panelRect = useElementRect(panel);

  // **The microphone control is always in the hit region.** It is the one thing on this
  // window that has to be visible without looking for it: a mute button the user has to
  // go find is one they cannot reach in the moment they want it (ui.md §5b).
  const [mic, setMic] = useState<HTMLDivElement | null>(null);
  const micRect = useElementRect(mic);

  // Where the palette is anchored, or null when it is closed. **This is the whole of its
  // state** (ADR-047) — keeping it to a point leaves room for a first-run hint later
  // without anything else on this window having to know the palette exists.
  const [paletteOrigin, setPaletteOrigin] = useState<PalettePoint | null>(null);
  const viewportRect = useViewportRect();
  const dismissPalette = useCallback(() => setPaletteOrigin(null), []);
  const paletteOpen = paletteOrigin !== null;

  // **One listener owns the right button on this window.** The WebView's own menu has
  // nothing to act on here and would cover whatever is on screen, so it is suppressed
  // everywhere; a press that arrived at all opens the palette, because Shell already
  // clicked through everything outside the hit region. While the palette is open its own
  // layer sits in front and re-anchors instead, so this must not also fire.
  useEffect(() => {
    const onContextMenu = (event: MouseEvent) => {
      event.preventDefault();
      if (!paletteOpen) {
        setPaletteOrigin({ x: event.clientX, y: event.clientY });
      }
    };
    document.addEventListener("contextmenu", onContextMenu);
    return () => document.removeEventListener("contextmenu", onContextMenu);
  }, [paletteOpen]);

  const lastReported = useRef<CssRect[]>([]);
  useEffect(() => {
    // **While the palette is open, the whole window is in the hit region.** Outside it
    // Shell clicks through, so without this a click beside the palette would land in
    // whatever is behind Lumi and the palette would never close.
    const paletteRect = paletteOrigin === null ? null : viewportRect;
    const rects = [characterRect, panelRect, micRect, paletteRect].filter(
      (rect): rect is CssRect => rect !== null,
    );
    // Most frames leave the region exactly as it was, and `shell.*` is the path with the
    // 1ms budget — so only a real change is worth an IPC call.
    if (sameHitRegion(rects, lastReported.current)) {
      return;
    }
    lastReported.current = rects;
    reportHitRegion(rects);
  }, [characterRect, panelRect, micRect, paletteOrigin, viewportRect, reportHitRegion]);

  return (
    <div
      className={`${hover === "inside" ? "stage stage--hover" : "stage"}${
        showBootScreen ? " stage--boot" : ""
      }`}
    >
      {/* The character's own surface. A plain left press is deliberately unhandled and
          reserved for touching the character (ADR-047); the wheel resizes, `alt` + left and
          the middle button move the window, and the right button opens the action palette.
          Never reachable from the keyboard (window move/resize follows OS conventions). */}
      {showCharacter && (
        <div
          className="stage__grab"
          onPointerDown={gestures.onPointerDown}
          onWheel={gestures.onWheel}
        >
          <CharacterCanvas source={model} onStatus={onStatus} onBounds={onBounds} />
        </div>
      )}
      {/* Only while the character is out. A bubble floating over a loading screen would
          be speech with nobody visibly saying it. */}
      {showCharacter && <Bubble />}
      {/* **Not conditional on anything but the character.** Whether the microphone is
          open is the one thing on this window that has to be visible without looking. */}
      {showCharacter && (
        <div ref={setMic} className="mic-anchor">
          <MicIndicator />
        </div>
      )}
      <div
        className={showBootScreen ? "overlay overlay--boot" : "overlay"}
        ref={setPanel}
        onPointerDown={gestures.onPointerDown}
        onWheel={gestures.onWheel}
      >
        {/* While preparing, shows what's happening instead of the character.
            **Always shows exactly one thing** (docs/architecture/ui.md "Boot phases").
            Showing loading and the panel side by side would describe the same situation twice. */}
        {showBootScreen ? <BootScreen setup={setup} connected={connected} /> : <SetupPanel />}
        {/* **Never silently degrades.** Shows that a placeholder is running instead of the production VRM. */}
        {status.fallbackReason && <p className="notice">{status.fallbackReason}</p>}
      </div>
      {/* The application actions (docs/architecture/ui.md §1), reached by right-clicking
          whatever this window is showing — the character, or the card standing in for it.
          They stay inside the `stage` window because `WsServer` keeps one connection per
          role, and a second window would take the character's connection. */}
      {paletteOrigin !== null && (
        <ActionPalette
          origin={paletteOrigin}
          onMove={setPaletteOrigin}
          onDismiss={dismissPalette}
        />
      )}
    </div>
  );
}
