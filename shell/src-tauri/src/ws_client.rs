//! Core への WS 接続。**Core → Shell の `os.*` を受けて実行する側**（B3 の適用点）。
//!
//! Core がハブなので、listen するのは Core。Shell は接続しにいく
//! （docs/architecture/core.md §7 起動シーケンス）。
//!
//! **受け取った要求をそのまま実行しない。** `os_command::validate` を必ず通す。
//! 拒否は握りつぶさずログに残す（roadmap Phase 0 検証手順 9）。

use std::time::Duration;

use futures_util::{SinkExt as _, StreamExt as _};
use serde_json::{json, Value};
use tauri::{AppHandle, Manager as _, PhysicalPosition};
use tokio::sync::watch;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;

use crate::os_command::{validate, OsCommand};

/// Core と同じ値を使う（core/lumi/transport/protocol.py の `PROTOCOL_VERSION`）。
const PROTOCOL_VERSION: u64 = 1;

/// 接続に失敗したときの再試行間隔。
const RECONNECT_DELAY: Duration = Duration::from_millis(500);

/// Core から届いた 1 通。**検証前**の生の形。
#[derive(Debug, Clone, PartialEq)]
pub struct IncomingCommand {
    pub id: String,
    pub method: String,
    pub payload: Value,
}

/// command の封筒を解釈する。**純粋関数**。
///
/// Phase 0 で Shell が受理するのは `command` だけ。
/// Core からの他の kind（`welcome` を除く）は受け取らない。
pub fn parse_command(raw: &str) -> Result<IncomingCommand, String> {
    let value: Value = serde_json::from_str(raw).map_err(|e| format!("JSON ではない: {e}"))?;
    if value.get("v").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
        return Err("プロトコルバージョンが違う".into());
    }
    if value.get("kind").and_then(Value::as_str) != Some("command") {
        return Err("command ではない".into());
    }
    let id = value.get("id").and_then(Value::as_str).ok_or("id が無い")?.to_owned();
    let method = value.get("method").and_then(Value::as_str).ok_or("method が無い")?.to_owned();
    let payload = value.get("payload").cloned().unwrap_or_else(|| json!({}));
    Ok(IncomingCommand { id, method, payload })
}

fn hello(token: &str) -> String {
    json!({"v": PROTOCOL_VERSION, "kind": "hello", "role": "shell", "token": token}).to_string()
}

fn ok_result(id: &str, payload: Value) -> String {
    json!({"kind": "result", "corr_id": id, "ok": true, "payload": payload}).to_string()
}

fn error_result(id: &str, reason: &str) -> String {
    json!({"kind": "result", "corr_id": id, "ok": false, "payload": {}, "error": reason})
        .to_string()
}

/// 接続の維持を開始する。ポートは Core の stdout から届く。
pub fn start(app: AppHandle, token: String, mut port_rx: watch::Receiver<Option<u16>>) {
    tauri::async_runtime::spawn(async move {
        loop {
            let port = *port_rx.borrow_and_update();
            let Some(port) = port else {
                // Core がまだ listen していない。次の変化を待つ。
                if port_rx.changed().await.is_err() {
                    return;
                }
                continue;
            };

            match run_connection(&app, &token, port).await {
                Ok(()) => log::info!("core.ws.closed port={port}"),
                Err(err) => log::warn!("core.ws.error port={port} {err}"),
            }
            tokio::time::sleep(RECONNECT_DELAY).await;
        }
    });
}

async fn run_connection(app: &AppHandle, token: &str, port: u16) -> Result<(), String> {
    let url = format!("ws://127.0.0.1:{port}");
    let (mut ws, _) = connect_async(&url).await.map_err(|e| format!("接続できない: {e}"))?;

    ws.send(Message::Text(hello(token).into())).await.map_err(|e| format!("hello 送信: {e}"))?;

    match ws.next().await {
        Some(Ok(Message::Text(text))) => {
            let value: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
            if value.get("kind").and_then(Value::as_str) != Some("welcome") {
                return Err("welcome が来ない（認証に失敗した可能性）".into());
            }
        }
        Some(Ok(_)) => return Err("welcome の代わりに別のフレームが来た".into()),
        Some(Err(err)) => return Err(format!("welcome の受信に失敗: {err}")),
        None => return Err("welcome を待っている間に閉じられた".into()),
    }
    log::info!("core.ws.connected port={port}");

    while let Some(frame) = ws.next().await {
        let message = frame.map_err(|e| format!("受信に失敗: {e}"))?;
        let Message::Text(raw) = message else {
            continue;
        };
        let response = match parse_command(&raw) {
            Ok(command) => handle(app, &command),
            Err(reason) => {
                // 相関 ID が取れないので返せない。**ログには必ず残す。**
                log::warn!("os.command.malformed reason={reason}");
                continue;
            }
        };
        ws.send(Message::Text(response.into())).await.map_err(|e| format!("送信に失敗: {e}"))?;
    }
    Ok(())
}

/// 検証 → 実行。**検証を通らなかったものは実行に到達しない。**
fn handle(app: &AppHandle, command: &IncomingCommand) -> String {
    match validate(&command.method, &command.payload) {
        Err(rejection) => {
            log::warn!(
                "os.command.rejected method={} reason={} detail={:?}",
                command.method,
                rejection.reason(),
                rejection
            );
            error_result(&command.id, rejection.reason())
        }
        Ok(validated) => match execute(app, &validated) {
            Ok(payload) => {
                log::info!("os.command.executed method={}", command.method);
                ok_result(&command.id, payload)
            }
            Err(reason) => {
                log::warn!("os.command.failed method={} reason={reason}", command.method);
                error_result(&command.id, &reason)
            }
        },
    }
}

fn execute(app: &AppHandle, command: &OsCommand) -> Result<Value, String> {
    match command {
        OsCommand::WindowGetPosition { window } => {
            let win = app.get_webview_window(window.label()).ok_or("window_not_open")?;
            let position = win.outer_position().map_err(|e| e.to_string())?;
            Ok(json!({"x": position.x, "y": position.y}))
        }
        OsCommand::WindowSetPosition { window, x, y } => {
            let win = app.get_webview_window(window.label()).ok_or("window_not_open")?;
            win.set_position(PhysicalPosition::new(*x, *y)).map_err(|e| e.to_string())?;
            Ok(json!({}))
        }
    }
}

#[cfg(test)]
mod tests {
    // テストは panic してよい場所なので unwrap を許す。
    #![allow(clippy::unwrap_used)]

    use super::*;

    #[test]
    fn parses_a_command() {
        let raw = r#"{"v":1,"kind":"command","id":"abc","method":"os.window.get_position",
                      "payload":{"window":"stage"}}"#;
        let command = parse_command(raw).unwrap();
        assert_eq!(command.id, "abc");
        assert_eq!(command.method, "os.window.get_position");
    }

    #[test]
    fn rejects_other_kinds() {
        assert!(parse_command(r#"{"v":1,"kind":"result","corr_id":"a","ok":true}"#).is_err());
        assert!(parse_command(r#"{"v":2,"kind":"command","id":"a","method":"os.x"}"#).is_err());
        assert!(parse_command("nope").is_err());
        assert!(parse_command(r#"{"v":1,"kind":"command","method":"os.x"}"#).is_err());
    }

    #[test]
    fn payload_defaults_to_empty_object() {
        let command =
            parse_command(r#"{"v":1,"kind":"command","id":"a","method":"os.x"}"#).unwrap();
        assert_eq!(command.payload, json!({}));
    }

    #[test]
    fn hello_does_not_leak_the_token_into_the_method_field() {
        let text = hello("s3cret");
        let value: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(value["role"], "shell");
        assert_eq!(value["token"], "s3cret");
    }

    #[test]
    fn rejection_is_reported_as_a_failed_result() {
        let value: Value = serde_json::from_str(&error_result("id1", "unknown_method")).unwrap();
        assert_eq!(value["ok"], false);
        assert_eq!(value["corr_id"], "id1");
        assert_eq!(value["error"], "unknown_method");
    }
}
