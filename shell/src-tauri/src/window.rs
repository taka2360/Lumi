//! The window contract — which windows exist, and how each should look.
//!
//! The deciding is done by **pure functions** that don't depend on Tauri, so they can
//! be unit-tested without opening a window (docs/architecture/ui.md "Extract window
//! settings into pure functions"). The two `#[tauri::command]`s below are the only
//! parts that touch a real window, and they do nothing but apply what those functions
//! decided.
//!
//! Only "how it should look" lives here. **No AI judgment enters at all**
//! (`shell.*` never carries AI judgment → docs/architecture/core.md §3).
//!
//! **Where the cursor is, and what that means, is `hover.rs`.** The hit region and the
//! click-through / dwell decisions used to live here, back when this file meant
//! "anything about the window" — but their only caller is the cursor watcher, and a
//! rule read in one place belongs next to it.

/// Window list → docs/architecture/ui.md §1
///
/// Phase 0 only builds `Stage` and `Credits`. `Permission` is Phase 4a, but
/// **its label is fixed early since it's needed for the protected-target
/// check (Invariant 8).**
// Permission is generated in Phase 4a, Settings in Phase 1.
// The label and protected-target check need to be fixed early, so only the unused-code warning is suppressed.
#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WindowKind {
    /// The character itself. Transparent / always-on-top / click-through / unfocused.
    Stage,
    /// The credits display (tray → credits). A normal window.
    Credits,
    /// The permission prompt. Must be focused / protected under Invariant 8. Implemented in Phase 4a.
    Permission,
    /// Settings. A normal window. Implemented in Phase 1.
    Settings,
}

impl WindowKind {
    /// Every variant. **The mapping to labels is derived from this single
    /// place**, so adding a variant can never leave `from_label` un-updated.
    /// The authoritative order and values live in `docs/contracts/wire.json` (→ ADR-022).
    pub const ALL: [WindowKind; 4] =
        [WindowKind::Stage, WindowKind::Credits, WindowKind::Permission, WindowKind::Settings];

    pub const fn label(self) -> &'static str {
        match self {
            WindowKind::Stage => "stage",
            WindowKind::Credits => "credits",
            WindowKind::Permission => "permission",
            WindowKind::Settings => "settings",
        }
    }

    /// **A protected window** (docs/contracts/security-boundaries.md B3 / Invariant 8).
    ///
    /// A window that must never be a target of `os.input.*` / `os.capture.*`.
    /// **Can't be disabled via settings**, so this stays hardcoded.
    ///
    /// The docs list "permission prompt / main window / settings," but here
    /// **every one of Lumi's own windows** is treated as protected. Two
    /// reasons: (a) the operational lesson borrowed from AIRI, "put yourself
    /// on your own deny list," and (b) adding a new window kind is
    /// **protected by default** (fail-closed). If an unprotected window is
    /// ever wanted, write the exception explicitly at that point.
    ///
    /// The caller is the `os.input.*` / `os.capture.*` branch of
    /// `os_command::validate` (Phase 4c). Until then it's only called from
    /// tests, so only the unused-code warning is suppressed.
    /// **Attached only here, not to the whole impl** (otherwise it would hide the warning if something else dies).
    #[allow(dead_code)]
    pub const fn is_protected(self) -> bool {
        true
    }

    /// **Never hand-write the inverse of `label()`.** Two parallel `match`
    /// expressions risk updating only one when a variant is added, leaving that window invisible to `os.*`.
    pub fn from_label(label: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|kind| kind.label() == label)
    }
}

/// The Stage window configuration, supplied by Core / settings.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StageConfig {
    pub width: f64,
    pub height: f64,
    /// Position at last exit. Falls back to a default position on the Shell side if absent.
    pub position: Option<(f64, f64)>,
    /// Excludes it from screen sharing (an operational lesson borrowed from AIRI: content protection).
    pub content_protection: bool,
}

impl Default for StageConfig {
    fn default() -> Self {
        Self { width: 480.0, height: 720.0, position: None, content_protection: false }
    }
}

/// The window-creation spec. Corresponds to `PlatformShell.createWindow(spec)`
/// (docs/interfaces/shell.md). Kept interpretable by an Electron implementation as well.
#[derive(Debug, Clone, PartialEq)]
pub struct WindowSpec {
    pub label: &'static str,
    pub title: &'static str,
    pub width: f64,
    pub height: f64,
    pub position: Option<(f64, f64)>,
    pub transparent: bool,
    pub decorations: bool,
    pub always_on_top: bool,
    pub skip_taskbar: bool,
    pub resizable: bool,
    pub shadow: bool,
    /// Doesn't steal focus when shown (equivalent to `showInactive`).
    pub focused: bool,
    pub visible: bool,
    pub content_protected: bool,
    /// Whether to make it click-through immediately after creation.
    pub click_through: bool,
}

/// Decides the Stage window's spec. **A pure function.**
pub fn compute_stage_window_options(cfg: &StageConfig) -> WindowSpec {
    WindowSpec {
        label: WindowKind::Stage.label(),
        title: "Lumi",
        width: cfg.width,
        height: cfg.height,
        position: cfg.position,
        transparent: true,
        decorations: false,
        always_on_top: true,
        skip_taskbar: true,
        // Frameless, so no OS resize handle appears, but **Lumi resizes
        // itself** (mouse wheel over the character → `shell_window_scale`).
        // Left as `true` because leaving it `false` may keep `set_size` from working on some environments.
        resizable: true,
        shadow: false,
        // Doesn't steal focus when shown. Stealing it would yank input away
        // from whatever window the user is working in (the most hated
        // behavior for a desktop mascot).
        focused: false,
        visible: true,
        content_protected: cfg.content_protection,
        // Click-through by default. Released only once the cursor enters the character region.
        click_through: true,
    }
}

/// The lower and upper bounds on the Stage window's size (physical pixels).
///
/// **The Stage isn't trusted** (B1 / B2). It hands over a scale factor,
/// which is clamped here. An API that took an absolute size would let the
/// Stage request an off-screen size or a single pixel.
pub const MIN_STAGE_SIZE: f64 = 160.0;
pub const MAX_STAGE_SIZE: f64 = 4000.0;

/// Decides the size after applying the scale factor. **A pure function** (docs/architecture/ui.md).
///
/// Preserves the aspect ratio. If only one side hits its bound, the shape
/// distorts, so **the factor is clamped first, then applied.**
pub fn compute_scaled_size(width: f64, height: f64, factor: f64) -> (f64, f64) {
    if !factor.is_finite() || factor <= 0.0 || !width.is_finite() || !height.is_finite() {
        // **Leaves it unchanged** if given a broken value (fail-closed).
        return (width, height);
    }
    let longest = width.max(height);
    let shortest = width.min(height);
    if shortest <= 0.0 {
        return (width, height);
    }
    // The allowed range for the factor. The shorter side sets the lower bound, the longer side the upper bound.
    let lowest = MIN_STAGE_SIZE / shortest;
    let highest = MAX_STAGE_SIZE / longest;
    // When an extreme aspect ratio makes both bounds incompatible, **the
    // upper bound wins** (staying on-screen does less harm).
    let clamped = if lowest > highest { highest } else { factor.clamp(lowest, highest) };
    (width * clamped, height * clamped)
}

/// The area a window can be placed in (**logical pixels**; the work area excluding the taskbar).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ScreenArea {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// The ratio of the character window's height to the screen's height.
///
/// Works out to 540px at 1440p (the previous default) and 405px at 1080p.
/// A ratio chosen so **the look stays consistent across different screens.**
const STAGE_HEIGHT_RATIO: f64 = 0.4;

/// The aspect ratio (width ÷ height). Portrait, since the character stands upright.
const STAGE_ASPECT: f64 = 2.0 / 3.0;

/// The margin from the right edge. Placed slightly inward.
const STAGE_MARGIN_RIGHT: f64 = 16.0;

/// The margin from the bottom edge is **0**. **Grounds it** on the work
/// area's bottom edge = the taskbar's top edge.
///
/// Leaving a gap here makes the character look like it's floating. Something
/// that lives on the desktop looks more natural standing on the floor.
const STAGE_MARGIN_BOTTOM: f64 = 0.0;

/// Decides the default size and position. **A pure function** (docs/architecture/ui.md).
///
/// **Placed bottom-right.** The bottom-right of the desktop is where
/// resident things have traditionally lived, and the bottom-right of the
/// work area (excluding the taskbar) is never hidden by the taskbar.
pub fn compute_stage_placement(area: ScreenArea) -> StageConfig {
    let height = (area.height * STAGE_HEIGHT_RATIO).clamp(MIN_STAGE_SIZE, MAX_STAGE_SIZE);
    let width = (height * STAGE_ASPECT).clamp(MIN_STAGE_SIZE, MAX_STAGE_SIZE);
    // If it would be larger than the screen, pulls it toward the top-left
    // instead of letting it overflow (a fail-safe for small screens).
    let x = (area.x + area.width - width - STAGE_MARGIN_RIGHT).max(area.x);
    let y = (area.y + area.height - height - STAGE_MARGIN_BOTTOM).max(area.y);
    StageConfig { width, height, position: Some((x, y)), content_protection: false }
}

/// Grabs and moves the window (`shell.*`). **The OS decides the coordinates.**
///
/// The Stage is never allowed to compute coordinates. Exposing `setPosition`
/// would let the Stage push the window off-screen (docs/interfaces/shell.md).
#[tauri::command]
pub fn shell_window_drag_start(window: tauri::WebviewWindow) -> Result<(), String> {
    require_stage(&window)?;
    window.start_dragging().map_err(|error| error.to_string())
}

/// The top-left position after scaling. **Anchors the bottom-right corner.** **A pure function.**
///
/// By default the window sits bottom-right on screen (`compute_stage_placement`).
/// Anchoring the top-left instead while scaling up would push the character
/// off-screen. It's also more natural for something standing to keep its feet planted.
pub fn anchor_bottom_right(position: (f64, f64), old: (f64, f64), new: (f64, f64)) -> (f64, f64) {
    (position.0 + old.0 - new.0, position.1 + old.1 - new.1)
}

/// Resizes the window by a scale factor (`shell.*`). **Clamping happens on the Shell side.**
#[tauri::command]
pub fn shell_window_scale(window: tauri::WebviewWindow, factor: f64) -> Result<(), String> {
    require_stage(&window)?;
    let size = window.outer_size().map_err(|error| error.to_string())?;
    let old = (f64::from(size.width), f64::from(size.height));
    let new = compute_scaled_size(old.0, old.1, factor);

    let position = window.outer_position().map_err(|error| error.to_string())?;
    let moved = anchor_bottom_right((f64::from(position.x), f64::from(position.y)), old, new);

    window
        .set_size(tauri::PhysicalSize::new(new.0.round() as u32, new.1.round() as u32))
        .map_err(|error| error.to_string())?;
    window
        .set_position(tauri::PhysicalPosition::new(moved.0.round() as i32, moved.1.round() as i32))
        .map_err(|error| error.to_string())
}

/// **Never lets this take effect on windows other than the Stage.** There's
/// no reason character controls should resize credits or the permission
/// prompt (Shell holds "rejection").
fn require_stage(window: &tauri::WebviewWindow) -> Result<(), String> {
    match WindowKind::from_label(window.label()) {
        Some(WindowKind::Stage) => Ok(()),
        _ => {
            log::warn!("shell.window.refused label={}", window.label());
            Err("Cannot operate on non-stage window".into())
        }
    }
}

/// Decides the credits window's spec. **A pure function.**
///
/// Credits are a required Phase 0 item (docs/licensing.md §6). They need to
/// live somewhere "findable with a bit of effort," so it's a normal window opened from the tray menu.
pub fn credits_title(locale: crate::locale::Locale) -> &'static str {
    match locale {
        crate::locale::Locale::Ja => "Lumi — クレジットとライセンス",
        crate::locale::Locale::En => "Lumi — Credits and licenses",
    }
}

pub fn compute_credits_window_options(locale: crate::locale::Locale) -> WindowSpec {
    WindowSpec {
        label: WindowKind::Credits.label(),
        title: credits_title(locale),
        width: 720.0,
        height: 640.0,
        position: None,
        transparent: false,
        decorations: true,
        always_on_top: false,
        skip_taskbar: false,
        resizable: true,
        shadow: true,
        focused: true,
        visible: true,
        content_protected: false,
        click_through: false,
    }
}

#[cfg(test)]
mod tests {
    // Tests are allowed to panic, so unwrap is permitted here.
    #![allow(clippy::unwrap_used)]

    use super::*;

    fn full_hd() -> ScreenArea {
        // The work area at 1080p, excluding the 48px taskbar.
        ScreenArea { x: 0.0, y: 0.0, width: 1920.0, height: 1032.0 }
    }

    #[test]
    fn the_window_starts_in_the_bottom_right() {
        let placement = compute_stage_placement(full_hd());
        let (x, y) = placement.position.unwrap();
        // Inset from the right and bottom edges by the margin. **Never overflows the work area.**
        assert!((x + placement.width + STAGE_MARGIN_RIGHT - 1920.0).abs() < 1e-9);
        // **The bottom edge sits flush with the work area.** Floating would make the character look airborne.
        assert!((y + placement.height - 1032.0).abs() < 1e-9);
    }

    #[test]
    fn the_size_follows_the_screen() {
        let small = compute_stage_placement(full_hd());
        let large =
            compute_stage_placement(ScreenArea { x: 0.0, y: 0.0, width: 2560.0, height: 1392.0 });
        assert!(large.height > small.height);
        // The aspect ratio stays constant regardless of screen size.
        assert!((small.width / small.height - large.width / large.height).abs() < 1e-9);
    }

    #[test]
    fn a_second_monitor_offset_is_respected() {
        // When another monitor sits to the left, the work area's origin isn't 0.
        let placement = compute_stage_placement(ScreenArea {
            x: -1920.0,
            y: 0.0,
            width: 1920.0,
            height: 1032.0,
        });
        let (x, _) = placement.position.unwrap();
        assert!(x < 0.0);
    }

    #[test]
    fn a_tiny_screen_does_not_push_the_window_off() {
        let placement =
            compute_stage_placement(ScreenArea { x: 0.0, y: 0.0, width: 200.0, height: 200.0 });
        let (x, y) = placement.position.unwrap();
        assert!(x >= 0.0 && y >= 0.0);
    }

    #[test]
    fn scaling_keeps_the_aspect_ratio() {
        let (w, h) = compute_scaled_size(480.0, 720.0, 1.25);
        assert_eq!((w, h), (600.0, 900.0));
    }

    #[test]
    fn scaling_stops_at_the_lower_bound() {
        // **The Stage must never be able to make the window disappear.** The shorter side stops at the lower bound.
        let (w, h) = compute_scaled_size(480.0, 720.0, 0.01);
        assert_eq!(w, MIN_STAGE_SIZE);
        assert!((h - MIN_STAGE_SIZE * 1.5).abs() < 1e-9);
    }

    #[test]
    fn scaling_stops_at_the_upper_bound() {
        // **Must never be enlarged past the screen.** The longer side stops at the upper bound.
        let (w, h) = compute_scaled_size(480.0, 720.0, 100.0);
        assert_eq!(h, MAX_STAGE_SIZE);
        assert!(w < MAX_STAGE_SIZE);
    }

    #[test]
    fn growing_keeps_the_bottom_right_corner() {
        // A character sitting bottom-right shouldn't drift off-screen every time it's enlarged.
        let position = (2072.0, 672.0);
        let old = (464.0, 696.0);
        let new = (584.0, 877.0);
        let moved = anchor_bottom_right(position, old, new);
        assert!((moved.0 + new.0 - (position.0 + old.0)).abs() < 1e-9);
        assert!((moved.1 + new.1 - (position.1 + old.1)).abs() < 1e-9);
        assert!(moved.0 < position.0 && moved.1 < position.1);
    }

    #[test]
    fn a_broken_factor_changes_nothing() {
        // fail-closed. **Never lets a broken value break the window.**
        for factor in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert_eq!(compute_scaled_size(480.0, 720.0, factor), (480.0, 720.0));
        }
    }

    #[test]
    fn stage_window_is_transparent_frameless_ontop_and_unfocused() {
        let spec = compute_stage_window_options(&StageConfig::default());
        assert!(spec.transparent);
        assert!(!spec.decorations);
        assert!(spec.always_on_top);
        assert!(!spec.focused, "Must not steal focus when shown");
        assert!(spec.skip_taskbar);
        assert!(spec.click_through, "Default must be click-through");
    }

    #[test]
    fn stage_window_carries_content_protection_from_config() {
        let cfg = StageConfig { content_protection: true, ..StageConfig::default() };
        assert!(compute_stage_window_options(&cfg).content_protected);
        assert!(!compute_stage_window_options(&StageConfig::default()).content_protected);
    }

    #[test]
    fn stage_window_restores_position() {
        let cfg = StageConfig { position: Some((10.0, 20.0)), ..StageConfig::default() };
        assert_eq!(compute_stage_window_options(&cfg).position, Some((10.0, 20.0)));
    }

    #[test]
    fn credits_window_is_a_normal_focusable_window() {
        let spec = compute_credits_window_options(crate::locale::Locale::Ja);
        assert!(!spec.transparent);
        assert!(spec.decorations);
        assert!(spec.focused);
        assert!(!spec.always_on_top);
    }

    #[test]
    fn every_lumi_window_is_protected() {
        for kind in WindowKind::ALL {
            assert!(kind.is_protected(), "{} is missing from protected windows", kind.label());
        }
        assert_eq!(WindowKind::from_label("permission"), Some(WindowKind::Permission));
        // A window that isn't Lumi's own is never protected (it doesn't even enter the check)
        assert_eq!(WindowKind::from_label("unknown"), None);
    }
}
