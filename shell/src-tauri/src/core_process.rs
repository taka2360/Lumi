//! Core サイドカーの起動・生存監視・確実な終了。
//!
//! 要件 → docs/interfaces/shell.md `spawnSidecar`
//!
//! | # | 要件 | 実装 |
//! |---|---|---|
//! | 1 | Shell 終了時に Core も確実に終了する | **Job Object（強制終了でも効く）＋** 明示的 kill ＋ stdin EOF による自己終了の3層 → `job_object.rs` |
//! | 2 | Core が異常終了したら検知して再起動する | 監視タスクが指数バックオフで再起動 |
//! | 3 | WS token を環境変数で渡す | `LUMI_WS_TOKEN_SHELL` / `LUMI_WS_TOKEN_STAGE`。**コマンドラインに載せない** |
//! | 4 | stdout / stderr を Shell 側でログに落とす | 1行ずつ `log` に流す |
//!
//! **ポート番号は Core の stdout から読む。** Core は 127.0.0.1 の空きポートに bind し、
//! `core.ws.listening` という構造化ログを 1 行出す。Shell が先にポートを決めて渡す方式は、
//! 決めてから起動するまでの間に他プロセスに取られる余地があるため採らない。

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use tokio::io::{AsyncBufReadExt as _, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{watch, Mutex};

use crate::job_object::KillOnCloseJob;

/// Core に渡す token。**role ごとに別のものを渡す**（B2 / B3）。
#[derive(Debug, Clone)]
pub struct CoreTokens {
    pub shell: String,
    pub stage: String,
}

/// Core を起動するコマンド。**純粋なデータ**にしておき、決定をテストできるようにする。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CoreLaunchSpec {
    pub program: PathBuf,
    pub args: Vec<String>,
}

/// 配布物に同梱されたサイドカーの実行体名。
const SIDECAR_NAME: &str = "lumi-core.exe";

/// サイドカーを探す場所（実行体のあるディレクトリからの相対）。**順に見る。**
///
/// `core/` は Tauri の `resources` が配置する場所。PyInstaller の onedir 出力は
/// 実行体と 80 個以上の依存ファイルの組なので、**Shell の隣に撒かずにディレクトリごと置く**
/// （→ docs/decisions/ADR-021-sidecar-packaging.md）。
const SIDECAR_DIRS: &[&str] = &["core", "."];

/// 子プロセスの終了を確認する間隔。`wait` は可変参照を要求し、
/// 終了時に kill するための保持と両立しないので、短い間隔で `try_wait` する。
const WAIT_POLL_INTERVAL: Duration = Duration::from_millis(100);

/// どうやって Core を起動するかを決める。**純粋関数**。
///
/// 1. 同梱サイドカーがあればそれを使う（配布された Lumi の経路）
/// 2. 無ければ開発用に `uv run` で `core/` を起動する
/// 3. どちらも無ければ `None` = **起動しない**。黙って劣化させない
pub fn resolve_launch_spec(
    sidecar: Option<&Path>,
    core_project_dir: Option<&Path>,
) -> Option<CoreLaunchSpec> {
    if let Some(path) = sidecar {
        return Some(CoreLaunchSpec { program: path.to_path_buf(), args: Vec::new() });
    }
    let project = core_project_dir?;
    Some(CoreLaunchSpec {
        program: PathBuf::from("uv"),
        args: vec![
            "run".into(),
            "--project".into(),
            project.to_string_lossy().into_owned(),
            "lumi-core".into(),
        ],
    })
}

/// 同梱サイドカーを探す。存在しなければ `None`（= 開発経路にフォールバックする）。
pub fn find_sidecar(exe_dir: &Path) -> Option<PathBuf> {
    sidecar_candidates(exe_dir).into_iter().find(|path| path.is_file())
}

/// 探す順に並べた候補。**純粋関数**（ファイルシステムに触らない）。
pub fn sidecar_candidates(exe_dir: &Path) -> Vec<PathBuf> {
    SIDECAR_DIRS.iter().map(|dir| exe_dir.join(dir).join(SIDECAR_NAME)).collect()
}

/// stdout の 1 行から listen ポートを取り出す。**純粋関数**。
///
/// Core の構造化ログ（1行1JSON）のうち `event == "core.ws.listening"` の行だけを見る。
pub fn parse_listening_port(line: &str) -> Option<u16> {
    let value: serde_json::Value = serde_json::from_str(line).ok()?;
    if value.get("event").and_then(|v| v.as_str()) != Some("core.ws.listening") {
        return None;
    }
    let port = value.get("port")?.as_u64()?;
    u16::try_from(port).ok()
}

/// 再起動の待ち時間。**純粋関数**。落ち続けるものを 100% CPU で叩き直さない。
pub fn restart_delay(consecutive_failures: u32) -> Duration {
    let capped = consecutive_failures.min(4);
    Duration::from_millis(500 * 2_u64.pow(capped))
}

/// 起動中の Core。**stdin を保持し続ける**ことが重要。
/// ここで stdin を drop すると Core 側の親監視が EOF を受け取って即座に終了する。
struct RunningCore {
    child: Child,
}

#[derive(Clone)]
pub struct CoreSupervisor {
    running: Arc<Mutex<Option<RunningCore>>>,
    shutdown: Arc<std::sync::atomic::AtomicBool>,
    port_tx: watch::Sender<Option<u16>>,
    /// Shell のプロセスが消えた時点で Core（とその子）を道連れにするためのジョブ。
    /// **強制終了に耐える唯一の層**なので、supervisor と同じ寿命で持つ。
    job: Arc<Option<KillOnCloseJob>>,
}

impl CoreSupervisor {
    pub fn new() -> (Self, watch::Receiver<Option<u16>>) {
        let (port_tx, port_rx) = watch::channel(None);
        let job = KillOnCloseJob::create();
        if job.is_none() {
            // 黙って弱くならない。ゾンビが残りうる状態であることをログに残す。
            log::warn!("core.job_object.unavailable 強制終了時に Core が残る可能性がある");
        }
        let supervisor = Self {
            running: Arc::new(Mutex::new(None)),
            shutdown: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            port_tx,
            job: Arc::new(job),
        };
        (supervisor, port_rx)
    }

    /// 監視ループを開始する。Core が落ちたら再起動する。
    pub fn start(&self, spec: CoreLaunchSpec, tokens: CoreTokens) {
        let supervisor = self.clone();
        tauri::async_runtime::spawn(async move {
            let mut failures: u32 = 0;
            loop {
                if supervisor.is_shutting_down() {
                    return;
                }
                if let Err(err) = supervisor.spawn_once(&spec, &tokens).await {
                    log::error!("core.spawn_failed {err}");
                }
                // 正常終了でも異常終了でも、Shell が生きている限り Core は居るべき。
                failures = failures.saturating_add(1);
                if supervisor.is_shutting_down() {
                    return;
                }
                let _ = supervisor.port_tx.send(None);
                tokio::time::sleep(restart_delay(failures)).await;
            }
        });
    }

    fn is_shutting_down(&self) -> bool {
        self.shutdown.load(std::sync::atomic::Ordering::SeqCst)
    }

    async fn spawn_once(&self, spec: &CoreLaunchSpec, tokens: &CoreTokens) -> std::io::Result<()> {
        let mut command = Command::new(&spec.program);
        command
            .args(&spec.args)
            // **token はコマンドラインに載せない。**（ps で他プロセスから見える）
            .env("LUMI_WS_TOKEN_SHELL", &tokens.shell)
            .env("LUMI_WS_TOKEN_STAGE", &tokens.stage)
            // 親が消えたら自分も終わる（ゾンビ対策の2枚目）。
            .env("LUMI_PARENT_WATCH", "stdin")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);

        let mut child = command.spawn()?;

        // **起動直後にジョブへ入れる。** ここより後に Shell が死ぬと道連れにできない。
        #[cfg(windows)]
        if let Some(job) = self.job.as_ref() {
            let assigned = match child.raw_handle() {
                Some(handle) => job.assign(handle),
                None => false,
            };
            if !assigned {
                log::warn!("core.job_object.assign_failed 強制終了時に Core が残る可能性がある");
            }
        }

        if let Some(stdout) = child.stdout.take() {
            let port_tx = self.port_tx.clone();
            tauri::async_runtime::spawn(async move {
                let mut lines = BufReader::new(stdout).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    if let Some(port) = parse_listening_port(&line) {
                        log::info!("core.ws.listening port={port}");
                        let _ = port_tx.send(Some(port));
                    }
                    log::info!("core: {line}");
                }
            });
        }
        if let Some(stderr) = child.stderr.take() {
            tauri::async_runtime::spawn(async move {
                let mut lines = BufReader::new(stderr).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    log::warn!("core!: {line}");
                }
            });
        }

        log::info!("core.spawned program={:?}", spec.program);

        // 待つ前に child を state に置く（終了時に kill できるようにする）。
        // `wait` には可変参照が要るので、待機用のハンドルだけ別に取る。
        let pid = child.id();
        {
            let mut slot = self.running.lock().await;
            *slot = Some(RunningCore { child });
        }
        loop {
            let mut slot = self.running.lock().await;
            let Some(running) = slot.as_mut() else {
                // shutdown 側が奪って kill した。
                log::info!("core.taken_over_by_shutdown pid={pid:?}");
                return Ok(());
            };
            match running.child.try_wait()? {
                Some(status) => {
                    *slot = None;
                    log::warn!("core.exited pid={pid:?} status={status:?}");
                    return Ok(());
                }
                None => {
                    drop(slot);
                    tokio::time::sleep(WAIT_POLL_INTERVAL).await;
                }
            }
        }
    }

    /// Shell の終了時に呼ぶ。**確実に殺す。ゾンビを残さない。**
    pub async fn shutdown(&self) {
        self.shutdown.store(true, std::sync::atomic::Ordering::SeqCst);
        let mut slot = self.running.lock().await;
        if let Some(mut running) = slot.take() {
            // stdin を先に落とす → Core は EOF を見て自分で終了する（正常系）。
            drop(running.child.stdin.take());
            // 応答が無ければ確実に殺す。
            let _ = running.child.kill().await;
            log::info!("core.killed");
        }
    }
}

#[cfg(test)]
mod tests {
    // テストは panic してよい場所なので unwrap を許す。
    #![allow(clippy::unwrap_used)]

    use super::*;

    #[test]
    fn prefers_the_bundled_sidecar() {
        let spec = resolve_launch_spec(Some(Path::new("C:/app/lumi-core.exe")), None).unwrap();
        assert_eq!(spec.program, PathBuf::from("C:/app/lumi-core.exe"));
        assert!(spec.args.is_empty());
    }

    #[test]
    fn looks_in_the_resources_directory_first() {
        // 配布物では `core/` に入る（Tauri の resources）。
        // 実行体の隣も見るのは、手で置いた場合と将来の externalBin のため。
        let candidates = sidecar_candidates(Path::new("C:/app"));
        assert_eq!(candidates.first().unwrap(), Path::new("C:/app/core/lumi-core.exe"));
        assert_eq!(candidates.len(), 2);
        assert!(candidates.iter().any(|p| p == Path::new("C:/app/./lumi-core.exe")));
    }

    #[test]
    fn falls_back_to_uv_in_development() {
        let spec = resolve_launch_spec(None, Some(Path::new("C:/repo/core"))).unwrap();
        assert_eq!(spec.program, PathBuf::from("uv"));
        assert_eq!(spec.args, vec!["run", "--project", "C:/repo/core", "lumi-core"]);
    }

    #[test]
    fn refuses_to_guess_when_nothing_is_available() {
        // 黙って起動しないより、起動しないことが分かる方がよい。
        assert_eq!(resolve_launch_spec(None, None), None);
    }

    #[test]
    fn reads_the_listening_port_from_structured_logs() {
        let line = r#"{"event":"core.ws.listening","host":"127.0.0.1","port":50505}"#;
        assert_eq!(parse_listening_port(line), Some(50505));
    }

    #[test]
    fn ignores_other_lines() {
        assert_eq!(parse_listening_port("not json"), None);
        assert_eq!(parse_listening_port(r#"{"event":"core.started"}"#), None);
        assert_eq!(parse_listening_port(r#"{"event":"core.ws.listening"}"#), None);
        // ポート番号として成立しない値を通さない
        assert_eq!(parse_listening_port(r#"{"event":"core.ws.listening","port":99999}"#), None);
    }

    #[test]
    fn backs_off_but_stays_bounded() {
        assert_eq!(restart_delay(0), Duration::from_millis(500));
        assert_eq!(restart_delay(1), Duration::from_millis(1000));
        assert_eq!(restart_delay(4), Duration::from_millis(8000));
        assert_eq!(restart_delay(100), Duration::from_millis(8000));
    }
}
