//! The WS connection to Core. **The side that receives and executes Core → Shell's `os.*`** (where B3 is applied).
//!
//! Core is the hub, so Core is the one that listens; Shell connects to it
//! (docs/architecture/core.md §7 startup sequence).
//!
//! **A received request is never executed as-is.** It always passes through
//! `os_command::validate`. Rejections are logged, never swallowed
//! (roadmap Phase 0 verification step 9).

use std::time::Duration;

use futures_util::{SinkExt as _, StreamExt as _};
use serde_json::{json, Value};
use tauri::{AppHandle, Manager as _, PhysicalPosition};
use tokio::sync::watch;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;

use crate::os_command::{validate, OsCommand};

/// Uses the same value as Core (`PROTOCOL_VERSION` in core/lumi/transport/protocol.py).
pub(crate) const PROTOCOL_VERSION: u64 = 1;

/// The retry interval after a failed connection.
const RECONNECT_DELAY: Duration = Duration::from_millis(500);

/// A single message from Core. Raw, **pre-validation** form.
#[derive(Debug, Clone, PartialEq)]
pub struct IncomingCommand {
    pub id: String,
    pub method: String,
    pub payload: Value,
}

/// Parses a command envelope. **A pure function.**
///
/// In Phase 0, `command` is the only kind Shell accepts.
/// Other kinds from Core (besides `welcome`) are not accepted.
pub fn parse_command(raw: &str) -> Result<IncomingCommand, String> {
    let value: Value = serde_json::from_str(raw).map_err(|e| format!("Not valid JSON: {e}"))?;
    if value.get("v").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
        return Err("Protocol version mismatch".into());
    }
    if value.get("kind").and_then(Value::as_str) != Some("command") {
        return Err("Kind must be command".into());
    }
    let id = value.get("id").and_then(Value::as_str).ok_or("Missing id")?.to_owned();
    let method = value.get("method").and_then(Value::as_str).ok_or("Missing method")?.to_owned();
    let payload = value.get("payload").cloned().unwrap_or_else(|| json!({}));
    Ok(IncomingCommand { id, method, payload })
}

fn hello(token: &str) -> String {
    json!({"v": PROTOCOL_VERSION, "kind": "hello", "role": "shell", "token": token}).to_string()
}

fn ok_result(id: &str, payload: Value) -> String {
    json!({"v": PROTOCOL_VERSION, "kind": "result", "corr_id": id, "ok": true, "payload": payload})
        .to_string()
}

fn error_result(id: &str, reason: &str) -> String {
    json!({
        "v": PROTOCOL_VERSION,
        "kind": "result",
        "corr_id": id,
        "ok": false,
        "payload": {},
        "error": reason,
    })
    .to_string()
}

/// Starts maintaining the connection. The port arrives via Core's stdout.
pub fn start(app: AppHandle, token: String, mut port_rx: watch::Receiver<Option<u16>>) {
    tauri::async_runtime::spawn(async move {
        loop {
            let port = *port_rx.borrow_and_update();
            let Some(port) = port else {
                // Core isn't listening yet. Waits for the next change.
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
    let (mut ws, _) = connect_async(&url).await.map_err(|e| format!("Cannot connect: {e}"))?;

    ws.send(Message::Text(hello(token).into()))
        .await
        .map_err(|e| format!("Send hello failed: {e}"))?;

    match ws.next().await {
        Some(Ok(Message::Text(text))) => {
            let value: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
            if value.get("kind").and_then(Value::as_str) != Some("welcome") {
                return Err("Did not receive welcome (authentication may have failed)".into());
            }
        }
        Some(Ok(_)) => return Err("Received frame other than welcome".into()),
        Some(Err(err)) => return Err(format!("Failed to receive welcome: {err}")),
        None => return Err("Connection closed while waiting for welcome".into()),
    }
    log::info!("core.ws.connected port={port}");

    while let Some(frame) = ws.next().await {
        let message = frame.map_err(|e| format!("Failed to receive message: {e}"))?;
        let Message::Text(raw) = message else {
            continue;
        };
        let response = match parse_command(&raw) {
            Ok(command) => handle(app, &command),
            Err(reason) => {
                // No correlation ID is available, so nothing can be returned. **Always logged.**
                log::warn!("os.command.malformed reason={reason}");
                continue;
            }
        };
        ws.send(Message::Text(response.into()))
            .await
            .map_err(|e| format!("Failed to send response: {e}"))?;
    }
    Ok(())
}

/// Validate → execute. **Anything that fails validation never reaches execution.**
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
    // Tests are allowed to panic, so unwrap is permitted here.
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

    /// Core refuses a frame whose version it cannot agree on (ADR-022). `result` used to be
    /// the one frame Shell sent without a version, which only worked because Core did not look.
    #[test]
    fn every_frame_carries_the_protocol_version() {
        for text in [hello("t"), ok_result("id1", json!({})), error_result("id1", "why")] {
            let value: Value = serde_json::from_str(&text).unwrap();
            assert_eq!(value["v"], PROTOCOL_VERSION, "{text}");
        }
    }
}
