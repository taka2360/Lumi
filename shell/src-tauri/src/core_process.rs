//! Launching the Core sidecar, watching it stay alive, and reliably terminating it.
//!
//! Requirements → docs/interfaces/shell.md `spawnSidecar`
//!
//! | # | Requirement | Implementation |
//! |---|---|---|
//! | 1 | Core reliably terminates when Shell terminates | Three layers: **Job Object (works even under force-kill) +** explicit kill + self-termination via stdin EOF → `job_object.rs` |
//! | 2 | Detects and restarts Core if it exits abnormally | The watcher task restarts with exponential backoff |
//! | 3 | Pass the WS token via environment variable | `LUMI_WS_TOKEN_SHELL` / `LUMI_WS_TOKEN_STAGE` / `LUMI_WS_TOKEN_PANEL`. **Never put it on the command line** |
//! | 4 | Shell logs stdout / stderr | Streamed to `log` line by line |
//!
//! **The port number is read from Core's stdout.** Core binds to a free port
//! on 127.0.0.1 and emits a single structured log line, `core.ws.listening`.
//! Having Shell decide the port up front and hand it over isn't used, since
//! another process could grab it in the gap between deciding and launching.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use tokio::io::{AsyncBufReadExt as _, BufReader};
use tokio::process::{Child, ChildStderr, ChildStdout, Command};
use tokio::sync::{watch, Mutex};

use crate::job_object::KillOnCloseJob;

/// `CREATE_NO_WINDOW`. Launches the console executable **without a window.**
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// The token passed to Core. **A different one is passed per role** (B2 / B3).
#[derive(Debug, Clone)]
pub struct CoreTokens {
    pub shell: String,
    pub stage: String,
    /// The auxiliary windows' token (ADR-042). **Separate from the Stage's** for the same
    /// reason the Stage's is separate from the Shell's: a role that can be claimed with
    /// somebody else's token is not a role.
    pub panel: String,
}

/// The command that launches Core. Kept as **pure data** so the decision is testable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CoreLaunchSpec {
    pub program: PathBuf,
    pub args: Vec<String>,
    /// Where **the Core being launched** keeps its Content Pack (ADR-029).
    ///
    /// **Derived from the same decision that picked which Core runs**, so the two cannot
    /// disagree. Resolving it separately allowed the WebView to be pointed at a stale
    /// `target/debug/core/_internal/content` while Core read the repository's — the character
    /// then never appeared, and the only sign was `asset protocol not configured to allow the
    /// path` (2026-08-19).
    pub content_dir: PathBuf,
}

/// The sidecar executable name bundled into the distributable.
const SIDECAR_NAME: &str = "lumi-core.exe";

/// Where to look for the sidecar (relative to the directory the executable is
/// in). **Checked in this order.**
///
/// `core/` is where Tauri's `resources` places it. PyInstaller's onedir
/// output is a bundle of the executable plus 80+ dependency files, so
/// **it's placed as a whole directory instead of scattered next to Shell**
/// (→ docs/decisions/ADR-021-sidecar-packaging.md).
const SIDECAR_DIRS: &[&str] = &["core", "."];

/// The interval for checking whether the child process has exited. `wait`
/// requires a mutable reference, which conflicts with holding it to `kill` on
/// shutdown, so `try_wait` is polled at a short interval instead.
const WAIT_POLL_INTERVAL: Duration = Duration::from_millis(100);

/// Decides how to launch Core. **A pure function.**
///
/// 1. If a dev project is known, launch `core/` via `uv run`
/// 2. Otherwise, the bundled sidecar (the path for a distributed Lumi)
/// 3. If neither is available, `None` = **don't launch.** Never degrade silently
///
/// **The reason source is preferred during development is that the built
/// sidecar sits in the same location.** `tauri dev` also places `resources`
/// under `target/debug/core/`, so preferring the sidecar would mean **editing
/// Python has no effect** (the previously built executable would run
/// instead). This actually happened (2026-08-15). `core_project_dir` is only
/// ever supplied in debug builds, so the sidecar is always chosen in the distributable.
pub fn resolve_launch_spec(
    sidecar: Option<&Path>,
    core_project_dir: Option<&Path>,
) -> Option<CoreLaunchSpec> {
    if let Some(project) = core_project_dir {
        return Some(CoreLaunchSpec {
            program: PathBuf::from("uv"),
            args: vec![
                "run".into(),
                "--project".into(),
                project.to_string_lossy().into_owned(),
                "lumi-core".into(),
            ],
            // The repository keeps `content/` beside `core/`, not inside it. **`parent()`
            // rather than joining `..`** — a path with `..` still in it doesn't match the
            // normalized path Tauri checks the asset scope against
            content_dir: project
                .parent()
                .map_or_else(|| project.join("../content"), |repo| repo.join("content")),
        });
    }
    let path = sidecar?;
    // PyInstaller's onedir layout: the Content Pack sits beside the executable it was
    // frozen into
    let content_dir = path
        .parent()
        .map_or_else(|| PathBuf::from("_internal/content"), |dir| dir.join("_internal/content"));
    Some(CoreLaunchSpec { program: path.to_path_buf(), args: Vec::new(), content_dir })
}

/// Looks for the bundled sidecar. `None` if it doesn't exist (= falls back to the dev path).
pub fn find_sidecar(exe_dir: &Path) -> Option<PathBuf> {
    sidecar_candidates(exe_dir).into_iter().find(|path| path.is_file())
}

/// Candidates ordered by search priority. **A pure function** (doesn't touch the filesystem).
pub fn sidecar_candidates(exe_dir: &Path) -> Vec<PathBuf> {
    SIDECAR_DIRS.iter().map(|dir| exe_dir.join(dir).join(SIDECAR_NAME)).collect()
}

/// Extracts the listening port from a single line of stdout. **A pure function.**
///
/// Only looks at lines from Core's structured log (one JSON object per line)
/// where `event == "core.ws.listening"`.
pub fn parse_listening_port(line: &str) -> Option<u16> {
    let value: serde_json::Value = serde_json::from_str(line).ok()?;
    if value.get("event").and_then(|v| v.as_str()) != Some("core.ws.listening") {
        return None;
    }
    let port = value.get("port")?.as_u64()?;
    // Port 0 means "let the OS choose" on the way in; on the way *out* it is never a real
    // listener. Publishing it would send `ws_client` reconnecting to `ws://127.0.0.1:0`
    // forever, which looks exactly like Core being slow to start. **Fail closed.**
    u16::try_from(port).ok().filter(|port| *port != 0)
}

/// The restart backoff delay. **A pure function.** Avoids hammering something that keeps failing at 100% CPU.
pub fn restart_delay(consecutive_failures: u32) -> Duration {
    let capped = consecutive_failures.min(4);
    Duration::from_millis(500 * 2_u64.pow(capped))
}

#[derive(Clone)]
pub struct CoreSupervisor {
    /// The Core that is running now. **Holding onto its stdin matters**: dropping stdin
    /// makes Core's own parent-watch see EOF and exit immediately.
    running: Arc<Mutex<Option<Child>>>,
    shutdown: Arc<std::sync::atomic::AtomicBool>,
    port_tx: watch::Sender<Option<u16>>,
    /// The job that takes Core (and its children) down together the moment
    /// Shell's process disappears. **The only layer that survives a force-kill**,
    /// so it's held with the same lifetime as the supervisor.
    job: Arc<Option<KillOnCloseJob>>,
}

impl CoreSupervisor {
    pub fn new() -> (Self, watch::Receiver<Option<u16>>) {
        let (port_tx, port_rx) = watch::channel(None);
        let job = KillOnCloseJob::create();
        if job.is_none() {
            // Never silently weaken. Logs that zombies may be left behind.
            log::warn!("core.job_object.unavailable Core process may remain on force-kill");
        }
        let supervisor = Self {
            running: Arc::new(Mutex::new(None)),
            shutdown: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            port_tx,
            job: Arc::new(job),
        };
        (supervisor, port_rx)
    }

    /// Starts the watch loop. Restarts Core if it goes down.
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
                // Whether it exited normally or abnormally, Core should be present as long as Shell is alive.
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

    /// The command that starts Core. **Nothing has happened yet** when this returns.
    fn build_command(spec: &CoreLaunchSpec, tokens: &CoreTokens) -> Command {
        let mut command = Command::new(&spec.program);
        command
            .args(&spec.args)
            // **The token is never put on the command line** (visible to other processes via `ps`).
            .env("LUMI_WS_TOKEN_SHELL", &tokens.shell)
            .env("LUMI_WS_TOKEN_STAGE", &tokens.stage)
            .env("LUMI_WS_TOKEN_PANEL", &tokens.panel)
            // Exits on its own once the parent disappears (the second layer of zombie prevention).
            .env("LUMI_PARENT_WATCH", "stdin")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);

        // **Never show a console window.** Core is a console-subsystem
        // executable (it can't be windowed, since the contract is that Shell
        // reads its structured stdout log). Without this flag, a black cmd
        // window appears alongside it, and if the user closes that window,
        // Core dies and the Supervisor correctly restarts it — **and it starts talking again** (observed 2026-08-15).
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);

        command
    }

    /// Puts the child into the kill-on-close job. **Done before anything else**, because
    /// from here on a force-kill of Shell has to be able to take Core down with it.
    fn assign_to_job(&self, child: &Child) {
        #[cfg(windows)]
        {
            let Some(job) = self.job.as_ref() else {
                return;
            };
            let assigned = match child.raw_handle() {
                Some(handle) => job.assign(handle),
                None => false,
            };
            if !assigned {
                log::warn!("core.job_object.assign_failed Core process may remain on force-kill");
            }
        }
        #[cfg(not(windows))]
        let _ = (self, child);
    }

    /// Streams Core's stdout to the log, **and publishes the port when it appears** — the
    /// one line Shell reads for its meaning rather than only to log it.
    fn spawn_stdout_reader(stdout: ChildStdout, port_tx: watch::Sender<Option<u16>>) {
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

    fn spawn_stderr_reader(stderr: ChildStderr) {
        tauri::async_runtime::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                log::warn!("core!: {line}");
            }
        });
    }

    async fn spawn_once(&self, spec: &CoreLaunchSpec, tokens: &CoreTokens) -> std::io::Result<()> {
        let mut child = Self::build_command(spec, tokens).spawn()?;

        // **Assigns it to the job immediately after launch.** If Shell dies
        // after this point, it can't be taken down together.
        self.assign_to_job(&child);

        let pid = child.id();
        // **Taken while the child is still local.** Once it is in `running`, reaching its
        // pipes would mean holding the mutex for as long as Core keeps logging.
        if let Some(stdout) = child.stdout.take() {
            Self::spawn_stdout_reader(stdout, self.port_tx.clone());
        }
        if let Some(stderr) = child.stderr.take() {
            Self::spawn_stderr_reader(stderr);
        }

        log::info!("core.spawned program={:?}", spec.program);

        // Puts the child into state before waiting (so it can be killed on shutdown).
        *self.running.lock().await = Some(child);
        self.wait_for_exit(pid).await
    }

    /// Waits for Core to exit. `wait` needs a mutable reference, which conflicts with
    /// holding it to `kill` on shutdown, so `try_wait` is polled at a short interval.
    ///
    /// **The lock is never held across the sleep.** `shutdown` has to take the child out
    /// of this same slot in order to kill it; keeping the guard while waiting would leave
    /// quitting blocked on a process that is still running — which is the zombie this
    /// file exists to prevent.
    async fn wait_for_exit(&self, pid: Option<u32>) -> std::io::Result<()> {
        loop {
            let exited = {
                let mut slot = self.running.lock().await;
                let Some(child) = slot.as_mut() else {
                    // The shutdown path took it and killed it.
                    log::info!("core.taken_over_by_shutdown pid={pid:?}");
                    return Ok(());
                };
                match child.try_wait()? {
                    Some(status) => {
                        *slot = None;
                        Some(status)
                    }
                    None => None,
                }
            };
            if let Some(status) = exited {
                log::warn!("core.exited pid={pid:?} status={status:?}");
                return Ok(());
            }
            tokio::time::sleep(WAIT_POLL_INTERVAL).await;
        }
    }

    /// Called when Shell exits. **Reliably kills it. Never leaves a zombie behind.**
    pub async fn shutdown(&self) {
        self.shutdown.store(true, std::sync::atomic::Ordering::SeqCst);
        let mut slot = self.running.lock().await;
        if let Some(mut child) = slot.take() {
            // Closes stdin first -> Core sees EOF and exits on its own (the normal path).
            drop(child.stdin.take());
            // Kills it outright if there's no response.
            let _ = child.kill().await;
            log::info!("core.killed");
        }
    }
}

#[cfg(test)]
mod tests {
    // Tests are allowed to panic, so unwrap is permitted here.
    #![allow(clippy::unwrap_used)]

    use super::*;

    #[test]
    fn uses_the_bundled_sidecar_when_there_is_no_source() {
        let spec = resolve_launch_spec(Some(Path::new("C:/app/lumi-core.exe")), None).unwrap();
        assert_eq!(spec.program, PathBuf::from("C:/app/lumi-core.exe"));
        assert!(spec.args.is_empty());
    }

    /// ★ Regression (2026-08-19): **the character never appeared.**
    ///
    /// The Content Pack directory was resolved by its own search, which found a stale
    /// `target/debug/core/_internal/content` from an earlier build — while Core, launched
    /// from source, read the repository's. The WebView was then allowed to read a directory
    /// that didn't hold the model Core had named, and the only sign was Tauri's
    /// `asset protocol not configured to allow the path`.
    ///
    /// **One decision, one source** (ADR-029).
    #[test]
    fn the_content_pack_belongs_to_the_core_that_runs() {
        let from_source = resolve_launch_spec(
            Some(Path::new("C:/app/lumi-core.exe")),
            Some(Path::new("C:/repo/core")),
        )
        .unwrap();
        // Launching from source ⇒ the repository's Content Pack, **never the built one**
        assert_eq!(from_source.content_dir, PathBuf::from("C:/repo").join("content"));

        let bundled = resolve_launch_spec(Some(Path::new("C:/app/lumi-core.exe")), None).unwrap();
        assert_eq!(bundled.content_dir, PathBuf::from("C:/app").join("_internal/content"));
    }

    #[test]
    fn the_content_pack_path_carries_no_parent_segments() {
        // **A path with `..` still in it doesn't match** the normalized path Tauri checks the
        // asset scope against — it would be allowed and then refused.
        let spec = resolve_launch_spec(None, Some(Path::new("C:/repo/core"))).unwrap();
        assert!(
            !spec.content_dir.components().any(|c| c.as_os_str() == ".."),
            "Path passed to scope contains .. segments: {}",
            spec.content_dir.display()
        );
    }

    #[test]
    fn development_prefers_the_source_over_a_stale_sidecar() {
        // **`tauri dev` also places resources under target/debug/core/.**
        // Preferring the sidecar would mean the previously built executable
        // runs even after editing Python.
        let spec = resolve_launch_spec(
            Some(Path::new("C:/app/lumi-core.exe")),
            Some(Path::new("C:/repo/core")),
        )
        .unwrap();
        assert_eq!(spec.program, PathBuf::from("uv"));
    }

    #[test]
    fn looks_in_the_resources_directory_first() {
        // In the distributable, it lives under `core/` (Tauri's resources).
        // Also checking next to the executable covers manual placement and a future externalBin.
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
        // Knowing it won't launch is better than it silently not launching.
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
        // Doesn't let through a value that isn't a valid port number
        assert_eq!(parse_listening_port(r#"{"event":"core.ws.listening","port":99999}"#), None);
        assert_eq!(parse_listening_port(r#"{"event":"core.ws.listening","port":0}"#), None);
    }

    #[test]
    fn backs_off_but_stays_bounded() {
        assert_eq!(restart_delay(0), Duration::from_millis(500));
        assert_eq!(restart_delay(1), Duration::from_millis(1000));
        assert_eq!(restart_delay(4), Duration::from_millis(8000));
        assert_eq!(restart_delay(100), Duration::from_millis(8000));
    }
}
