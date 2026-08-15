//! Windows Job Object — **子プロセスを親と一緒に確実に殺す**。
//!
//! docs/interfaces/shell.md の要件1「Shell 終了時に Core も確実に終了する（ゾンビを残さない）」。
//!
//! ## なぜ必要か（実測でわかったこと）
//!
//! 当初は2つの手段だけで足りると考えていた。
//!
//! 1. Shell の終了処理で明示的に kill する → **強制終了されたら走らない**
//! 2. Core 側で stdin の EOF を見て自分で終わる → **単体では動くが、間に別のプロセスが挟まると漏れる**
//!
//! 開発時の Core は `uv run lumi-core` で起動するため、`Shell → uv.exe → python.exe` になる。
//! Shell を `Stop-Process -Force` で殺すと **uv.exe が孤児として残った**（2026-08-15 実測）。
//!
//! Job Object に入れておくと、**Shell のプロセスハンドルが OS に閉じられた時点で**
//! ジョブ内のプロセスがまとめて終了する。強制終了でも効く。
//!
//! ## 保証しないこと
//!
//! Windows 専用の仕組みである。他 OS では別の手段（プロセスグループ / `PDEATHSIG`）が要る。
//! Phase 0 の対象は Windows なので、ここでは Windows だけを実装する。

#[cfg(windows)]
mod imp {
    use std::ffi::c_void;
    use std::mem::{size_of, zeroed};

    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    /// 「このハンドルが閉じたら中のプロセスを全部殺す」ジョブ。
    pub struct KillOnCloseJob(HANDLE);

    // HANDLE は生ポインタだが、ここでは所有権を持つ1本だけを保持し、
    // 閉じるのは Drop のみ。スレッド間で共有しても安全。
    unsafe impl Send for KillOnCloseJob {}
    unsafe impl Sync for KillOnCloseJob {}

    impl KillOnCloseJob {
        pub fn create() -> Option<Self> {
            // SAFETY: 引数は null 可。戻り値の妥当性を直後に確認している。
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() {
                    return None;
                }
                let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
                limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let ok = SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    std::ptr::addr_of!(limits).cast::<c_void>(),
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                if ok == 0 {
                    CloseHandle(handle);
                    return None;
                }
                Some(Self(handle))
            }
        }

        /// 子プロセスをジョブに入れる。**失敗したら false**。握りつぶさず呼び出し側でログに残す。
        pub fn assign(&self, process: HANDLE) -> bool {
            if process.is_null() {
                return false;
            }
            // SAFETY: 両方とも有効なハンドル。失敗しても副作用は無い。
            unsafe { AssignProcessToJobObject(self.0, process) != 0 }
        }
    }

    impl Drop for KillOnCloseJob {
        fn drop(&mut self) {
            // SAFETY: create でのみ得たハンドルを一度だけ閉じる。
            unsafe { CloseHandle(self.0) };
        }
    }
}

#[cfg(not(windows))]
mod imp {
    /// Windows 以外では何もしない。**「効いている」と誤解させないため、
    /// `create` は `None` を返す**（呼び出し側が「使えない」とログに書ける）。
    pub struct KillOnCloseJob;

    impl KillOnCloseJob {
        pub fn create() -> Option<Self> {
            None
        }
        pub fn assign(&self, _process: *mut std::ffi::c_void) -> bool {
            false
        }
    }
}

pub use imp::KillOnCloseJob;
