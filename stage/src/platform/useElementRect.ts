/**
 * Measuring what is on screen, for the hit region.
 *
 * These live beside `geometry.ts` rather than in `App.tsx` because they are about the
 * window's geometry rather than about the character: `CssRect` is defined here, Shell is
 * the only consumer of what they measure, and a second window needing a rectangle should
 * not have to reach into the character window's component to find one.
 */

import { useEffect, useState } from "react";

import type { CssRect } from "./geometry";

/** Tracks an element's on-screen rectangle. Updates whenever the layout changes. */
export function useElementRect(element: HTMLElement | null): CssRect | null {
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

/** The window's own client rectangle. Changes whenever the wheel resizes the window. */
export function useViewportRect(): CssRect {
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
