//! Lumi Shell — OS privilege primitives only. **Holds no judgment** (except Invariant 8's rejection).
//!
//! Design → docs/architecture/ui.md, docs/interfaces/shell.md,
//! docs/contracts/security-boundaries.md's B3

mod core_endpoint;
mod core_process;
mod hover;
mod job_object;
mod os_command;
mod tray;
mod window;
/// Cross-checks the names and constants on the wire against
/// `docs/contracts/wire.json` (ADR-022). **Holds only tests**, so it's not
/// bundled into the distributable.
#[cfg(test)]
mod wire_contract;
mod ws_client;

use std::path::PathBuf;

use rand::RngCore as _;
use tauri::{AppHandle, Manager as _, RunEvent, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::core_endpoint::{shell_core_endpoint, spawn_endpoint_notifier, CoreEndpointState};
use crate::core_process::{find_sidecar, resolve_launch_spec, CoreSupervisor, CoreTokens};
use crate::hover::{shell_hit_region_set, spawn_cursor_watcher, HitRegionStore};
use crate::window::{
    compute_credits_window_options, compute_stage_placement, compute_stage_window_options,
    shell_window_drag_start, shell_window_scale, ScreenArea, StageConfig, WindowKind, WindowSpec,
};

/// Opens a window per the spec.
///
/// **Never write conditional branching here.** "How it should look" is
/// decided by the pure functions in `window.rs`; this function only pipes
/// that into Tauri's API. Otherwise, window behavior ends up scattered
/// across places that can't be tested.
fn create_window(
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

/// Opens credits and licenses (tray → credits).
///
/// **A static page that never connects to Core** (docs/architecture/ui.md).
/// Presenting the license documents is an obligation independent of Lumi's
/// operating state, and must be readable even if Core is down.
///
/// If already open, brings it forward instead of recreating it. **Two
/// windows for the same document would be pointless.**
fn open_credits(app: &AppHandle) {
    let label = WindowKind::Credits.label();
    if let Some(existing) = app.get_webview_window(label) {
        let _ = existing.show();
        let _ = existing.unminimize();
        let _ = existing.set_focus();
        return;
    }

    let spec = compute_credits_window_options();
    match create_window(app, &spec, WebviewUrl::App("credits.html".into())) {
        Ok(_) => log::info!("credits.opened"),
        // **Never let it silently do nothing.** Logs it if it couldn't open.
        Err(error) => log::error!("credits.open_failed {error}"),
    }
}

/// Decides the Stage window's size and position from the screen.
///
/// **How it's placed is decided by the pure functions in `window.rs`.** All
/// this does is take the work area from Tauri and **convert it to logical
/// pixels.** Falls back to a default if the monitor can't be obtained
/// (logged so it **never falls back silently**).
fn stage_placement(app: &AppHandle) -> StageConfig {
    let monitor = match app.primary_monitor() {
        Ok(Some(monitor)) => monitor,
        _ => {
            log::warn!("stage.monitor_unavailable 既定の大きさで開く");
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

/// Generates the WS token. **Shell creates it and passes it to Core via an
/// environment variable** (docs/interfaces/shell.md). No other path out of the process is created.
fn generate_token() -> String {
    let mut bytes = [0u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// The project directory used to launch Core during development.
///
/// `None` in release builds, since the bundled sidecar is used instead.
/// Returned only in debug, **so the repository path is never baked into the distributable.**
fn dev_core_project_dir() -> Option<PathBuf> {
    if !cfg!(debug_assertions) {
        return None;
    }
    // **Walked up rather than joined with `../..`.** The Content Pack directory is derived
    // from this (ADR-029) and ends up in the asset protocol's scope, where leftover parent
    // segments make the allowed path unreadable in logs and needlessly fragile to match.
    // `<repo>/shell/src-tauri` → `<repo>` → `<repo>/core`
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(std::path::Path::parent)
        .map(|repo| repo.join("core"))
}

/// Lets the WebView read the Content Pack **of the Core that is actually running**, and
/// nothing else (ADR-029).
///
/// Core decides which model to draw and sends its path; the bytes are served by Shell,
/// because reading a file is an OS privilege and **Core holds authority, not capabilities**.
/// Anything outside this directory is refused by Tauri before it is opened — the Stage is
/// never trusted with a path (docs/contracts/security-boundaries.md B2).
///
/// **The directory comes from the launch decision**, not from a search of its own. Searching
/// separately pointed the WebView at a stale build's Content Pack while Core read the
/// repository's, and the character simply never appeared (2026-08-19).
///
/// **Failure is not fatal.** Lumi runs with the placeholder, and the Stage says why.
fn allow_content_pack(app: &AppHandle, dir: &std::path::Path) {
    if !dir.is_dir() {
        log::warn!("Content Pack が無い ({}): プレースホルダで起動する", dir.display());
        return;
    }

    match app.asset_protocol_scope().allow_directory(dir, true) {
        Ok(()) => log::info!("Content Pack を許可した: {}", dir.display()),
        // **Never silently degrade.** Without this the character simply never appears, and
        // the reason would exist nowhere
        Err(error) => log::error!("Content Pack を許可できない ({}): {error}", dir.display()),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // **Creates a separate token per role.** The Stage is only ever given the Stage one
    // (docs/contracts/security-boundaries.md B2 / B3).
    let shell_token = generate_token();
    let stage_token = generate_token();
    let (supervisor, port_rx) = CoreSupervisor::new();

    let setup_supervisor = supervisor.clone();
    let setup_shell_token = shell_token.clone();

    let app = tauri::Builder::default()
        .manage(HitRegionStore::default())
        .manage(CoreEndpointState::new(port_rx.clone(), stage_token.clone()))
        // `shell.*`'s allowlist (B1). Anything not listed here can't be called from the Stage.
        // **Never add a command here that carries AI judgment.**
        .invoke_handler(tauri::generate_handler![
            shell_hit_region_set,
            shell_core_endpoint,
            shell_window_drag_start,
            shell_window_scale
        ])
        .setup(move |app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default().level(log::LevelFilter::Info).build(),
                )?;
            }

            // Persisting window position belongs to Core (settings storage is
            // Core's job → docs/architecture/ui.md §2). Phase 0 doesn't persist
            // it, so it's **always calculated from the screen and placed bottom-right**.
            let spec = compute_stage_window_options(&stage_placement(app.handle()));
            create_window(app.handle(), &spec, WebviewUrl::App("index.html".into()))?;

            // Without the tray, Lumi can't be quit (`stage` is frameless and hidden from the taskbar).
            tray::init(app.handle())?;

            spawn_cursor_watcher(app.handle().clone());

            let sidecar = std::env::current_exe()
                .ok()
                .and_then(|exe| exe.parent().map(std::path::Path::to_path_buf))
                .and_then(|dir| find_sidecar(&dir));

            spawn_endpoint_notifier(app.handle().clone(), port_rx.clone());

            match resolve_launch_spec(sidecar.as_deref(), dev_core_project_dir().as_deref()) {
                Some(launch) => {
                    // **Whichever Core is about to run, that is whose Content Pack the
                    // WebView may read.** One decision, one source
                    allow_content_pack(app.handle(), &launch.content_dir);
                    setup_supervisor.start(
                        launch,
                        CoreTokens { shell: setup_shell_token.clone(), stage: stage_token.clone() },
                    );
                    ws_client::start(
                        app.handle().clone(),
                        setup_shell_token.clone(),
                        port_rx.clone(),
                    );
                }
                // **Never silently degrade.** If Core isn't found, say so.
                None => log::error!("core.not_found 起動できる Core が見つからない"),
            }

            log::info!("shell.started");
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Tauri アプリの初期化に失敗した");

    app.run(move |_app, event| {
        if let RunEvent::Exit = event {
            // **Never leave a zombie behind.** Reliably brings Core down on exit.
            tauri::async_runtime::block_on(supervisor.shutdown());
        }
    });
}
