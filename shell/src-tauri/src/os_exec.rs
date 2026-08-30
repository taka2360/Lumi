//! Carrying out an `os.*` request that already passed verification (B3's third step).
//!
//! The three stages of B3 are three files, in this order:
//!
//! | File | Holds |
//! |---|---|
//! | `os_command.rs` | the verification. **Pure** — needs neither a socket nor a window |
//! | `os_exec.rs` | the doing, and **only for an `OsCommand`** |
//! | `ws_client.rs` | the frames: parsing what Core sent, and returning a result |
//!
//! **This module never decides whether an operation should happen.** By the time a value
//! reaches here the target is one of Lumi's own windows and the numbers are finite; what
//! is left is calling Tauri (docs/contracts/security-boundaries.md B3).

use serde_json::{json, Value};
use tauri::{AppHandle, Manager as _, PhysicalPosition};

use crate::os_command::OsCommand;

/// Performs the request. `Err` is a stable reason code, never a sentence for a person
/// (Core turns reason codes into text → ADR-036).
pub fn execute(app: &AppHandle, command: &OsCommand) -> Result<Value, String> {
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
