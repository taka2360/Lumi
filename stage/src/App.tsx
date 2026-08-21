/**
 * Stage — **expression only**. Holds no business logic.
 *
 * The criterion: every value readable from the store is something Core broadcast
 * via `stage.*`. If the Stage is computing its own state, logic has leaked in.
 * → docs/architecture/ui.md §2
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { CharacterCanvas, type CharacterStatus } from "./character/CharacterCanvas";
import { useStageStore } from "./core/store";
import { useCoreConnection } from "./core/useCoreConnection";
import { Inspector } from "./inspector/Inspector";
import type { CssRect } from "./platform/geometry";
import { useHitRegionReporter, useHoverState, useWindowGestures } from "./platform/useStageShell";
import { Settings } from "./settings/Settings";
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

  const [status, setStatus] = useState<CharacterStatus>({ kind: null, fallbackReason: null });
  const onStatus = useCallback((next: CharacterStatus) => setStatus(next), []);

  const [characterRect, setCharacterRect] = useState<CssRect | null>(null);
  const onBounds = useCallback((rect: CssRect | null) => setCharacterRect(rect), []);

  // Click-through must be disabled over the panel.
  // **The union of the character and the panel** is passed as the hit region (docs/architecture/ui.md).
  const [panel, setPanel] = useState<HTMLDivElement | null>(null);
  const panelRect = useElementRect(panel);

  // The Inspector is a real control. **Without its rect in the hit region the toggle
  // cannot be clicked at all** — Shell makes everything outside the region click-through.
  // When hidden, its rect is excluded to prevent blocking click-through over empty space.
  const [inspector, setInspector] = useState<HTMLDivElement | null>(null);
  const inspectorRect = useElementRect(inspector);

  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [anchorHovered, setAnchorHovered] = useState(false);

  const isActivelyHovered = hover === "inside" || anchorHovered || inspectorOpen || settingsOpen;
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

  const lastReported = useRef<string>("");
  useEffect(() => {
    const activeInspectorRect = inspectVisible ? inspectorRect : null;
    const rects = [characterRect, panelRect, activeInspectorRect].filter(
      (rect): rect is CssRect => rect !== null,
    );
    const signature = JSON.stringify(rects);
    if (signature === lastReported.current) {
      return;
    }
    lastReported.current = signature;
    reportHitRegion(rects);
  }, [characterRect, panelRect, inspectorRect, inspectVisible, reportHitRegion]);

  return (
    <div className={hover === "inside" ? "stage stage--hover" : "stage"}>
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
      <div
        className="overlay"
        ref={setPanel}
        onPointerDown={gestures.onPointerDown}
        onWheel={gestures.onWheel}
      >
        {/* While preparing, shows what's happening instead of the character.
            **Always shows exactly one thing** (docs/architecture/ui.md "Boot phases").
            Showing loading and the panel side by side would describe the same situation twice. */}
        {showCharacter || prompt || blocked ? (
          <SetupPanel />
        ) : (
          <BootScreen setup={setup} connected={connected} />
        )}
        {/* **Never silently degrades.** Shows that a placeholder is running instead of the production VRM. */}
        {status.fallbackReason && <p className="notice">{status.fallbackReason}</p>}
      </div>
      {/* **A development view** (docs/architecture/ui.md §5). Inside the `stage` window
          because `WsServer` keeps one connection per role — a second window would take
          the character's connection. Shown on hover or when expanded. */}
      <div
        ref={setInspector}
        className={inspectVisible ? "inspect-anchor inspect-anchor--visible" : "inspect-anchor"}
        onPointerEnter={() => setAnchorHovered(true)}
        onPointerLeave={() => setAnchorHovered(false)}
      >
        <Inspector onOpenChange={setInspectorOpen} />
        <Settings onOpenChange={setSettingsOpen} />
      </div>
    </div>
  );
}
