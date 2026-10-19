#![cfg(target_os = "linux")]

use soft_launcher::process::{CommandSpec, Runner, SystemRunner};
use std::{
    fs,
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

const CHILD: &str = "import os,pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)";

fn wait_for(mut condition: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(3);
    while !condition() {
        assert!(
            Instant::now() < deadline,
            "lifecycle condition did not settle"
        );
        thread::sleep(Duration::from_millis(10));
    }
}

fn alive(pid: u32) -> bool {
    fs::read_to_string(format!("/proc/{pid}/stat"))
        .is_ok_and(|stat| !stat.rsplit_once(')').unwrap().1.starts_with(" Z"))
}

struct Fixture {
    parent: Child,
    pid_file: std::path::PathBuf,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = self.parent.kill();
        let _ = self.parent.wait();
        if let Ok(pid) = fs::read_to_string(&self.pid_file)
            .unwrap_or_default()
            .parse::<u32>()
        {
            // Only the synthetic child bearing this private fixture's exact path.
            if fs::read(format!("/proc/{pid}/cmdline")).is_ok_and(|bytes| {
                bytes
                    .split(|b| *b == 0)
                    .any(|arg| arg == self.pid_file.as_os_str().as_encoded_bytes())
            }) {
                let _ = Command::new("/usr/bin/kill")
                    .args(["-KILL", "--", &pid.to_string()])
                    .status();
            }
        }
    }
}

#[test]
#[ignore = "helper subprocess only"]
fn lifecycle_helper() {
    let folder =
        std::env::var_os("SOFT_LAUNCHER_LIFECYCLE_FIXTURE").expect("private fixture required");
    let folder = std::path::PathBuf::from(folder);
    let pid_file = folder.join("child.pid");
    let detached = std::env::var("SOFT_LAUNCHER_LIFECYCLE_DETACHED").unwrap() == "true";
    thread::spawn(move || {
        let mut spec = CommandSpec::new(
            "/usr/bin/python3",
            ["-c", CHILD, pid_file.to_str().unwrap()],
        );
        spec.detached = detached;
        spec.timeout_ms = 30_000;
        let _ = SystemRunner.run(&spec);
    });
    wait_for(|| folder.join("finish").exists());
    std::process::exit(0);
}

fn check_lifecycle(detached: bool, ending: &str) {
    let folder = tempfile::tempdir().unwrap();
    let parent = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "lifecycle_helper", "--ignored"])
        .env("SOFT_LAUNCHER_LIFECYCLE_FIXTURE", folder.path())
        .env("SOFT_LAUNCHER_LIFECYCLE_DETACHED", detached.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let mut fixture = Fixture {
        parent,
        pid_file: folder.path().join("child.pid"),
    };
    wait_for(|| {
        fs::read_to_string(&fixture.pid_file).is_ok_and(|text| text.parse::<u32>().is_ok())
    });
    let pid: u32 = fs::read_to_string(&fixture.pid_file)
        .unwrap()
        .parse()
        .unwrap();
    assert!(alive(pid));
    match ending {
        "normal" => fs::write(folder.path().join("finish"), "exit").unwrap(),
        "term" => {
            assert!(
                Command::new("/usr/bin/kill")
                    .args(["-TERM", "--", &fixture.parent.id().to_string()])
                    .status()
                    .unwrap()
                    .success()
            );
        }
        "kill" => fixture.parent.kill().unwrap(),
        _ => unreachable!(),
    }
    wait_for(|| fixture.parent.try_wait().unwrap().is_some());
    if detached {
        thread::sleep(Duration::from_millis(100));
        assert!(
            alive(pid),
            "detached user application must survive launcher exit"
        );
    } else {
        wait_for(|| !alive(pid));
    }
}

#[test]
fn detached_app_survives_normal_exit() {
    check_lifecycle(true, "normal");
}
#[test]
fn detached_app_survives_sigterm() {
    check_lifecycle(true, "term");
}
#[test]
fn detached_app_survives_main_death() {
    check_lifecycle(true, "kill");
}
#[test]
fn bounded_worker_exits_on_normal_exit() {
    check_lifecycle(false, "normal");
}
#[test]
fn bounded_worker_exits_on_sigterm() {
    check_lifecycle(false, "term");
}
#[test]
fn bounded_worker_exits_on_main_death() {
    check_lifecycle(false, "kill");
}
