//! Lumi Shell — OS 特権プリミティブのみ。**判断を持たない**（Invariant 8 の拒否を除く）。
//!
//! 設計 → docs/architecture/ui.md, docs/interfaces/shell.md,
//! docs/contracts/security-boundaries.md の B3

mod core_endpoint;
mod core_process;
mod hover;
mod job_object;
mod os_command;
mod tray;
mod window;
mod ws_client;

use std::path::PathBuf;

use rand::RngCore as _;
use tauri::{AppHandle, Manager as _, RunEvent, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::core_endpoint::{shell_core_endpoint, spawn_endpoint_notifier, CoreEndpointState};
use crate::core_process::{find_sidecar, resolve_launch_spec, CoreSupervisor, CoreTokens};
use crate::hover::{shell_hit_region_set, spawn_cursor_watcher, HitRegionStore};
use crate::window::{
    compute_credits_window_options, compute_stage_window_options, StageConfig, WindowKind,
    WindowSpec,
};

/// 仕様どおりにウィンドウを開く。
///
/// **ここに条件分岐を書かない。** 「どう見えるべきか」は `window.rs` の純粋関数が決め、
/// この関数はそれを Tauri の API に流すだけにする。そうしないと、ウィンドウの挙動が
/// テストできない場所に散らばる。
fn create_window(
    app: &AppHandle,
    spec: &WindowSpec,
    url: WebviewUrl,
) -> tauri::Result<WebviewWindow> {
    let mut builder = WebviewWindowBuilder::new(app, spec.label, url)
        .title(spec.title)
        .inner_size(spec.width, spec.height)
        .transparent(spec.transparent)
        .decorations(spec.decorations)
        .always_on_top(spec.always_on_top)
        .skip_taskbar(spec.skip_taskbar)
        .resizable(spec.resizable)
        .shadow(spec.shadow)
        .focused(spec.focused)
        .visible(spec.visible);

    if let Some((x, y)) = spec.position {
        builder = builder.position(x, y);
    }

    let win = builder.build()?;

    // ビルダーに無い設定は生成後に適用する。
    win.set_content_protected(spec.content_protected)?;
    win.set_ignore_cursor_events(spec.click_through)?;

    Ok(win)
}

/// クレジットとライセンスを開く（トレイ → クレジット）。
///
/// **Core に繋がない静的なページ**（docs/architecture/ui.md）。
/// ライセンス文書の提示は Lumi の動作状態と無関係な義務であり、
/// Core が落ちていても読めなければならない。
///
/// すでに開いていれば作り直さず前に出す。**同じ文書の窓が2枚出ても意味が無い。**
fn open_credits(app: &AppHandle) {
    let label = WindowKind::Credits.label();
    if let Some(existing) = app.get_webview_window(label) {
        let _ = existing.show();
        let _ = existing.unminimize();
        let _ = existing.set_focus();
        return;
    }

    let spec = compute_credits_window_options();
    match create_window(app, &spec, WebviewUrl::App("credits.html".into())) {
        Ok(_) => log::info!("credits.opened"),
        // **黙って何も起きない、にしない。** 開けなかったならログに残す。
        Err(error) => log::error!("credits.open_failed {error}"),
    }
}

/// WS token を生成する。**Shell が作り、環境変数で Core に渡す**
/// （docs/interfaces/shell.md）。プロセスの外に出す経路をここ以外に作らない。
fn generate_token() -> String {
    let mut bytes = [0u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// 開発時に Core を起動するためのプロジェクトディレクトリ。
///
/// リリースビルドでは同梱サイドカーを使うので `None`。
/// **配布物にリポジトリのパスを焼き込まない**ため、debug のときだけ返す。
fn dev_core_project_dir() -> Option<PathBuf> {
    if cfg!(debug_assertions) {
        Some(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../core"))
    } else {
        None
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // **role ごとに別の token を作る。** Stage には Stage 用しか渡さない
    // （docs/contracts/security-boundaries.md B2 / B3）。
    let shell_token = generate_token();
    let stage_token = generate_token();
    let (supervisor, port_rx) = CoreSupervisor::new();

    let setup_supervisor = supervisor.clone();
    let setup_shell_token = shell_token.clone();

    let app = tauri::Builder::default()
        .manage(HitRegionStore::default())
        .manage(CoreEndpointState::new(port_rx.clone(), stage_token.clone()))
        // `shell.*` の allowlist（B1）。ここに載っていないものは Stage から呼べない。
        // **AI の判断を運ぶコマンドをここに足さない。**
        .invoke_handler(tauri::generate_handler![shell_hit_region_set, shell_core_endpoint])
        .setup(move |app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default().level(log::LevelFilter::Info).build(),
                )?;
            }

            // ウィンドウ位置の永続化は Core が持つ（設定の保存は Core → docs/architecture/ui.md §2）。
            // Phase 0 の時点では Core との接続前に Stage を出すため、既定値で開く。
            let spec = compute_stage_window_options(&StageConfig::default());
            create_window(app.handle(), &spec, WebviewUrl::App("index.html".into()))?;

            // トレイが無いと Lumi を終了できない（`stage` は枠なし・タスクバー非表示）。
            tray::init(app.handle())?;

            spawn_cursor_watcher(app.handle().clone());

            let sidecar = std::env::current_exe()
                .ok()
                .and_then(|exe| exe.parent().map(std::path::Path::to_path_buf))
                .and_then(|dir| find_sidecar(&dir));

            spawn_endpoint_notifier(app.handle().clone(), port_rx.clone());

            match resolve_launch_spec(sidecar.as_deref(), dev_core_project_dir().as_deref()) {
                Some(launch) => {
                    setup_supervisor.start(
                        launch,
                        CoreTokens { shell: setup_shell_token.clone(), stage: stage_token.clone() },
                    );
                    ws_client::start(
                        app.handle().clone(),
                        setup_shell_token.clone(),
                        port_rx.clone(),
                    );
                }
                // **黙って劣化しない。** Core が無いなら、無いと言う。
                None => log::error!("core.not_found 起動できる Core が見つからない"),
            }

            log::info!("shell.started");
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Tauri アプリの初期化に失敗した");

    app.run(move |_app, event| {
        if let RunEvent::Exit = event {
            // **ゾンビを残さない。** 終了時に確実に Core を落とす。
            tauri::async_runtime::block_on(supervisor.shutdown());
        }
    });
}
