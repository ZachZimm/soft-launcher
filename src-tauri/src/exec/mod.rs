pub mod builtin;
pub mod codex;
pub mod hermes;
pub mod shell;
pub mod xdg;
use crate::{
    catalogue::Action,
    collectors::Collectors,
    process::{CommandSpec, Runner},
};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ExecArgs {
    pub input: Option<String>,
    pub file: Option<String>,
    pub app_id: Option<String>,
    pub url: Option<String>,
    pub expr: Option<String>,
    pub text: Option<String>,
    pub destination: Option<String>,
    pub workspace: Option<u32>,
    pub process_name: Option<String>,
    pub host: Option<String>,
    pub seconds: Option<u64>,
    pub value: Option<f64>,
    pub from_unit: Option<String>,
    pub to_unit: Option<String>,
}
impl ExecArgs {
    pub fn validate(&self) -> Result<(), String> {
        if serde_json::to_vec(self)
            .map_err(|_| "invalid arguments")?
            .len()
            > 16 * 1024
        {
            return Err("arguments exceed 16 KiB".into());
        }
        for s in [
            &self.input,
            &self.file,
            &self.app_id,
            &self.url,
            &self.expr,
            &self.text,
            &self.destination,
            &self.process_name,
            &self.host,
            &self.from_unit,
            &self.to_unit,
        ]
        .into_iter()
        .flatten()
        {
            if s.len() > 4096 || s.contains('\0') {
                return Err("argument is oversized or contains NUL".into());
            }
        }
        if self.value.is_some_and(|n| !n.is_finite()) {
            return Err("value must be finite".into());
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq)]
pub enum Plan {
    Command(CommandSpec),
    Commands(Vec<CommandSpec>),
    Text(String),
    Timer(u64),
    Rename {
        source: PathBuf,
        target: PathBuf,
    },
    Note {
        path: PathBuf,
        text: String,
    },
    Codex {
        template: String,
        text: String,
    },
    Hermes {
        text: String,
    },
    Noop,
    ToggleWifi {
        read: CommandSpec,
        enable: CommandSpec,
        disable: CommandSpec,
    },
}
fn required<'a>(value: &'a Option<String>, label: &str) -> Result<&'a str, String> {
    value
        .as_deref()
        .filter(|s| !s.trim().is_empty())
        .ok_or_else(|| format!("missing {label}"))
}
fn copy(text: &str) -> CommandSpec {
    let mut c = CommandSpec::new("wl-copy", ["--type", "text/plain;charset=utf-8"]);
    c.stdin = Some(text.as_bytes().to_vec());
    c
}
fn search(prefix: &str, input: &str) -> Plan {
    let mut url = url::Url::parse(prefix).unwrap();
    url.query_pairs_mut().append_pair("q", input);
    Plan::Command(xdg::open(url.as_str()))
}
fn evaluate_text_builtin(name: &str, args: &ExecArgs) -> Result<String, String> {
    match name {
        "calc" => builtin::calc(required(&args.expr, "expression")?),
        "convert" => builtin::convert(
            args.value.ok_or("missing value")?,
            required(&args.from_unit, "source unit")?,
            required(&args.to_unit, "destination unit")?,
        ),
        _ => Err("only calc and convert support read-only preview".into()),
    }
}
/// Evaluate only the two bounded, side-effect-free builtins. A catalogue entry
/// cannot rebind one of these IDs to another executor and gain preview access.
pub fn preview(action: &Action, args: &ExecArgs) -> Result<String, String> {
    if !matches!(action.id.as_str(), "calc" | "convert")
        || action.exec.kind != "builtin"
        || action.exec.name.as_deref() != Some(action.id.as_str())
    {
        return Err("only calc and convert support read-only preview".into());
    }
    args.validate()?;
    evaluate_text_builtin(&action.id, args)
}
pub fn build(action: &Action, args: &ExecArgs, collectors: &Collectors) -> Result<Plan, String> {
    args.validate()?;
    let command = |p: &str, a: Vec<String>| Ok(Plan::Command(CommandSpec::new(p, a)));
    match action.exec.kind.as_str() {
        "none" => Ok(Plan::Noop),
        "shell" => Ok(Plan::Command(shell::plan(required(
            &args.input,
            "shell input",
        )?)?)),
        "app" => {
            let app = collectors
                .app(required(&args.app_id, "app ID")?)
                .ok_or("unknown app ID")?;
            app.validate_installed()?;
            let mut spec = if app.terminal {
                let mut a = vec!["-e".into()];
                a.extend(app.argv.clone());
                CommandSpec::new("foot", a)
            } else {
                CommandSpec::new(&app.argv[0], app.argv[1..].to_vec())
            };
            spec.detached = true;
            Ok(Plan::Command(spec))
        }
        "xdg-open" => {
            let target = if action.exec.arg.as_deref() == Some("file") {
                xdg::file(required(&args.file, "file")?, collectors.home())?
                    .to_string_lossy()
                    .into_owned()
            } else {
                xdg::url(required(&args.url, "URL")?)?
            };
            Ok(Plan::Command(xdg::open(&target)))
        }
        "hermes" => Ok(Plan::Hermes {
            text: required(&args.input, "typed Hermes request")?.to_string(),
        }),
        "codex" | "bonsai" => {
            // Clipboard actions read native text only after action choice, with
            // the same original-window privacy gate used by context and paste.
            let text = collectors.clipboard_text()?;
            if text.len() > 8192 {
                return Err("clipboard text exceeds the 8 KiB text request limit".into());
            }
            Ok(Plan::Codex {
                template: action.exec.template.clone().ok_or("missing template")?,
                text,
            })
        }
        "builtin" => match action.exec.name.as_deref().ok_or("missing builtin")? {
            name @ ("calc" | "convert") => Ok(Plan::Text(evaluate_text_builtin(name, args)?)),
            "web_search" => Ok(search(
                "https://www.google.com/search",
                required(&args.input, "search input")?,
            )),
            "define_word" => Ok(search(
                "https://www.google.com/search",
                &format!("define {}", required(&args.input, "word")?),
            )),
            "copy_result" => Ok(Plan::Command(copy(required(&args.text, "text")?))),
            "paste_plain" => {
                let text = collectors.clipboard_text()?;
                Ok(Plan::Commands(vec![
                    copy(&text),
                    CommandSpec::new("wtype", ["-M", "shift", "-k", "Insert", "-m", "shift"]),
                ]))
            }
            "rename_file" | "move_file" => {
                let source = xdg::file(required(&args.file, "file")?, collectors.home())?;
                if source == collectors.home() {
                    return Err("cannot rename or move the home directory".into());
                }
                let dest = required(&args.destination, "destination")?;
                let target = if action.exec.name.as_deref() == Some("rename_file") {
                    if dest.contains('/') || matches!(dest, "." | "..") {
                        return Err("new name must be a single filename".into());
                    }
                    source.with_file_name(dest)
                } else {
                    let dir = xdg::file(dest, collectors.home())?;
                    if !dir.is_dir() {
                        return Err("destination must be a directory".into());
                    }
                    dir.join(source.file_name().ok_or("file has no basename")?)
                };
                if target.exists() {
                    return Err("destination already exists".into());
                }
                Ok(Plan::Rename { source, target })
            }
            "kill_process" => {
                let name = required(&args.process_name, "process name")?;
                if name.len() > 128
                    || name.starts_with('-')
                    || !name
                        .bytes()
                        .all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
                {
                    return Err("invalid exact process name".into());
                }
                command(
                    "pkill",
                    vec![
                        "-TERM".into(),
                        "-x".into(),
                        "--".into(),
                        name.replace('.', "\\."),
                    ],
                )
            }
            "toggle_wifi" => Ok(Plan::ToggleWifi {
                read: CommandSpec::new("nmcli", ["radio", "wifi"]),
                enable: CommandSpec::new("nmcli", ["radio", "wifi", "on"]),
                disable: CommandSpec::new("nmcli", ["radio", "wifi", "off"]),
            }),
            "lock_screen" => command("loginctl", vec!["lock-session".into()]),
            "screenshot_region" => {
                let mut c = CommandSpec::new("hyprshot", ["-m", "region"]);
                c.detached = true;
                Ok(Plan::Command(c))
            }
            "workspace" => {
                let n = args
                    .workspace
                    .filter(|n| (1..=20).contains(n))
                    .ok_or("workspace must be 1..20")?;
                command(
                    "hyprctl",
                    vec![
                        "dispatch".into(),
                        format!("hl.dsp.focus({{ workspace = {n} }})"),
                    ],
                )
            }
            "open_editor" => {
                let file = xdg::file(required(&args.file, "project path")?, collectors.home())?;
                let mut c = CommandSpec::new("code", ["--", &file.to_string_lossy()]);
                c.detached = true;
                Ok(Plan::Command(c))
            }
            "timer" => Ok(Plan::Timer(
                args.seconds
                    .filter(|s| (1..=86400).contains(s))
                    .ok_or("timer must be 1..86400 seconds")?,
            )),
            "note" => Ok(Plan::Note {
                path: collectors
                    .home()
                    .join(".local/share/soft-launcher/inbox.md"),
                text: required(&args.text.clone().or(args.input.clone()), "note text")?.to_string(),
            }),
            "search_notes" => {
                let folder = collectors.home().join("notes");
                if !folder.is_dir() {
                    return Err("~/notes does not exist".into());
                }
                command(
                    "rg",
                    vec![
                        "--line-number".into(),
                        "--fixed-strings".into(),
                        "--max-count".into(),
                        "10".into(),
                        "--".into(),
                        required(&args.input, "notes search")?.into(),
                        folder.to_string_lossy().into_owned(),
                    ],
                )
            }
            "media_play_pause" => command("playerctl", vec!["play-pause".into()]),
            "volume_up" => command(
                "wpctl",
                vec![
                    "set-volume".into(),
                    "-l".into(),
                    "1.0".into(),
                    "@DEFAULT_AUDIO_SINK@".into(),
                    "5%+".into(),
                ],
            ),
            "volume_down" => command(
                "wpctl",
                vec![
                    "set-volume".into(),
                    "@DEFAULT_AUDIO_SINK@".into(),
                    "5%-".into(),
                ],
            ),
            "ssh" => {
                let host = required(&args.host, "host")?;
                if host.len() > 253
                    || host.starts_with('-')
                    || !host
                        .bytes()
                        .all(|c| c.is_ascii_alphanumeric() || b"._-@".contains(&c))
                    || host.matches('@').count() > 1
                {
                    return Err("invalid SSH host".into());
                }
                let mut c = CommandSpec::new("foot", ["-e", "ssh", "--", host]);
                c.detached = true;
                Ok(Plan::Command(c))
            }
            "clef_status" => command(
                "curl",
                vec![
                    "--fail".into(),
                    "--silent".into(),
                    "--max-time".into(),
                    "2".into(),
                    format!(
                        "{}/api/tags",
                        std::env::var("SOFT_LAUNCHER_CLEF_URL")
                            .unwrap_or_else(|_| "http://127.0.0.1:11434".into())
                            .trim_end_matches('/')
                    ),
                ],
            ),
            _ => Err("unknown builtin".into()),
        },
        _ => Err("unknown executor".into()),
    }
}
pub fn perform(
    plan: &Plan,
    runner: &dyn Runner,
    codex: &codex::Codex,
    hermes: &hermes::Hermes,
) -> Result<Option<String>, String> {
    match plan {
        Plan::Noop | Plan::Timer(_) => Ok(None),
        Plan::ToggleWifi {
            read,
            enable,
            disable,
        } => {
            let current = runner.run(read)?;
            match String::from_utf8_lossy(&current).trim() {
                "enabled" => {
                    runner.run(disable)?;
                }
                "disabled" => {
                    runner.run(enable)?;
                }
                _ => return Err("cannot determine current Wi-Fi state".into()),
            }
            Ok(None)
        }
        Plan::Text(text) => Ok(Some(text.clone())),
        Plan::Command(command) => {
            let bytes = runner.run(command)?;
            if command.program == "hyprctl" && command.args.first().is_some_and(|a| a == "dispatch")
            {
                crate::collectors::hyprland::dispatch_ok(&bytes)?;
                return Ok(None);
            }
            Ok((!bytes.is_empty())
                .then(|| String::from_utf8_lossy(&bytes).chars().take(4096).collect()))
        }
        Plan::Commands(commands) => {
            for command in commands {
                runner.run(command)?;
            }
            Ok(None)
        }
        Plan::Rename { source, target } => {
            // renameat2 NOREPLACE prevents a race overwriting an existing target.
            #[cfg(target_os = "linux")]
            {
                use std::ffi::CString;
                use std::os::unix::ffi::OsStrExt;
                let source =
                    CString::new(source.as_os_str().as_bytes()).map_err(|_| "invalid path")?;
                let target =
                    CString::new(target.as_os_str().as_bytes()).map_err(|_| "invalid path")?;
                unsafe extern "C" {
                    fn renameat2(
                        oldfd: i32,
                        old: *const i8,
                        newfd: i32,
                        new: *const i8,
                        flags: u32,
                    ) -> i32;
                }
                if unsafe { renameat2(-100, source.as_ptr(), -100, target.as_ptr(), 1) } != 0 {
                    return Err("move failed, destination may exist or filesystem differs".into());
                }
                Ok(None)
            }
            #[cfg(not(target_os = "linux"))]
            {
                Err("moves require Linux".into())
            }
        }
        Plan::Note { path, text } => {
            use std::io::Write;
            std::fs::create_dir_all(path.parent().ok_or("invalid inbox path")?)
                .map_err(|_| "cannot create inbox directory")?;
            let mut f = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(path)
                .map_err(|_| "cannot open inbox")?;
            writeln!(
                f,
                "\n- {} {}",
                chrono::Local::now().to_rfc3339(),
                text.replace('\n', "\n  ")
            )
            .map_err(|_| "cannot write inbox")?;
            Ok(None)
        }
        Plan::Codex { template, text } => codex.text(runner, template, text).map(Some),
        Plan::Hermes { text } => hermes.send(runner, text).map(|_| None),
    }
}
