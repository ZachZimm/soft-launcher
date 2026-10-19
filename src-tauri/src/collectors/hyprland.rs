use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Window {
    pub class: String,
    pub title: String,
    #[serde(default, skip_serializing)]
    pub address: Option<String>,
}
pub fn private(window: &Window) -> bool {
    let class = window.class.to_lowercase();
    let title = window.title.to_lowercase();
    [
        "keepass",
        "1password",
        "bitwarden",
        "password",
        "bank",
        "vault",
    ]
    .iter()
    .any(|s| class.contains(s) || title.contains(s))
}
pub fn parse(text: &[u8]) -> Option<Window> {
    serde_json::from_slice(text).ok()
}
pub fn redact(window: &mut Window) {
    if private(window) {
        window.title = format!("private:{:x}", Sha256::digest(window.title.as_bytes()));
    }
    window.class = window.class.chars().take(80).collect();
    window.title = window.title.chars().take(120).collect();
}

/// Hyprland 0.56 Lua dispatcher. An absent selector must never focus the active
/// window, so resolve the validated original address and fail before dispatch.
pub fn focus_command(address: &str) -> Result<crate::process::CommandSpec, String> {
    if address.len() <= 2
        || address.len() > 18
        || !address.starts_with("0x")
        || !address[2..].bytes().all(|c| c.is_ascii_hexdigit())
    {
        return Err("original window address invalid".into());
    }
    let lua = format!(
        "function() local w = hl.get_window(\"address:{address}\"); if not w then error(\"original window unavailable\") end; hl.dispatch(hl.dsp.focus({{ window = w }})) end"
    );
    Ok(crate::process::CommandSpec::new(
        "hyprctl",
        ["dispatch", &lua],
    ))
}
pub fn dispatch_ok(bytes: &[u8]) -> Result<(), String> {
    if bytes == b"ok" || String::from_utf8_lossy(bytes).trim() == "ok" {
        Ok(())
    } else {
        Err("Hyprland rejected the dispatcher".into())
    }
}
