/**
 * The action palette shown when the character is right-clicked (ADR-047).
 *
 * ## Why this is a Stage element and not an OS menu
 *
 * Windows' own context menu is **bigger than the character**. On a 348×522 window it
 * covers the one thing the window exists to show. The action row already is a small
 * representation, so the palette only has to float it. Keeping it in the Stage also keeps
 * every label inside `stage/src/i18n`; a native menu would move those strings into Shell
 * and duplicate the translation table.
 *
 * ## Why there is a full-window layer behind it
 *
 * Everything outside the hit region is click-through, so a click "outside the palette"
 * never reaches the Stage at all. `App` widens the hit region to the whole window while
 * this is open, and this layer is what catches that click. Both revert on close.
 */

import { useEffect, useLayoutEffect, useState } from "react";

import { AppActions } from "./AppActions";

/** A point in the Stage window's client coordinates (CSS pixels). */
export interface PalettePoint {
  x: number;
  y: number;
}

export interface PaletteBox {
  width: number;
  height: number;
}

/** Kept between the palette and the window edge so it never looks flush against nothing. */
export const PALETTE_MARGIN = 4;

/**
 * Where the palette actually goes, given where the press landed. **A pure function.**
 *
 * The palette lives inside the Stage window, so clamping to the window is also what keeps
 * it on screen near a display edge — there is no second, monitor-level clamp to do.
 * When the palette is wider than the window it is pinned to the near edge rather than
 * centred, because the buttons on the left are the ones people reach for first.
 */
export function clampPalettePosition(
  point: PalettePoint,
  box: PaletteBox,
  viewport: PaletteBox,
): PalettePoint {
  const maxX = Math.max(PALETTE_MARGIN, viewport.width - box.width - PALETTE_MARGIN);
  const maxY = Math.max(PALETTE_MARGIN, viewport.height - box.height - PALETTE_MARGIN);
  return {
    x: Math.min(Math.max(point.x, PALETTE_MARGIN), maxX),
    y: Math.min(Math.max(point.y, PALETTE_MARGIN), maxY),
  };
}

export function ActionPalette({
  origin,
  onMove,
  onDismiss,
}: {
  /** Where the press that opened (or last moved) the palette landed. */
  origin: PalettePoint;
  /** A right press while open re-anchors the palette instead of closing it. */
  onMove: (point: PalettePoint) => void;
  onDismiss: () => void;
}) {
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const [position, setPosition] = useState<PalettePoint | null>(null);

  // Laid out before paint, so the palette is never seen at the unclamped point.
  useLayoutEffect(() => {
    if (!node) {
      return;
    }
    const place = () => {
      const box = node.getBoundingClientRect();
      setPosition(
        clampPalettePosition(
          origin,
          { width: box.width, height: box.height },
          { width: window.innerWidth, height: window.innerHeight },
        ),
      );
    };
    place();
    // The window is resized by the wheel, and the palette wraps at narrow widths.
    const observer = new ResizeObserver(place);
    observer.observe(node);
    window.addEventListener("resize", place);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", place);
    };
  }, [node, origin]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onDismiss();
      }
    };
    // **Also on blur.** Every action here opens a window of its own, which takes the
    // focus; without this the palette would still be sitting on the character when the
    // user comes back. A failed action opens nothing, keeps the focus, and so keeps its
    // message on screen — which is the one case where staying open is the point.
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("blur", onDismiss);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("blur", onDismiss);
    };
  }, [onDismiss]);

  // Gives the palette the keyboard as well as the pointer. Without this the first Tab
  // would walk the document from the top, which on this window is the character.
  useEffect(() => {
    node?.querySelector("button")?.focus();
  }, [node]);

  // **Listeners, not JSX handlers.** The layer is a dismiss surface rather than a
  // control: it has no role to claim, and the palette it guards brings its own labelled
  // buttons. Attaching them here keeps that out of the element's props.
  const [layer, setLayer] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!layer) {
      return;
    }
    const insidePalette = (target: EventTarget | null) =>
      target instanceof Node && node !== null && node.contains(target);

    const onPointerDown = (event: PointerEvent) => {
      // **The right button is left to `contextmenu`.** Closing on the press would let the
      // `contextmenu` that follows it reopen the palette — one gesture, two events.
      if (event.button !== 2 && !insidePalette(event.target)) {
        onDismiss();
      }
    };
    const onContextMenu = (event: MouseEvent) => {
      event.preventDefault();
      if (!insidePalette(event.target)) {
        onMove({ x: event.clientX, y: event.clientY });
      }
    };

    layer.addEventListener("pointerdown", onPointerDown);
    layer.addEventListener("contextmenu", onContextMenu);
    return () => {
      layer.removeEventListener("pointerdown", onPointerDown);
      layer.removeEventListener("contextmenu", onContextMenu);
    };
  }, [layer, node, onDismiss, onMove]);

  const placed = position ?? origin;

  return (
    <div className="palette-layer" ref={setLayer}>
      <div
        ref={setNode}
        className="palette"
        style={{ left: `${placed.x}px`, top: `${placed.y}px` }}
      >
        <AppActions />
      </div>
    </div>
  );
}
