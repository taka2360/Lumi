//! Windows Job Object — **reliably kills child processes together with the parent**.
//!
//! docs/interfaces/shell.md requirement 1: "Core reliably terminates when Shell
//! terminates (no zombies left behind)."
//!
//! ## Why this is needed (learned from observation)
//!
//! Two measures were originally thought to be enough.
//!
//! 1. Explicitly kill it during Shell's shutdown → **doesn't run if force-killed**
//! 2. Core watches for stdin EOF and exits on its own → **works standalone, but
//!    leaks if another process sits in between**
//!
//! In dev, Core is launched via `uv run lumi-core`, making it `Shell → uv.exe →
//! python.exe`. Killing Shell with `Stop-Process -Force` **left uv.exe orphaned**
//! (observed 2026-08-15).
//!
//! Placing it in a Job Object means **the moment the OS closes Shell's process
//! handle**, every process in the job terminates together. This also works under a force-kill.
//!
//! ## What this does not guarantee
//!
//! This is a Windows-only mechanism. Other OSes need a different approach
//! (process groups / `PDEATHSIG`). Phase 0 targets Windows, so only Windows is implemented here.

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

    /// A job that means "kill every process inside once this handle closes."
    pub struct KillOnCloseJob(HANDLE);

    // HANDLE is a raw pointer, but this only ever holds a single owning
    // instance, closed only by Drop. Safe to share across threads.
    unsafe impl Send for KillOnCloseJob {}
    unsafe impl Sync for KillOnCloseJob {}

    impl KillOnCloseJob {
        pub fn create() -> Option<Self> {
            // SAFETY: the arguments may be null. The return value's validity is checked immediately after.
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

        /// Adds a child process to the job. **`false` on failure.** Never
        /// swallowed — the caller logs it.
        pub fn assign(&self, process: HANDLE) -> bool {
            if process.is_null() {
                return false;
            }
            // SAFETY: both are valid handles. No side effect on failure.
            unsafe { AssignProcessToJobObject(self.0, process) != 0 }
        }
    }

    impl Drop for KillOnCloseJob {
        fn drop(&mut self) {
            // SAFETY: closes exactly once a handle obtained only via create.
            unsafe { CloseHandle(self.0) };
        }
    }
}

#[cfg(not(windows))]
mod imp {
    /// Does nothing outside Windows. **`create` returns `None`, so it's never
    /// mistaken for "this is working"** (the caller can log "unavailable").
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
