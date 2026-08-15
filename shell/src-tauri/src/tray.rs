//! トレイ — Phase 0 は「クレジット」と「終了」だけ。
//!
//! 設計 → docs/architecture/ui.md「トレイメニュー」
//!
//! **トレイに AI の判断を出さない。** ここに並ぶのはウィンドウの開閉とプロセスの終了だけで、
//! Lumi が何を考えているかは一切載らない（`shell.*` は AI の判断を運ばない）。

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::AppHandle;

/// トレイメニューの項目。**id と挙動を純粋関数で対応づける**ので、
/// メニューを開かずにテストできる。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayAction {
    /// クレジットとライセンスを開く（docs/licensing.md §6 の「少し探せばわかる場所」）。
    OpenCredits,
    /// Lumi を終了する。
    Quit,
}

/// メニューに並べる順の (id, 表示名)。
///
/// **終了を最後に置く。** 押し間違いで落ちるのが一番困る。
const MENU_ITEMS: &[(&str, &str)] = &[("credits", "クレジットとライセンス"), ("quit", "終了")];

/// メニュー id を挙動に対応づける。**純粋関数**。
///
/// 知らない id には何もしない。トレイは Shell が自分で作るので未知の id は来ないが、
/// **来たときに黙って何かをしないこと**を型で保証しておく。
fn resolve_tray_action(id: &str) -> Option<TrayAction> {
    match id {
        "credits" => Some(TrayAction::OpenCredits),
        "quit" => Some(TrayAction::Quit),
        _ => None,
    }
}

/// トレイアイコンとメニューを作る。
///
/// **`stage` は枠なし・クリックスルー・タスクバー非表示なので、
/// トレイが無いと Lumi を終了できない。** 失敗したら黙って続けない。
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
            // 終了は RunEvent::Exit を経由するので、Core も一緒に落ちる。
            Some(TrayAction::Quit) => app.exit(0),
            None => log::warn!("tray.unknown_item id={}", event.id.as_ref()),
        })
        .build(app)?;

    Ok(())
}

#[cfg(test)]
mod tests {
    // テストは panic してよい場所なので unwrap を許す。
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
        // クレジットは配布時の義務（docs/licensing.md §6）、
        // 終了はトレイ以外に手段が無い（docs/architecture/ui.md）。どちらも落とせない。
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
