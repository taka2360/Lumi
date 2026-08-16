//! The tray — Phase 0 only has "credits" and "quit."
//!
//! Design → docs/architecture/ui.md "Tray menu"
//!
//! **No AI judgment is ever shown in the tray.** All that's listed here is
//! opening/closing windows and terminating the process — nothing about what Lumi
//! is thinking appears at all (`shell.*` never carries AI judgment).

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::AppHandle;

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
const MENU_ITEMS: &[(&str, &str)] = &[("credits", "クレジットとライセンス"), ("quit", "終了")];

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
pub fn init(app: &AppHandle) -> tauri::Result<()> {
    let mut items: Vec<MenuItem<tauri::Wry>> = Vec::with_capacity(MENU_ITEMS.len());
    for (id, label) in MENU_ITEMS {
        items.push(MenuItem::with_id(app, *id, *label, true, None::<&str>)?);
    }
    let refs: Vec<&dyn tauri::menu::IsMenuItem<tauri::Wry>> =
        items.iter().map(|i| i as &dyn tauri::menu::IsMenuItem<tauri::Wry>).collect();
    let menu = Menu::with_items(app, &refs)?;

    let icon = app
        .default_window_icon()
        .cloned()
        .ok_or_else(|| tauri::Error::AssetNotFound("トレイに使うアイコンが見つからない".into()))?;

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

#[cfg(test)]
mod tests {
    // Tests are allowed to panic, so unwrap is permitted here.
    #![allow(clippy::unwrap_used)]

    use super::*;

    #[test]
    fn every_menu_item_resolves_to_an_action() {
        for (id, label) in MENU_ITEMS {
            assert!(resolve_tray_action(id).is_some(), "{id} に対応する挙動が無い");
            assert!(!label.is_empty());
        }
    }

    #[test]
    fn credits_and_quit_are_both_present() {
        // Credits are a distribution obligation (docs/licensing.md §6), and quit
        // has no other means besides the tray (docs/architecture/ui.md). Neither can be dropped.
        let actions: Vec<_> =
            MENU_ITEMS.iter().filter_map(|(id, _)| resolve_tray_action(id)).collect();
        assert!(actions.contains(&TrayAction::OpenCredits));
        assert!(actions.contains(&TrayAction::Quit));
    }

    #[test]
    fn unknown_menu_id_does_nothing() {
        assert_eq!(resolve_tray_action("settings"), None);
        assert_eq!(resolve_tray_action(""), None);
    }
}
