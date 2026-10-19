//! Startup serialization only. This is not an application-lifetime lock.
use std::os::unix::{fs::OpenOptionsExt, io::AsRawFd};
use std::{
    fs::{File, OpenOptions},
    io,
    path::Path,
};

pub struct StartupGuard(Option<File>);

fn managed_hidden(hidden: bool, cgroup: &str) -> bool {
    hidden
        && cgroup.lines().any(|line| {
            line.splitn(3, ':')
                .nth(2)
                .is_some_and(|path| path.ends_with("/soft-launcher.service"))
        })
}

fn acquire_at(path: &Path, managed: bool) -> io::Result<StartupGuard> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)?;
    // SAFETY: flock acts on this live, owned descriptor only.
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_SH | libc::LOCK_NB) } == 0 {
        return Ok(StartupGuard(Some(file)));
    }
    let error = io::Error::last_os_error();
    if error.kind() == io::ErrorKind::WouldBlock && managed {
        // The updater deliberately starts the managed hidden instance while
        // holding its exclusive lock. Session hotkeys cannot take this path.
        return Ok(StartupGuard(None));
    }
    Err(error)
}

pub fn acquire(hidden: bool) -> io::Result<StartupGuard> {
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            std::path::PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() }))
        });
    acquire_at(
        &runtime.join("soft-launcher-update.lock"),
        managed_hidden(hidden, &std::fs::read_to_string("/proc/self/cgroup")?),
    )
}

impl Drop for StartupGuard {
    fn drop(&mut self) {
        if let Some(file) = self.0.take() {
            // SAFETY: the descriptor belongs to this guard, never another process.
            unsafe {
                libc::flock(file.as_raw_fd(), libc::LOCK_UN);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn startup_lock_releases_before_updates_and_denies_concurrent_start() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lock");
        let startup = acquire_at(&path, false).unwrap();
        let update = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&path)
            .unwrap();
        assert_ne!(
            unsafe { libc::flock(update.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) },
            0
        );
        drop(startup);
        assert_eq!(
            unsafe { libc::flock(update.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) },
            0
        );
        assert!(
            matches!(acquire_at(&path, false), Err(error) if error.kind() == io::ErrorKind::WouldBlock)
        );
        assert!(acquire_at(&path, true).is_ok());
        drop(update);
        assert!(acquire_at(&path, false).is_ok());
    }
    #[test]
    fn only_hidden_exact_managed_service_can_bypass_update() {
        let unit =
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/soft-launcher.service\n";
        assert!(managed_hidden(true, unit));
        assert!(!managed_hidden(false, unit));
        assert!(!managed_hidden(
            true,
            "0::/user.slice/user-1000.slice/session-c1.scope\n"
        ));
        assert!(!managed_hidden(
            true,
            "0::/app.slice/other-soft-launcher.service\n"
        ));
        assert!(!managed_hidden(
            true,
            "0::/app.slice/soft-launcher.service/child\n"
        ));
    }
}
