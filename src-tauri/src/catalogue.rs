use serde::{Deserialize, Serialize};
use std::{
    collections::HashSet,
    fs,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Exec {
    pub kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub arg: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub template: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Action {
    pub id: String,
    pub description: String,
    pub exec: Exec,
    #[serde(default, alias = "needs_text")]
    pub needs_text: bool,
    #[serde(default, alias = "requires_confirmation")]
    pub requires_confirmation: bool,
}
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Catalogue {
    pub generation: u64,
    pub actions: Vec<Action>,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Document {
    action: Vec<Action>,
}
pub fn builtin_arg(name: &str) -> Option<&'static str> {
    Some(match name {
        "calc" => "expr",
        "convert" | "web_search" | "define_word" | "note" | "search_notes" => "input",
        "copy_result" => "text",
        "paste_plain" => "clipboard",
        "rename_file" | "move_file" | "open_editor" => "file",
        "kill_process" => "process",
        "workspace" => "workspace",
        "timer" => "seconds",
        "ssh" => "host",
        "toggle_wifi" | "lock_screen" | "screenshot_region" | "media_play_pause" | "volume_up"
        | "volume_down" | "clef_status" => "none",
        _ => return None,
    })
}
impl Action {
    pub fn dangerous(&self) -> bool {
        matches!(self.exec.kind.as_str(), "shell" | "hermes")
            || matches!(
                self.exec.name.as_deref(),
                Some(
                    "rename_file"
                        | "move_file"
                        | "kill_process"
                        | "toggle_wifi"
                        | "ssh"
                        | "paste_plain"
                )
            )
            || self.requires_confirmation
    }
    pub fn validate(&mut self) -> Result<(), String> {
        if self.id.is_empty()
            || self.id.len() > 64
            || !self
                .id
                .bytes()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_')
        {
            return Err(
                "action IDs must use 1..64 lowercase letters, numbers or underscores".into(),
            );
        }
        if self.description.trim().is_empty() || self.description.chars().count() > 300 {
            return Err("action description must contain 1..300 characters".into());
        }
        let valid = match self.exec.kind.as_str() {
            "builtin" => {
                self.exec.name.as_deref().is_some_and(|n| {
                    builtin_arg(n)
                        .is_some_and(|arg| self.exec.arg.as_deref().unwrap_or("none") == arg)
                }) && self.exec.template.is_none()
            }
            "xdg-open" => {
                matches!(self.exec.arg.as_deref(), Some("file" | "url"))
                    && self.exec.name.is_none()
                    && self.exec.template.is_none()
            }
            "app" => {
                self.exec.arg.as_deref() == Some("app")
                    && self.exec.name.is_none()
                    && self.exec.template.is_none()
            }
            "shell" => {
                self.exec.arg.as_deref() == Some("input")
                    && self.exec.name.is_none()
                    && self.exec.template.is_none()
            }
            "hermes" => {
                self.exec.arg.as_deref() == Some("input")
                    && self.exec.name.is_none()
                    && self.exec.template.is_none()
            }
            "codex" => {
                matches!(
                    self.exec.template.as_deref(),
                    Some("summarize" | "translate")
                ) && self.exec.name.is_none()
                    && self.exec.arg.as_deref() == Some("clipboard")
            }
            "bonsai" => {
                matches!(
                    self.exec.template.as_deref(),
                    Some("reply" | "summarise" | "translate")
                ) && self.exec.name.is_none()
                    && self.exec.arg.as_deref() == Some("clipboard")
            }
            "none" => {
                self.exec.name.is_none() && self.exec.arg.is_none() && self.exec.template.is_none()
            }
            _ => false,
        };
        if !valid {
            return Err(format!("invalid executor for {}", self.id));
        }
        if self.exec.arg.as_ref().is_some_and(|s| {
            ![
                "file",
                "input",
                "url",
                "expr",
                "none",
                "clipboard",
                "app",
                "text",
                "seconds",
                "workspace",
                "process",
                "host",
            ]
            .contains(&s.as_str())
        }) {
            return Err("unknown argument source".into());
        }
        self.needs_text = matches!(self.exec.kind.as_str(), "codex" | "bonsai");
        self.requires_confirmation = self.dangerous();
        Ok(())
    }
}
pub fn parse(text: &str, generation: u64) -> Result<Catalogue, String> {
    if text.len() > 128 * 1024 {
        return Err("catalogue exceeds 128 KiB".into());
    }
    let mut doc: Document =
        toml::from_str(text).map_err(|e| format!("invalid catalogue TOML: {e}"))?;
    if doc.action.is_empty() || doc.action.len() > 128 {
        return Err("catalogue must contain 1..128 actions".into());
    }
    let mut ids = HashSet::new();
    for action in &mut doc.action {
        action.validate()?;
        if !ids.insert(action.id.clone()) {
            return Err(format!("duplicate action ID {}", action.id));
        }
    }
    Ok(Catalogue {
        generation,
        actions: doc.action,
    })
}
pub fn parse_proposal(text: &str) -> Result<Action, String> {
    let c = parse(text, 0)?;
    if c.actions.len() != 1 {
        return Err("review must contain exactly one action".into());
    }
    let action = c.actions.into_iter().next().unwrap();
    if action.id == "other" {
        return Err("other is reserved".into());
    }
    Ok(action)
}
pub struct Store {
    pub path: PathBuf,
    pub catalogue: Catalogue,
    pub source: String,
}
impl Store {
    pub fn open(path: PathBuf) -> Result<Self, String> {
        let source = read(&path)?;
        let catalogue = parse(&source, 1)?;
        Ok(Self {
            path,
            catalogue,
            source,
        })
    }
    pub fn reload(&mut self) -> Result<bool, String> {
        let source = read(&self.path)?;
        if source == self.source {
            return Ok(false);
        }
        let catalogue = parse(&source, self.catalogue.generation + 1)?;
        self.source = source;
        self.catalogue = catalogue;
        Ok(true)
    }
    pub fn append(&mut self, reviewed: &str) -> Result<(), String> {
        let action = parse_proposal(reviewed)?;
        // Reject external edits during review rather than overwriting them.
        if read(&self.path)? != self.source {
            return Err("catalogue changed; reload and review again".into());
        }
        if self.catalogue.actions.iter().any(|a| a.id == action.id) {
            return Err("action ID already exists".into());
        }
        let joined = format!("{}\n\n{}\n", self.source.trim_end(), reviewed.trim());
        let catalogue = parse(&joined, self.catalogue.generation + 1)?;
        let temp = self
            .path
            .with_extension(format!("{}.tmp", uuid::Uuid::new_v4()));
        use std::io::Write;
        let result = (|| {
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temp)
                .map_err(|e| e.to_string())?;
            file.write_all(joined.as_bytes())
                .map_err(|e| e.to_string())?;
            file.sync_all().map_err(|e| e.to_string())?;
            fs::rename(&temp, &self.path).map_err(|e| e.to_string())
        })();
        if result.is_err() {
            let _ = fs::remove_file(temp);
        }
        result?;
        self.source = joined;
        self.catalogue = catalogue;
        Ok(())
    }
}
fn read(path: &Path) -> Result<String, String> {
    if fs::metadata(path).map_err(|e| e.to_string())?.len() > 128 * 1024 {
        return Err("catalogue exceeds 128 KiB".into());
    }
    fs::read_to_string(path).map_err(|e| format!("cannot read catalogue: {e}"))
}
