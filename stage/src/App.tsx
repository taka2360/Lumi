/**
 * Stage — **expression only**. Holds no business logic.
 *
 * The criterion: every value readable from the store is something Core broadcast
 * via `stage.*`. If the Stage is computing its own state, logic has leaked in.
 * → docs/architecture/ui.md §2
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ActionPalette, type PalettePoint } from "./actions/ActionPalette";
import { AppActions } from "./actions/AppActions";
import { MicIndicator } from "./audio/MicIndicator";
import { CharacterCanvas, type CharacterStatus } from "./character/CharacterCanvas";
import { useStageStore } from "./core/store";
import { useCoreConnection } from "./core/useCoreConnection";
import type { CssRect } from "./platform/geometry";
import { useHitRegionReporter, useHoverState, useWindowGestures } from "./platform/useStageShell";
import { BootScreen } from "./setup/BootScreen";
import { SetupPanel } from "./setup/SetupPanel";
import { Bubble } from "./speech/Bubble";

/** Tracks an element's on-screen rectangle. Updates whenever the layout changes. */
function useElementRect(element: HTMLElement | null): CssRect | null {
  const [rect, setRect] = useState<CssRect | null>(null);

  useEffect(() => {
    if (!element) {
      setRect(null);
      return;
    }
    const measure = () => {
      const box = element.getBoundingClientRect();
      setRect({ x: box.x, y: box.y, width: box.width, height: box.height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [element]);

  return rect;
}

/** The Stage window's own client rectangle. Changes whenever the wheel resizes the window. */
function useViewportRect(): CssRect {
  const [rect, setRect] = useState<CssRect>(() => ({
    x: 0,
    y: 0,
    width: window.innerWidth,
    height: window.innerHeight,
  }));

  useEffect(() => {
    const measure = () =>
      setRect({ x: 0, y: 0, width: window.innerWidth, height: window.innerHeight });
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  return rect;
}

export function App() {
  useCoreConnection();

  const hover = useHoverState();
  const reportHitRegion = useHitRegionReporter();
  // **The character reserves a plain left press** for touching it later (ADR-047); the
  // boot and setup surface has no character to reserve it for and keeps left-drag.
  const characterGestures = useWindowGestures("character");
  const panelGestures = useWindowGestures("panel");

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
  // Setup commands belong above any actionable setup card, including the blocked
  // screen. Keeping this condition separate from `prompt` avoids hiding the
  // settings/diagnostics entry points when setup is waiting for user action.
  const controlsAbovePanel = prompt !== null || blocked;
  // Loading and actionable setup states share one layout host. Keeping the action row
  // inside the same overlay gives it the same horizontal reference as the card;
  // a sibling flex item would move independently when the Stage window is resized.
  const controlsInOverlay = showBootScreen || controlsAbovePanel;

  const [status, setStatus] = useState<CharacterStatus>({ kind: null, fallbackReason: null });
  const onStatus = useCallback((next: CharacterStatus) => setStatus(next), []);

  const [characterRect, setCharacterRect] = useState<CssRect | null>(null);
  const onBounds = useCallback((rect: CssRect | null) => setCharacterRect(rect), []);

  // Click-through must be disabled over the panel.
  // **The union of the character and the panel** is passed as the hit region (docs/architecture/ui.md).
  const [panel, setPanel] = useState<HTMLDivElement | null>(null);
  const panelRect = useElementRect(panel);

  // The action row is a real control. **Without its rect in the hit region the buttons
  // cannot be clicked at all** — Shell makes everything outside the region click-through.
  // When hidden, its rect is excluded to prevent blocking click-through over empty space.
  const [inspector, setInspector] = useState<HTMLDivElement | null>(null);
  const inspectorRect = useElementRect(inspector);

  const [anchorHovered, setAnchorHovered] = useState(false);

  // Boot/setup and normal character mode render the controls in different hosts. Their
  // child components are remounted when that host changes, so clear parent-owned flags that
  // otherwise keep the hidden anchor visible.
  useEffect(() => {
    if (controlsInOverlay) {
      return;
    }
    setAnchorHovered(false);
  }, [controlsInOverlay]);

  // **Only the boot and setup surface reveals the actions on hover** (ADR-047). Once the
  // character is out, hovering it does nothing: a mascot that grows a settings button when
  // the cursor passes over it reads as a widget, not as something living on the desktop.
  const isActivelyHovered = hover === "inside" || anchorHovered;
  const [inspectVisible, setInspectVisible] = useState(false);

  useEffect(() => {
    if (isActivelyHovered) {
      setInspectVisible(true);
      return;
    }
    const timer = setTimeout(() => {
      setInspectVisible(false);
    }, 400);
    return () => clearTimeout(timer);
  }, [isActivelyHovered]);

  // **The microphone control is always in the hit region.** Everything else on this
  // window appears on hover; a mute button that has to be found by hovering is one the
  // user cannot reach in the moment they most want it (ui.md §5b).
  const [mic, setMic] = useState<HTMLDivElement | null>(null);
  const micRect = useElementRect(mic);

  // Where the palette is anchored, or null when it is closed. **This is the whole of its
  // state** (ADR-047) — keeping it to a point leaves room for a first-run hint later
  // without anything else on this window having to know the palette exists.
  const [paletteOrigin, setPaletteOrigin] = useState<PalettePoint | null>(null);
  const viewportRect = useViewportRect();
  const dismissPalette = useCallback(() => setPaletteOrigin(null), []);

  // Nothing on screen would explain a palette floating over a loading card, and a setup
  // question that arrives while it is open must not leave it waiting to reappear.
  useEffect(() => {
    if (!showCharacter || controlsInOverlay) {
      setPaletteOrigin(null);
    }
  }, [showCharacter, controlsInOverlay]);

  // **One listener owns the right button on this window.** The WebView's own menu has
  // nothing to act on here and would cover the character, so it is suppressed everywhere;
  // a press that landed on the character also opens the palette. Once the palette is open
  // its own layer sits in front and re-anchors instead, so this cannot reopen it.
  const [grab, setGrab] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    const onContextMenu = (event: MouseEvent) => {
      event.preventDefault();
      if (grab && event.target instanceof Node && grab.contains(event.target)) {
        setPaletteOrigin({ x: event.clientX, y: event.clientY });
      }
    };
    document.addEventListener("contextmenu", onContextMenu);
    return () => document.removeEventListener("contextmenu", onContextMenu);
  }, [grab]);

  const lastReported = useRef<string>("");
  useEffect(() => {
    const activeInspectorRect = inspectVisible ? inspectorRect : null;
    // **While the palette is open, the whole window is in the hit region.** Outside it
    // Shell clicks through, so without this a click beside the palette would land in
    // whatever is behind Lumi and the palette would never close.
    const paletteRect = paletteOrigin === null ? null : viewportRect;
    const rects = [characterRect, panelRect, activeInspectorRect, micRect, paletteRect].filter(
      (rect): rect is CssRect => rect !== null,
    );
    const signature = JSON.stringify(rects);
    if (signature === lastReported.current) {
      return;
    }
    lastReported.current = signature;
    reportHitRegion(rects);
  }, [
    characterRect,
    panelRect,
    inspectorRect,
    micRect,
    inspectVisible,
    paletteOrigin,
    viewportRect,
    reportHitRegion,
  ]);

  const inspectorControls = <AppActions />;

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
          ref={setGrab}
          className="stage__grab"
          onPointerDown={characterGestures.onPointerDown}
          onWheel={characterGestures.onWheel}
        >
          <CharacterCanvas source={model} onStatus={onStatus} onBounds={onBounds} />
        </div>
      )}
      {/* Only while the character is out. A bubble floating over a loading screen would
          be speech with nobody visibly saying it. */}
      {showCharacter && <Bubble />}
      {/* **Not conditional on hover.** Whether the microphone is open is the one thing
          on this window that has to be visible without looking for it. */}
      {showCharacter && (
        <div ref={setMic} className="mic-anchor">
          <MicIndicator />
        </div>
      )}
      <div
        className={showBootScreen ? "overlay overlay--boot" : "overlay"}
        ref={setPanel}
        onPointerDown={panelGestures.onPointerDown}
        onWheel={panelGestures.onWheel}
      >
        {controlsInOverlay && (
          <div
            ref={setInspector}
            className={
              showBootScreen
                ? `inspect-anchor inspect-anchor--boot${
                    inspectVisible ? " inspect-anchor--visible" : ""
                  }`
                : "inspect-anchor inspect-anchor--setup"
            }
            onPointerEnter={() => setAnchorHovered(true)}
            onPointerLeave={() => setAnchorHovered(false)}
          >
            {inspectorControls}
          </div>
        )}
        {/* While preparing, shows what's happening instead of the character.
            **Always shows exactly one thing** (docs/architecture/ui.md "Boot phases").
            Showing loading and the panel side by side would describe the same situation twice. */}
        {showBootScreen ? <BootScreen setup={setup} connected={connected} /> : <SetupPanel />}
        {/* **Never silently degrades.** Shows that a placeholder is running instead of the production VRM. */}
        {status.fallbackReason && <p className="notice">{status.fallbackReason}</p>}
      </div>
      {/* The same action row (docs/architecture/ui.md §1), now reached by right-clicking
          the character instead of hovering it. It stays inside the `stage` window because
          `WsServer` keeps one connection per role — a second window would take the
          character's connection. */}
      {!controlsInOverlay && paletteOrigin !== null && (
        <ActionPalette
          origin={paletteOrigin}
          onMove={setPaletteOrigin}
          onDismiss={dismissPalette}
        />
      )}
    </div>
  );
}
