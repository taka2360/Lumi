//! Lumi Shell — OS privilege primitives only. **Holds no judgment** (except Invariant 8's rejection).
//!
//! Design → docs/architecture/ui.md, docs/interfaces/shell.md,
//! docs/contracts/security-boundaries.md's B3

mod app;
mod content_pack;
mod core_endpoint;
mod core_process;
mod hover;
mod job_object;
mod locale;
mod os_command;
mod os_exec;
mod tray;
mod window;
mod window_open;
/// Cross-checks the names and constants on the wire against
/// `docs/contracts/wire.json` (ADR-022). **Holds only tests**, so it's not
/// bundled into the distributable.
#[cfg(test)]
mod wire_contract;
mod ws_client;

use rand::RngCore as _;
use tauri::{RunEvent, WebviewUrl};

use crate::content_pack::{allow_content_pack, dev_core_project_dir};
use crate::core_endpoint::{shell_core_endpoint, spawn_endpoint_notifier, CoreEndpointState};
use crate::core_process::{find_sidecar, resolve_launch_spec, CoreSupervisor, CoreTokens};
use crate::hover::{shell_hit_region_set, spawn_cursor_watcher, HitRegionStore};
use crate::locale::system_locale;
use crate::window::{compute_stage_window_options, shell_window_drag_start, shell_window_scale};
use crate::window_open::{create_window, stage_placement};

/// Generates the WS token. **Shell creates it and passes it to Core via an
/// environment variable** (docs/interfaces/shell.md). No other path out of the process is created.
fn generate_token() -> String {
    let mut bytes = [0u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // **Creates a separate token per role.** The Stage is only ever given the Stage one
    // (docs/contracts/security-boundaries.md B2 / B3).
    let shell_token = generate_token();
    let stage_token = generate_token();
    // **A third token for the auxiliary windows** (ADR-042). Held by settings, inspector
    // and memory; never by the character window, which is what keeps `invoke`'s addressee
    // unambiguous.
    let panel_token = generate_token();
    let (supervisor, port_rx) = CoreSupervisor::new();

    let setup_supervisor = supervisor.clone();
    let setup_shell_token = shell_token.clone();

    let app = tauri::Builder::default()
        .manage(HitRegionStore::default())
        .manage(CoreEndpointState::new(port_rx.clone(), stage_token.clone(), panel_token.clone()))
        // `shell.*`'s allowlist (B1). Anything not listed here can't be called from the Stage.
        // **Never add a command here that carries AI judgment.**
        .invoke_handler(tauri::generate_handler![
            shell_hit_region_set,
            shell_core_endpoint,
            shell_window_drag_start,
            shell_window_scale,
            tray::shell_locale_set,
            window_open::shell_credits_open,
            window_open::shell_help_open,
            window_open::shell_panel_open,
            app::shell_ollama_site_open,
            // Credits and quitting carry no judgment, so `shell.*`'s rule holds.
            app::shell_app_quit
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
            tray::init(app.handle(), system_locale())?;

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
                        CoreTokens {
                            shell: setup_shell_token.clone(),
                            stage: stage_token.clone(),
                            panel: panel_token.clone(),
                        },
                    );
                    ws_client::start(
                        app.handle().clone(),
                        setup_shell_token.clone(),
                        port_rx.clone(),
                    );
                }
                // **Never silently degrade.** If Core isn't found, say so.
                None => log::error!("core.not_found No runnable Core found"),
            }

            log::info!("shell.started");
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Failed to initialize Tauri application");

    app.run(move |_app, event| {
        if let RunEvent::Exit = event {
            // **Never leave a zombie behind.** Reliably brings Core down on exit.
            tauri::async_runtime::block_on(supervisor.shutdown());
        }
    });
}
