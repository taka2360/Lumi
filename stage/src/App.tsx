/**
 * Stage — **expression only**. Holds no business logic.
 *
 * The criterion: every value readable from the store is something Core broadcast
 * via `stage.*`. If the Stage is computing its own state, logic has leaked in.
 * → docs/architecture/ui.md §2
 */

import { useCallback, useEffect, useRef, useState } from "react";

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

export function App() {
  useCoreConnection();

  const hover = useHoverState();
  const reportHitRegion = useHitRegionReporter();
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
  // Setup commands belong above any actionable setup card, including the blocked
  // screen. Keeping this condition separate from `prompt` avoids hiding the
  // settings/diagnostics entry points when setup is waiting for user action.
  const controlsAbovePanel = prompt !== null || blocked;

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

  // Prompt/setup and normal character mode render the controls in different hosts. Their
  // child components are remounted when that host changes, so clear parent-owned flags that
  // otherwise keep the hidden normal-mode anchor visible.
  useEffect(() => {
    if (controlsAbovePanel) {
      return;
    }
    setAnchorHovered(false);
  }, [controlsAbovePanel]);

  // **The panels are windows of their own now** (ADR-042), so nothing expands in place
  // any more and the row's visibility follows the cursor alone.
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

  const lastReported = useRef<string>("");
  useEffect(() => {
    const activeInspectorRect = inspectVisible ? inspectorRect : null;
    const rects = [characterRect, panelRect, activeInspectorRect, micRect].filter(
      (rect): rect is CssRect => rect !== null,
    );
    const signature = JSON.stringify(rects);
    if (signature === lastReported.current) {
      return;
    }
    lastReported.current = signature;
    reportHitRegion(rects);
  }, [characterRect, panelRect, inspectorRect, micRect, inspectVisible, reportHitRegion]);

  const inspectorControls = <AppActions />;

  return (
    <div
      className={`${hover === "inside" ? "stage stage--hover" : "stage"}${
        showBootScreen ? " stage--boot" : ""
      }`}
    >
      {/* The surface for grabbing the character to move the window. Never reachable
          from the keyboard (window move/resize follows OS conventions, not Stage UI). */}
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
        onPointerDown={gestures.onPointerDown}
        onWheel={gestures.onWheel}
      >
        {controlsAbovePanel && (
          <div
            ref={setInspector}
            className="inspect-anchor inspect-anchor--setup"
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
      {/* **A development view** (docs/architecture/ui.md §5). Inside the `stage` window
          because `WsServer` keeps one connection per role — a second window would take
          the character's connection. Shown on hover or when expanded. */}
      {!controlsAbovePanel && (
        <div
          ref={setInspector}
          className={inspectVisible ? "inspect-anchor inspect-anchor--visible" : "inspect-anchor"}
          onPointerEnter={() => setAnchorHovered(true)}
          onPointerLeave={() => setAnchorHovered(false)}
        >
          {inspectorControls}
        </div>
      )}
    </div>
  );
}
