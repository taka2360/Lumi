//! App-level `shell.*` commands — **the ones that touch neither a window nor Core.**
//!
//! What may live here: a command with no arguments whose whole effect is on the process
//! itself or on something the OS owns. Opening one of Lumi's own windows belongs in
//! `window_open.rs`; anything tied to the tray belongs in `tray.rs`.
//!
//! **The rule matters more than the two functions below.** `tray.rs` became the place
//! where every unrelated command landed precisely because nobody had written down what
//! it was for.

use tauri::AppHandle;

/// Fixed by Shell: Stage never gets an arbitrary-URL capability.
const OLLAMA_DOWNLOAD_URL: &str = "https://ollama.com/download";

/// Quits Lumi, from the Stage.
///
/// **Exists for the setup screen's quit button** (ADR-034): someone stopped before Lumi
/// has ever started has not met the tray, which was the only way out
/// (docs/architecture/ui.md "Tray menu").
///
/// **Carries no judgment** — no arguments, nothing to decide. Goes through the same
/// `app.exit(0)` as the tray item, so Core goes down with it via `RunEvent::Exit`.
#[tauri::command]
pub fn shell_app_quit(app: AppHandle) {
    log::info!("shell.app_quit requested by stage");
    app.exit(0);
}

/// Opens Ollama's fixed official download page in the user's default browser.
///
/// The URL is not an argument, so a compromised Stage cannot redirect this command.
#[tauri::command]
pub fn shell_ollama_site_open() -> Result<(), String> {
    log::info!("shell.ollama_site_open requested by stage");
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer.exe")
            .arg(OLLAMA_DOWNLOAD_URL)
            .spawn()
            .map(|_| ())
            .map_err(|error| error.to_string())
    }
    #[cfg(not(target_os = "windows"))]
    {
        Err("unsupported_platform".to_owned())
    }
}
