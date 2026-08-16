//! ホバー検知とクリックスルーの切り替え。
//!
//! **なぜ自前でカーソルを監視するのか**
//! Tauri 2 の `set_ignore_cursor_events(true)` は Electron の
//! `setIgnoreMouseEvents(true, { forward: true })` と違い、mousemove も届かなくなる。
//! そのため「クリックは貫通するが、キャラクターに乗せたら反応する」を WebView 側だけでは作れない。
//! → docs/interfaces/shell.md「`setClickThrough` と `onCursorMove` — 最大の差異点」
//!
//! **判定は Shell 側で行う。** 60Hz のカーソル位置を毎回 Stage に送って往復すると、
//! `shell.*` の「1ms 以下であるべきもの」という規則を守れない。
//! Stage は当たり判定領域（`shell.hit_region.set`）を渡すだけで、判定そのものは
//! `window.rs` の純粋関数が行う。**AI の判断はこの経路に一切乗らない。**

use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Emitter as _, Manager as _};

use crate::window::{
    decide_click_through, decide_hover_transition, HitRect, HitRegion, HoverState, Point,
    WindowKind,
};

/// ポーリング間隔。60Hz 相当。
/// 実測結果 → docs/measurements/phase0.md
const POLL_INTERVAL: Duration = Duration::from_millis(16);

/// ホバー状態の通知イベント名。
///
/// docs では `shell.hover.state` と書くが、**Tauri のイベント名にドットは使えない**
/// （英数字と `-` `/` `:` `_` のみ）。`.` を `:` に置き換えた名前を線上の名前とする。
/// 対応する Stage 側の定数は `stage/src/platform/tauri.ts`。
pub(crate) const EVENT_HOVER_STATE: &str = "shell:hover:state";

/// Stage から渡された当たり判定領域。Shell が持つ唯一の可変状態。
#[derive(Default)]
pub struct HitRegionStore(Mutex<HitRegion>);

impl HitRegionStore {
    fn snapshot(&self) -> HitRegion {
        match self.0.lock() {
            Ok(guard) => guard.clone(),
            // ロックが毒されたら「領域不明」として扱う = クリックスルーを維持する。
            // ここで掴む側に倒すと、デスクトップが操作不能になる。
            Err(_) => HitRegion::default(),
        }
    }

    fn set(&self, region: HitRegion) {
        if let Ok(mut guard) = self.0.lock() {
            *guard = region;
        }
    }
}

/// `shell.hit_region.set` — Stage が VRM の描画結果から算出した当たり判定領域を渡す。
///
/// 座標は **Stage ウィンドウのクライアント領域の左上を原点とする物理ピクセル**。
/// CSS ピクセルではない（`devicePixelRatio` を掛けるのは Stage 側の責務）。
#[tauri::command]
pub fn shell_hit_region_set(store: tauri::State<'_, HitRegionStore>, rects: Vec<HitRect>) {
    store.set(HitRegion { rects });
}

/// カーソル監視スレッドを起動する。
///
/// asyncio ならぬ Tauri の非同期ランタイムには載せない。16ms 周期の固定ループであり、
/// 他のタスクと同居させるとジッタが乗るため、専用スレッドにする。
pub fn spawn_cursor_watcher(app: AppHandle) {
    std::thread::spawn(move || {
        let mut hover = HoverState::Outside;
        // 「まだ一度も適用していない」を None で表す。初回は必ず適用する。
        let mut applied_click_through: Option<bool> = None;

        loop {
            std::thread::sleep(POLL_INTERVAL);

            let Some(win) = app.get_webview_window(WindowKind::Stage.label()) else {
                // Stage がまだ無い / 閉じられた。監視を続けて復帰を待つ。
                continue;
            };
            let (Ok(cursor), Ok(origin)) = (app.cursor_position(), win.inner_position()) else {
                continue;
            };

            let point =
                Point { x: cursor.x - f64::from(origin.x), y: cursor.y - f64::from(origin.y) };
            let region = store_of(&app).snapshot();

            let click_through = decide_click_through(point, &region);
            if applied_click_through != Some(click_through) {
                if win.set_ignore_cursor_events(click_through).is_ok() {
                    applied_click_through = Some(click_through);
                } else {
                    // 適用に失敗したら次の周期で再試行する（状態を進めない）。
                    continue;
                }
            }

            if let Some(next) = decide_hover_transition(point, &region, hover) {
                hover = next;
                // 変化したときだけ送る。60Hz で送ると `shell.*` の予算を食い潰す。
                let _ = win.emit(EVENT_HOVER_STATE, next);
                log::info!("shell.hover.state {next:?} at ({}, {})", point.x, point.y);
            }
        }
    });
}

fn store_of(app: &AppHandle) -> tauri::State<'_, HitRegionStore> {
    app.state::<HitRegionStore>()
}
