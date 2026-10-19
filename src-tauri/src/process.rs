use std::{
    io::{Read, Write},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

#[derive(Clone, Debug, PartialEq)]
pub struct CommandSpec {
    pub program: String,
    pub args: Vec<String>,
    pub stdin: Option<Vec<u8>>,
    pub timeout_ms: u64,
    pub output_limit: usize,
    pub detached: bool,
}
impl CommandSpec {
    pub fn new(program: &str, args: impl IntoIterator<Item = impl Into<String>>) -> Self {
        Self {
            program: program.into(),
            args: args.into_iter().map(Into::into).collect(),
            stdin: None,
            timeout_ms: 2000,
            output_limit: 8192,
            detached: false,
        }
    }
}
pub trait Runner: Send + Sync {
    fn run(&self, spec: &CommandSpec) -> Result<Vec<u8>, String>;
}
pub struct SystemRunner;
impl Runner for SystemRunner {
    fn run(&self, spec: &CommandSpec) -> Result<Vec<u8>, String> {
        if spec.detached {
            let mut child = Command::new(&spec.program)
                .args(&spec.args)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .map_err(|_| format!("{} unavailable", spec.program))?;
            thread::spawn(move || {
                let _ = child.wait();
            });
            return Ok(Vec::new());
        }
        let mut command = Command::new(&spec.program);
        command
            .args(&spec.args)
            .stdin(if spec.stdin.is_some() {
                Stdio::piped()
            } else {
                Stdio::null()
            })
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        #[cfg(target_os = "linux")]
        {
            use std::os::unix::process::CommandExt;
            let parent = std::process::id() as libc::pid_t;
            // SAFETY: the child uses only async-signal-safe libc calls before
            // exec. It cannot inherit another command or a detached application.
            unsafe {
                command.pre_exec(move || {
                    if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                    // Close the race where the parent exited before prctl.
                    if libc::getppid() != parent {
                        libc::_exit(125);
                    }
                    Ok(())
                });
            }
        }
        let mut child = command
            .spawn()
            .map_err(|_| format!("{} unavailable", spec.program))?;
        let mut stdout = child.stdout.take().unwrap();
        let limit = spec.output_limit;
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        thread::spawn(move || {
            let mut out = Vec::new();
            // Continue draining output to avoid deadlock, but never retain beyond the cap.
            let mut buf = [0u8; 4096];
            while let Ok(n) = stdout.read(&mut buf) {
                if n == 0 {
                    break;
                }
                let keep = n.min(limit.saturating_sub(out.len()));
                out.extend_from_slice(&buf[..keep]);
            }
            let _ = tx.send(out);
        });
        if let Some(bytes) = spec.stdin.clone() {
            if let Some(mut stdin) = child.stdin.take() {
                thread::spawn(move || {
                    let _ = stdin.write_all(&bytes);
                });
            }
        }
        let deadline = Instant::now() + Duration::from_millis(spec.timeout_ms);
        loop {
            match child.try_wait() {
                Ok(Some(status)) => {
                    // Desktop launchers can leave stdout inherited by descendants. Do not
                    // block waiting for EOF from those descendants after the launcher exits.
                    if !status.success() {
                        return Err(format!("{} failed", spec.program));
                    }
                    return Ok(rx
                        .recv_timeout(Duration::from_millis(50))
                        .unwrap_or_default());
                }
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(5)),
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!("{} timed out", spec.program));
                }
            }
        }
    }
}
