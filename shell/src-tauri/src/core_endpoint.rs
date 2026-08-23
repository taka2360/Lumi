//! Tells a window where to connect to Core (`shell.*`).
//!
//! **The token is chosen from the window that asked, never from what it asks for.**
//! Sharing one would let a compromised Stage claim `role: "shell"` and hijack `os.*`
//! (docs/contracts/security-boundaries.md B2 / B3); letting a window name the role it
//! wants would let the memory window claim `stage` and take the character's connection
//! (ADR-042). Neither is a check somewhere downstream — the caller simply never holds
//! the other token.
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
    panel_token: String,
}

impl CoreEndpointState {
    pub fn new(
        port: watch::Receiver<Option<u16>>,
        stage_token: String,
        panel_token: String,
    ) -> Self {
        Self { port, stage_token, panel_token }
    }

    /// The endpoint for one window. **`None` for a window that is not Lumi's**, which in
    /// practice means an unknown label: a window nobody wrote down gets no token at all.
    fn snapshot(&self, kind: Option<WindowKind>) -> Option<CoreEndpoint> {
        let port = (*self.port.borrow())?;
        let token = match kind? {
            WindowKind::Stage => self.stage_token.clone(),
            kind if kind.is_panel() => self.panel_token.clone(),
            // `credits` never connects, and `permission` (Phase 4a) must not either.
            _ => return None,
        };
        Some(CoreEndpoint { port, token })
    }
}

/// `shell.core.endpoint` — a window fetches what it needs to connect to Core.
#[tauri::command]
pub fn shell_core_endpoint(
    window: tauri::Window,
    state: State<'_, CoreEndpointState>,
) -> Option<CoreEndpoint> {
    // **The label comes from Tauri, not from the caller.** A window cannot ask on
    // another's behalf, which is what makes the token choice above trustworthy.
    let kind = WindowKind::from_label(window.label());
    if kind.is_none() {
        log::warn!("shell.core_endpoint.unknown_window label={}", window.label());
    }
    state.snapshot(kind)
}

/// Notifies the Stage once the port is decided. A notification **so the Stage never has to wait**.
///
/// **Only the Stage is notified.** A panel window is opened by the user, long after the
/// port is known, and asks for the endpoint itself — a broadcast would mean deciding which
/// token to put in an event that any window can receive.
pub fn spawn_endpoint_notifier(app: AppHandle, mut port: watch::Receiver<Option<u16>>) {
    tauri::async_runtime::spawn(async move {
        while port.changed().await.is_ok() {
            let Some(window) = app.get_webview_window(WindowKind::Stage.label()) else {
                continue;
            };
            let endpoint = app.state::<CoreEndpointState>().snapshot(Some(WindowKind::Stage));
            let _ = window.emit(EVENT_CORE_ENDPOINT, endpoint);
        }
    });
}
