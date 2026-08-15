//! Stage に Core への接続先を教える（`shell.*`）。
//!
//! **Stage には Stage 用の token しか渡さない。**
//! 共有すると、乗っ取られた Stage が `role: "shell"` を名乗って `os.*` を横取りできる
//! （docs/contracts/security-boundaries.md B2 / B3）。
//!
//! ポート番号は Core が起動してから決まる（Core の stdout から届く）。
//! まだ分からないうちは `None` を返し、決まったらイベントで知らせる。

use serde::Serialize;
use tauri::{AppHandle, Emitter as _, Manager as _, State};
use tokio::sync::watch;

use crate::window::WindowKind;

/// ポートが決まった / 変わったことを Stage に伝えるイベント名。
/// （Tauri のイベント名にドットは使えないので `.` を `:` にしてある）
pub(crate) const EVENT_CORE_ENDPOINT: &str = "shell:core:endpoint";

#[derive(Clone, Serialize)]
pub struct CoreEndpoint {
    pub port: u16,
    /// **Stage 用の token。** Shell 用は絶対にここに入れない。
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

/// `shell.core.endpoint` — Stage が Core に繋ぐための情報を取りに来る。
#[tauri::command]
pub fn shell_core_endpoint(state: State<'_, CoreEndpointState>) -> Option<CoreEndpoint> {
    state.snapshot()
}

/// ポートが決まったら Stage に知らせる。**Stage を待たせない**ための通知。
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
