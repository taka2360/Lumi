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
//! (`shell.hit_region.set`); the decision itself is made by a pure function in
//! `window.rs`. **No AI judgment ever rides on this path.**

use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Emitter as _, Manager as _};

use crate::window::{
    decide_click_through, decide_hover_transition, HitRect, HitRegion, HoverState, Point,
    WindowKind,
};

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
            let region = store_of(&app).snapshot();

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

fn store_of(app: &AppHandle) -> tauri::State<'_, HitRegionStore> {
    app.state::<HitRegionStore>()
}
