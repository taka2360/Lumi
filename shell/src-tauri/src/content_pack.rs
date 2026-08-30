//! Which Content Pack the WebView may read, and where the Core that owns it lives.
//!
//! Core decides which model to draw and sends its path; the bytes are served by Shell,
//! because reading a file is an OS privilege and **Core holds authority, not capabilities**
//! (ADR-029).

use std::path::{Path, PathBuf};

use tauri::{AppHandle, Manager as _};

/// The project directory used to launch Core during development.
///
/// `None` in release builds, since the bundled sidecar is used instead.
/// Returned only in debug, **so the repository path is never baked into the distributable.**
pub(crate) fn dev_core_project_dir() -> Option<PathBuf> {
    if !cfg!(debug_assertions) {
        return None;
    }
    // **Walked up rather than joined with `../..`.** The Content Pack directory is derived
    // from this (ADR-029) and ends up in the asset protocol's scope, where leftover parent
    // segments make the allowed path unreadable in logs and needlessly fragile to match.
    // `<repo>/shell/src-tauri` → `<repo>` → `<repo>/core`
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .map(|repo| repo.join("core"))
}

/// Lets the WebView read the Content Pack **of the Core that is actually running**, and
/// nothing else (ADR-029).
///
/// Anything outside this directory is refused by Tauri before it is opened — the Stage is
/// never trusted with a path (docs/contracts/security-boundaries.md B2).
///
/// **The directory comes from the launch decision**, not from a search of its own. Searching
/// separately pointed the WebView at a stale build's Content Pack while Core read the
/// repository's, and the character simply never appeared (2026-08-19).
///
/// **Failure is not fatal.** Lumi runs with the placeholder, and the Stage says why.
pub(crate) fn allow_content_pack(app: &AppHandle, dir: &Path) {
    if !dir.is_dir() {
        log::warn!("Content Pack not found ({}): launching with placeholder", dir.display());
        return;
    }

    match app.asset_protocol_scope().allow_directory(dir, true) {
        Ok(()) => log::info!("Allowed Content Pack directory: {}", dir.display()),
        // **Never silently degrade.** Without this the character simply never appears, and
        // the reason would exist nowhere
        Err(error) => {
            log::error!("Cannot allow Content Pack directory ({}): {error}", dir.display())
        }
    }
}
