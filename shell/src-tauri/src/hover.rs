//! Hover detection and toggling click-through.
//!
//! **Why the cursor is watched by hand**
//! Unlike Electron's `setIgnoreMouseEvents(true, { forward: true })`, Tauri 2's
//! `set_ignore_cursor_events(true)` also stops mousemove from arriving. So "clicks
//! pass through, but it reacts once over the character" can't be built from the
//! WebView side alone.
//! → docs/interfaces/shell.md "`setClickThrough` and `onCursorMove` — the biggest difference"
//!
//! **The decision is made on the Shell side.** Sending the cursor position to the
//! Stage and back at 60Hz would violate `shell.*`'s rule of "things that should
//! take under 1ms." The Stage only hands over the hit region
//! (`shell.hit_region.set`); the decision itself is a pure function below.
//! **No AI judgment ever rides on this path.**
//!
//! The hit region, the dwell state and both decisions live here rather than in
//! `window.rs` because **this is their only caller**. They were split across two files
//! back when `window.rs` meant "anything about the window"; the watcher and the rule it
//! applies belong together.

use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Emitter as _, Manager as _};

use crate::window::WindowKind;

/// A point on screen (physical pixels).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}

/// The character's hit-test region. Computed by the Stage from the VRM's
/// render output and handed to Shell (docs/architecture/ui.md "Hover
/// detection implementation approach").
///
/// Coordinates are in the **Stage window's client coordinates** (top-left
/// origin, physical pixels). Combining it with the window position is the caller's job.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct HitRegion {
    pub rects: Vec<HitRect>,
}

#[derive(Debug, Clone, Copy, PartialEq, serde::Deserialize)]
pub struct HitRect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl HitRect {
    fn contains(&self, p: Point) -> bool {
        p.x >= self.x && p.x < self.x + self.width && p.y >= self.y && p.y < self.y + self.height
    }
}

impl HitRegion {
    pub fn contains(&self, p: Point) -> bool {
        self.rects.iter().any(|r| r.contains(p))
    }

    /// An empty region = either nothing has arrived from the Stage yet, or there's nothing to render.
    pub fn is_empty(&self) -> bool {
        self.rects.is_empty()
    }
}

/// Whether click-through should be released. **A pure function.**
///
/// `true` = click-through (ignores mouse events and passes them to the window behind).
///
/// **Click-through when the region is unset** (the fail-safe side, not
/// fail-open). Falling to fail-closed (grabbing clicks) here would make the
/// entire desktop unclickable the instant the Stage breaks. **The user being
/// unable to operate their PC is the more dangerous outcome.**
pub fn decide_click_through(cursor: Point, region: &HitRegion) -> bool {
    if region.is_empty() {
        return true;
    }
    !region.contains(cursor)
}

/// The cursor's dwell state. Notified to the Stage as `shell.hover.state`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum HoverState {
    Outside,
    Inside,
}

/// The hover-state transition. **A pure function.**
///
/// Returns `None` if unchanged from last time. **IPC is only emitted on a
/// change**, since polling at 60Hz would eat through `shell.*`'s 1ms budget if sent every cycle.
pub fn decide_hover_transition(
    cursor: Point,
    region: &HitRegion,
    prev: HoverState,
) -> Option<HoverState> {
    let next = if !region.is_empty() && region.contains(cursor) {
        HoverState::Inside
    } else {
        HoverState::Outside
    };
    if next == prev {
        None
    } else {
        Some(next)
    }
}

/// The polling interval. Equivalent to 60Hz.
/// Measurement results → docs/measurements/phase0.md
const POLL_INTERVAL: Duration = Duration::from_millis(16);

/// The event name for hover-state notifications.
///
/// The docs write this as `shell.hover.state`, but **Tauri event names can't
/// contain dots** (only alphanumerics and `-` `/` `:` `_`). The name with `.`
/// replaced by `:` is the wire name. The corresponding constant on the Stage
/// side is in `stage/src/platform/tauri.ts`.
pub(crate) const EVENT_HOVER_STATE: &str = "shell:hover:state";

/// The hit region handed over by the Stage. The only mutable state Shell holds.
#[derive(Default)]
pub struct HitRegionStore(Mutex<HitRegion>);

impl HitRegionStore {
    fn snapshot(&self) -> HitRegion {
        match self.0.lock() {
            Ok(guard) => guard.clone(),
            // A poisoned lock is treated as "region unknown" = click-through is
            // maintained. Erring toward "grabbable" here would make the desktop unusable.
            Err(_) => HitRegion::default(),
        }
    }

    fn set(&self, region: HitRegion) {
        if let Ok(mut guard) = self.0.lock() {
            *guard = region;
        }
    }
}

/// `shell.hit_region.set` — the Stage hands over the hit region it computed from the VRM's render output.
///
/// Coordinates are **physical pixels with their origin at the top-left of the
/// Stage window's client area.** Not CSS pixels (applying `devicePixelRatio` is the Stage's responsibility).
#[tauri::command]
pub fn shell_hit_region_set(store: tauri::State<'_, HitRegionStore>, rects: Vec<HitRect>) {
    store.set(HitRegion { rects });
}

/// Starts the cursor-watching thread.
///
/// Never placed on Tauri's async runtime (the Rust equivalent of asyncio). This
/// is a fixed 16ms-period loop, and sharing it with other tasks would introduce
/// jitter, so it gets a dedicated thread.
pub fn spawn_cursor_watcher(app: AppHandle) {
    std::thread::spawn(move || {
        let mut hover = HoverState::Outside;
        // `None` represents "never applied yet." Always applied on the first pass.
        let mut applied_click_through: Option<bool> = None;

        loop {
            std::thread::sleep(POLL_INTERVAL);

            let Some(win) = app.get_webview_window(WindowKind::Stage.label()) else {
                // The Stage doesn't exist yet / was closed. Keeps watching for it to come back.
                continue;
            };
            let (Ok(cursor), Ok(origin)) = (app.cursor_position(), win.inner_position()) else {
                continue;
            };

            let point =
                Point { x: cursor.x - f64::from(origin.x), y: cursor.y - f64::from(origin.y) };
            let region = app.state::<HitRegionStore>().snapshot();

            let click_through = decide_click_through(point, &region);
            if applied_click_through != Some(click_through) {
                if win.set_ignore_cursor_events(click_through).is_ok() {
                    applied_click_through = Some(click_through);
                } else {
                    // Retries next cycle if applying it failed (state isn't advanced).
                    continue;
                }
            }

            if let Some(next) = decide_hover_transition(point, &region, hover) {
                hover = next;
                // Only sent when it changes. Sending at 60Hz would eat through `shell.*`'s budget.
                let _ = win.emit(EVENT_HOVER_STATE, next);
                log::info!("shell.hover.state {next:?} at ({}, {})", point.x, point.y);
            }
        }
    });
}

#[cfg(test)]
mod tests {
    // Tests are allowed to panic, so unwrap is permitted here.
    #![allow(clippy::unwrap_used)]

    use super::*;

    fn region() -> HitRegion {
        HitRegion { rects: vec![HitRect { x: 100.0, y: 200.0, width: 50.0, height: 60.0 }] }
    }

    #[test]
    fn click_through_is_released_only_inside_the_region() {
        let r = region();
        assert!(!decide_click_through(Point { x: 120.0, y: 230.0 }, &r));
        assert!(decide_click_through(Point { x: 99.0, y: 230.0 }, &r));
        assert!(decide_click_through(Point { x: 120.0, y: 199.0 }, &r));
        // The right and bottom edges are exclusive (avoids double-counting with an adjacent rect)
        assert!(decide_click_through(Point { x: 150.0, y: 230.0 }, &r));
        assert!(decide_click_through(Point { x: 120.0, y: 260.0 }, &r));
    }

    #[test]
    fn click_through_stays_on_when_region_is_unknown() {
        // The desktop must remain operable even if the Stage is down
        let empty = HitRegion::default();
        assert!(decide_click_through(Point { x: 120.0, y: 230.0 }, &empty));
    }

    #[test]
    fn hover_transition_fires_only_on_change() {
        let r = region();
        let inside = Point { x: 120.0, y: 230.0 };
        let outside = Point { x: 0.0, y: 0.0 };

        assert_eq!(
            decide_hover_transition(inside, &r, HoverState::Outside),
            Some(HoverState::Inside)
        );
        assert_eq!(decide_hover_transition(inside, &r, HoverState::Inside), None);
        assert_eq!(
            decide_hover_transition(outside, &r, HoverState::Inside),
            Some(HoverState::Outside)
        );
        assert_eq!(decide_hover_transition(outside, &r, HoverState::Outside), None);
    }
}
