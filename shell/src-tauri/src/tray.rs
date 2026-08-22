//! The tray — Phase 0 only has "credits" and "quit."
//!
//! Design → docs/architecture/ui.md "Tray menu"
//!
//! **No AI judgment is ever shown in the tray.** All that's listed here is
//! opening/closing windows and terminating the process — nothing about what Lumi
//! is thinking appears at all (`shell.*` never carries AI judgment).

use tauri::image::Image;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager as _};

/// Fixed by Shell: Stage never gets an arbitrary-URL capability.
const OLLAMA_DOWNLOAD_URL: &str = "https://ollama.com/download";

use crate::locale::Locale;

/// The native tray icon is kept at the Windows notification-area size.
/// Embedding the tracked asset makes the tray independent of whichever
/// default window icon Tauri selects from the bundle icon set.
const TRAY_ICON: &[u8] = include_bytes!("../icons/32x32.png");

/// The tray menu's items. **Maps id to behavior via a pure function**, so it's
/// testable without opening the menu.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayAction {
    /// Opens credits and licenses (the "somewhere findable with a bit of effort" from docs/licensing.md §6).
    OpenCredits,
    /// Quits Lumi.
    Quit,
}

/// The (id, display name) pairs in menu order.
///
/// **Quit is placed last.** Crashing from a misclick would be the worst outcome.
fn menu_items(locale: Locale) -> [(&'static str, &'static str); 2] {
    match locale {
        Locale::Ja => [("credits", "クレジットとライセンス"), ("quit", "終了")],
        Locale::En => [("credits", "Credits and licenses"), ("quit", "Quit")],
    }
}

fn build_menu(app: &AppHandle, locale: Locale) -> tauri::Result<Menu<tauri::Wry>> {
    let labels = menu_items(locale);
    let mut items: Vec<MenuItem<tauri::Wry>> = Vec::with_capacity(labels.len());
    for (id, label) in &labels {
        items.push(MenuItem::with_id(app, *id, *label, true, None::<&str>)?);
    }
    let refs: Vec<&dyn tauri::menu::IsMenuItem<tauri::Wry>> =
        items.iter().map(|item| item as &dyn tauri::menu::IsMenuItem<tauri::Wry>).collect();
    Menu::with_items(app, &refs)
}

/// Maps a menu id to a behavior. **A pure function.**
///
/// Does nothing for an unknown id. Since Shell builds the tray itself, an
/// unknown id shouldn't arrive, but **the type guarantees nothing silently
/// happens if one does.**
fn resolve_tray_action(id: &str) -> Option<TrayAction> {
    match id {
        "credits" => Some(TrayAction::OpenCredits),
        "quit" => Some(TrayAction::Quit),
        _ => None,
    }
}

/// Creates the tray icon and menu.
///
/// **The `stage` window is frameless, click-through, and hidden from the
/// taskbar, so without the tray there'd be no way to quit Lumi.** Never
/// silently continues if this fails.
pub fn init(app: &AppHandle, locale: Locale) -> tauri::Result<()> {
    let menu = build_menu(app, locale)?;

    let icon = Image::from_bytes(TRAY_ICON)?;

    TrayIconBuilder::with_id("lumi")
        .icon(icon)
        .tooltip("Lumi")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match resolve_tray_action(event.id.as_ref()) {
            Some(TrayAction::OpenCredits) => crate::open_credits(app),
            // Quit goes through RunEvent::Exit, so Core goes down with it.
            Some(TrayAction::Quit) => app.exit(0),
            None => log::warn!("tray.unknown_item id={}", event.id.as_ref()),
        })
        .build(app)?;

    Ok(())
}

/// Quits Lumi, from the Stage.
///
/// **Exists for the setup screen's 終了 button** (ADR-034): someone stopped before Lumi
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

/// Opens the static credits and licenses window from the Stage action menu.
///
/// **No URL or window label comes from Stage.** The only possible result is the same
/// bundled document opened by the tray, so exposing this does not grant arbitrary
/// navigation or window creation.
// A synchronous IPC command runs inline on the event-loop thread. Window creation then
// dispatches back to that same loop and waits, deadlocking the app on Windows. `async`
// runs this synchronous function on Tauri's thread pool, leaving the loop free to create
// and paint the WebView (observed as a blank credits window plus frozen quit actions).
#[tauri::command(async)]
pub fn shell_credits_open(app: AppHandle) {
    log::info!("shell.credits_open requested by stage");
    crate::open_credits(&app);
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

/// Updates native Shell-owned labels after Core accepted a locale setting.
#[tauri::command]
pub fn shell_locale_set(app: AppHandle, locale: &str) -> Result<(), String> {
    let locale = Locale::from_code(locale).ok_or_else(|| "unsupported_locale".to_owned())?;
    let tray = app.tray_by_id("lumi").ok_or_else(|| "tray_not_found".to_owned())?;
    let menu = build_menu(&app, locale).map_err(|error| error.to_string())?;
    tray.set_menu(Some(menu)).map_err(|error| error.to_string())?;

    if let Some(credits) = app.get_webview_window(crate::window::WindowKind::Credits.label()) {
        credits
            .set_title(crate::window::credits_title(locale))
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    // Tests are allowed to panic, so unwrap is permitted here.
    #![allow(clippy::unwrap_used)]

    use super::*;

    #[test]
    fn every_menu_item_resolves_to_an_action() {
        for (id, label) in menu_items(Locale::Ja) {
            assert!(resolve_tray_action(id).is_some(), "No action mapped for {id}");
            assert!(!label.is_empty());
        }
    }

    #[test]
    fn credits_and_quit_are_both_present() {
        // Credits are a distribution obligation (docs/licensing.md §6), and quit
        // has no other means besides the tray (docs/architecture/ui.md). Neither can be dropped.
        let actions: Vec<_> =
            menu_items(Locale::Ja).iter().filter_map(|(id, _)| resolve_tray_action(id)).collect();
        assert!(actions.contains(&TrayAction::OpenCredits));
        assert!(actions.contains(&TrayAction::Quit));
    }

    #[test]
    fn unknown_menu_id_does_nothing() {
        assert_eq!(resolve_tray_action("settings"), None);
        assert_eq!(resolve_tray_action(""), None);
    }

    #[test]
    fn english_menu_is_localized() {
        assert_eq!(menu_items(Locale::En)[0].1, "Credits and licenses");
        assert_eq!(menu_items(Locale::En)[1].1, "Quit");
    }

    #[test]
    fn unknown_locale_is_refused() {
        assert_eq!(Locale::from_code("fr"), None);
    }

    #[test]
    fn credits_open_never_runs_inline_in_the_ipc_handler() {
        // Regression: a synchronous command deadlocks Windows while window creation
        // dispatches back to the event loop, leaving a blank window and freezing quit.
        let source = include_str!("tray.rs").replace("\r\n", "\n");
        assert!(source.contains("#[tauri::command(async)]\npub fn shell_credits_open"));
    }
}
