//! Opening Lumi's own windows, and bringing one forward when it is already open.
//!
//! **Only the applying lives here.** "How it should look" is decided by the pure
//! functions in `window.rs`; this module pipes those decisions into Tauri's API and
//! owns the two `shell.*` commands that ask for a window to be opened.
//!
//! The commands sit beside the functions they call. They used to live in `tray.rs` and
//! reach back into the crate root for the implementation, which made the root a shared
//! implementation module and put the deadlock note (below) two files away from the code
//! it explains.

use tauri::{AppHandle, Manager as _, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::locale::system_locale;
use crate::window::{
    compute_credits_window_options, compute_help_window_options, compute_panel_window_options,
    compute_stage_placement, PanelKind, ScreenArea, StageConfig, WindowSpec,
};

/// Opens a window per the spec.
///
/// **Never write conditional branching here.** "How it should look" is
/// decided by the pure functions in `window.rs`; this function only pipes
/// that into Tauri's API. Otherwise, window behavior ends up scattered
/// across places that can't be tested.
pub(crate) fn create_window(
    app: &AppHandle,
    spec: &WindowSpec,
    url: WebviewUrl,
) -> tauri::Result<WebviewWindow> {
    let mut builder = WebviewWindowBuilder::new(app, spec.label, url)
        .title(spec.title)
        .inner_size(spec.width, spec.height)
        .transparent(spec.transparent)
        .decorations(spec.decorations)
        .always_on_top(spec.always_on_top)
        .skip_taskbar(spec.skip_taskbar)
        .resizable(spec.resizable)
        .shadow(spec.shadow)
        .focused(spec.focused)
        .visible(spec.visible);

    if let Some((x, y)) = spec.position {
        builder = builder.position(x, y);
    }

    let win = builder.build()?;

    // Settings not available on the builder are applied after creation.
    win.set_content_protected(spec.content_protected)?;
    win.set_ignore_cursor_events(spec.click_through)?;

    // **The builder's `position` alone doesn't take effect** (observed a few
    // dozen px of drift on Windows, 2026-08-15). Repositioned once more
    // after creation. Since it was also passed to the builder, this move isn't visible.
    if let Some((x, y)) = spec.position {
        win.set_position(tauri::LogicalPosition::new(x, y))?;
    }

    Ok(win)
}

/// Brings the window forward if it is already open, and creates it otherwise.
///
/// `Ok(None)` means one was already open. **Two windows for the same document would be
/// pointless**, and two settings windows disagreeing about the current value is not a
/// state anyone should have to reason about.
///
/// **The creation error is handed back rather than swallowed**, so each caller still
/// names the window the user actually clicked on.
fn open_or_focus(
    app: &AppHandle,
    topic: &str,
    spec: &WindowSpec,
    url: WebviewUrl,
) -> tauri::Result<Option<WebviewWindow>> {
    let Some(existing) = app.get_webview_window(spec.label) else {
        return create_window(app, spec, url).map(Some);
    };

    // **Logged rather than discarded.** "Nothing happened when I clicked it" with a
    // silent `Err` behind it is the hardest kind of report to act on, and bringing a
    // window forward is exactly the operation a window manager can refuse.
    for (what, result) in [
        ("show", existing.show()),
        ("unminimize", existing.unminimize()),
        ("focus", existing.set_focus()),
    ] {
        if let Err(error) = result {
            log::warn!("{topic}.reuse_failed label={} op={what} {error}", spec.label);
        }
    }
    Ok(None)
}

/// Opens credits and licenses (tray or Stage action menu → credits).
///
/// **A static page that never connects to Core** (docs/architecture/ui.md).
/// Presenting the license documents is an obligation independent of Lumi's
/// operating state, and must be readable even if Core is down.
pub(crate) fn open_credits(app: &AppHandle) {
    let spec = compute_credits_window_options(system_locale());
    match open_or_focus(app, "credits", &spec, WebviewUrl::App("/credits.html".into())) {
        Ok(Some(_)) => log::info!("credits.opened"),
        Ok(None) => {}
        // **Never let it silently do nothing.** Logs it if it couldn't open.
        Err(error) => log::error!("credits.open_failed {error}"),
    }
}

/// Opens the operating guide (tray or Stage action palette → help).
///
/// **A static page that never connects to Core**, for the same reason as credits and one
/// of its own: the gestures it explains are how someone reaches the setup screen's
/// controls, and that screen is shown precisely when Core has not come up.
pub(crate) fn open_help(app: &AppHandle) -> tauri::Result<()> {
    let spec = compute_help_window_options(system_locale());
    if open_or_focus(app, "help", &spec, WebviewUrl::App("/help.html".into()))?.is_some() {
        log::info!("help.opened");
    }
    Ok(())
}

/// Opens one of the auxiliary windows (ADR-042).
///
/// **The same shape as `open_credits`**, including bringing an existing one forward
/// rather than making a second.
pub(crate) fn open_panel(app: &AppHandle, kind: PanelKind) {
    let spec = compute_panel_window_options(kind, system_locale());
    let label = spec.label;
    match open_or_focus(app, "panel", &spec, WebviewUrl::App(kind.page().into())) {
        Ok(Some(_)) => log::info!("panel.opened label={label}"),
        Ok(None) => {}
        // **Never let it silently do nothing.** Logs it if it couldn't open.
        Err(error) => log::error!("panel.open_failed label={label} {error}"),
    }
}

/// Decides the Stage window's size and position from the screen.
///
/// **How it's placed is decided by the pure functions in `window.rs`.** All
/// this does is take the work area from Tauri and **convert it to logical
/// pixels.** Falls back to a default if the monitor can't be obtained
/// (logged so it **never falls back silently**).
pub(crate) fn stage_placement(app: &AppHandle) -> StageConfig {
    let monitor = match app.primary_monitor() {
        Ok(Some(monitor)) => monitor,
        _ => {
            log::warn!("stage.monitor_unavailable opening with default size");
            return StageConfig::default();
        }
    };
    // Work area = the region excluding the taskbar etc. Staying within it keeps the bottom edge from being hidden.
    let area = monitor.work_area();
    let scale = monitor.scale_factor();
    let placement = compute_stage_placement(ScreenArea {
        x: f64::from(area.position.x) / scale,
        y: f64::from(area.position.y) / scale,
        width: f64::from(area.size.width) / scale,
        height: f64::from(area.size.height) / scale,
    });
    // **Logs where it was placed.** So that if it's off, it can later be
    // narrowed down to whether the screen values or the calculation is at fault.
    log::info!(
        "stage.placement work_area={}x{}+{}+{} scale={scale} size={}x{} position={:?}",
        area.size.width,
        area.size.height,
        area.position.x,
        area.position.y,
        placement.width,
        placement.height,
        placement.position,
    );
    placement
}

/// Opens the static credits and licenses window from the Stage action menu.
///
/// **No URL or window label comes from Stage.** The only possible result is the same
/// bundled document opened by the tray, so exposing this does not grant arbitrary
/// navigation or window creation.
// A synchronous IPC command runs inline on the event-loop thread. Window creation then
// dispatches back to that same loop and waits, deadlocking the app on Windows. `async`
// runs this synchronous function on Tauri's thread pool, leaving the loop free to create
// and paint the WebView (observed as a blank credits window plus frozen quit actions).
#[tauri::command(async)]
pub fn shell_credits_open(app: AppHandle) {
    log::info!("shell.credits_open requested by stage");
    open_credits(&app);
}

/// Opens the operating guide from the Stage's action palette.
///
/// **The same shape as `shell_credits_open`**: no URL and no label come from the Stage,
/// and the only possible result is the same bundled page the tray opens.
// `async` for the same reason as `shell_credits_open`: creating a window from a
// synchronous IPC handler deadlocks the Windows event loop.
#[tauri::command(async)]
pub fn shell_help_open(app: AppHandle) -> Result<(), String> {
    log::info!("shell.help_open requested by stage");
    open_help(&app).map_err(|error| {
        log::error!("help.open_failed {error}");
        error.to_string()
    })
}

/// Opens one of Lumi's own auxiliary windows from the Stage's action row (ADR-042).
///
/// **The argument is one of three names, not a label or a URL.** An unrecognised value
/// opens nothing and is logged — a compromised Stage gets "open Lumi's settings window
/// repeatedly", which is where this ends (docs/interfaces/shell.md).
// `async` for the same reason as `shell_credits_open`: creating a window from a
// synchronous IPC handler deadlocks the Windows event loop.
#[tauri::command(async)]
pub fn shell_panel_open(app: AppHandle, kind: String) {
    match PanelKind::from_request(&kind) {
        Some(panel) => {
            log::info!("shell.panel_open requested by stage kind={kind}");
            open_panel(&app, panel);
        }
        // **fail-closed, and loudly.** Silence here would look like a window that opens
        // sometimes, which is far harder to diagnose than one that never does.
        None => log::warn!("shell.panel_open.unknown kind={kind}"),
    }
}

#[cfg(test)]
mod tests {
    /// Regression: a synchronous command deadlocks Windows while window creation
    /// dispatches back to the event loop, leaving a blank window and freezing quit.
    /// **Both window-opening commands are exposed to the Stage**, so both need it.
    #[test]
    fn window_opening_commands_never_run_inline_in_the_ipc_handler() {
        let source = include_str!("window_open.rs").replace("\r\n", "\n");
        for command in ["shell_credits_open", "shell_help_open", "shell_panel_open"] {
            assert!(
                source.contains(&format!("#[tauri::command(async)]\npub fn {command}")),
                "{command} must be async to avoid the Windows event-loop deadlock"
            );
        }
    }

    /// The Help action already has an on-screen error path in Stage. The command must
    /// reject when creation fails so that path is reachable rather than silently succeeding.
    #[test]
    fn help_open_command_returns_failures_to_stage() {
        let source = include_str!("window_open.rs");
        assert!(source.contains("pub fn shell_help_open(app: AppHandle) -> Result<(), String>"));
    }
}
