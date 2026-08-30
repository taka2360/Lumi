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

/// Performs the request.
///
/// **`window_not_open` is a stable code; the two Tauri failures are not** — they cross the
/// wire as whatever message Tauri produced. Nothing shows them to anyone today: the only
/// caller is Core's `dev_probe.py`, which logs them. So ADR-036 (reasons travel as codes,
/// the text lives in Stage) does not reach this path yet.
///
/// **If an `os.*` failure ever has to be shown to a user, give these codes first.** Do not
/// assume they already are ones.
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
