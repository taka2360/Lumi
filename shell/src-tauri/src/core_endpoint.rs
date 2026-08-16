//! Tells the Stage where to connect to Core (`shell.*`).
//!
//! **Only the Stage-specific token is ever handed to the Stage.**
//! Sharing it would let a compromised Stage claim `role: "shell"` and hijack
//! `os.*` (docs/contracts/security-boundaries.md B2 / B3).
//!
//! The port number is only decided once Core has started (it arrives via Core's
//! stdout). `None` is returned while it's still unknown, and an event announces it once decided.

use serde::Serialize;
use tauri::{AppHandle, Emitter as _, Manager as _, State};
use tokio::sync::watch;

use crate::window::WindowKind;

/// The event name that tells the Stage the port was decided / changed.
/// (Tauri event names can't contain dots, so `.` is replaced with `:`)
pub(crate) const EVENT_CORE_ENDPOINT: &str = "shell:core:endpoint";

#[derive(Clone, Serialize)]
pub struct CoreEndpoint {
    pub port: u16,
    /// **The Stage-specific token.** The Shell one must never go here.
    pub token: String,
}

pub struct CoreEndpointState {
    port: watch::Receiver<Option<u16>>,
    stage_token: String,
}

impl CoreEndpointState {
    pub fn new(port: watch::Receiver<Option<u16>>, stage_token: String) -> Self {
        Self { port, stage_token }
    }

    fn snapshot(&self) -> Option<CoreEndpoint> {
        let port = (*self.port.borrow())?;
        Some(CoreEndpoint { port, token: self.stage_token.clone() })
    }
}

/// `shell.core.endpoint` — the Stage fetches what it needs to connect to Core.
#[tauri::command]
pub fn shell_core_endpoint(state: State<'_, CoreEndpointState>) -> Option<CoreEndpoint> {
    state.snapshot()
}

/// Notifies the Stage once the port is decided. A notification **so the Stage never has to wait**.
pub fn spawn_endpoint_notifier(app: AppHandle, mut port: watch::Receiver<Option<u16>>) {
    tauri::async_runtime::spawn(async move {
        while port.changed().await.is_ok() {
            let Some(window) = app.get_webview_window(WindowKind::Stage.label()) else {
                continue;
            };
            let endpoint = app.state::<CoreEndpointState>().snapshot();
            let _ = window.emit(EVENT_CORE_ENDPOINT, endpoint);
        }
    });
}
