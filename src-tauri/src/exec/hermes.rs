use crate::process::{CommandSpec, Runner};
use std::path::{Path, PathBuf};

#[derive(Clone)]
pub struct Hermes {
    adapter: PathBuf,
}
impl Hermes {
    pub fn new(adapter: PathBuf) -> Self {
        Self { adapter }
    }
    pub fn from_env(home: &Path) -> Self {
        Self::new(
            std::env::var_os("SOFT_LAUNCHER_HERMES_ADAPTER")
                .map(PathBuf::from)
                .unwrap_or_else(|| home.join(".local/lib/soft-launcher/hermes-handoff.py")),
        )
    }
    pub fn send(&self, runner: &dyn Runner, text: &str) -> Result<(), String> {
        if text.trim().is_empty() || text.len() > 4096 || text.contains('\0') {
            return Err("Hermes needs typed text of 1..4096 bytes without NUL".into());
        }
        let adapter = self.adapter.to_str().ok_or("invalid Hermes adapter path")?;
        // Older adapters accept stdin even with unknown flags and would open
        // a terminal. Probe with no payload before giving one any typed text.
        let mut probe = CommandSpec::new("python3", [adapter, "--capabilities"]);
        probe.timeout_ms = 1_000;
        probe.output_limit = 4097;
        let capability = runner
            .run(&probe)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok());
        if !capability.as_ref().is_some_and(|value| {
            value.get("version").and_then(|v| v.as_u64()) == Some(1)
                && value.get("destination").and_then(|v| v.as_str()) == Some("hermes-desktop")
        }) {
            return Err("Hermes Desktop bridge is unavailable or unsupported".into());
        }
        let mut spec = CommandSpec::new("python3", [adapter]);
        spec.stdin = Some(
            serde_json::to_vec(&serde_json::json!({"text":text}))
                .map_err(|_| "invalid Hermes request")?,
        );
        spec.timeout_ms = 49_000;
        spec.output_limit = 4097;
        let bytes = runner.run(&spec).map_err(
            |_| "Hermes Desktop delivery is unconfirmed; check Desktop before sending again",
        )?;
        let value: serde_json::Value = serde_json::from_slice(&bytes).map_err(
            |_| "Hermes Desktop delivery is unconfirmed; check Desktop before sending again",
        )?;
        if value.get("status").and_then(|v| v.as_str()) != Some("accepted") {
            return Err(match value.get("error").and_then(|v| v.as_str()) {
                Some("desktop") => "Hermes Desktop unavailable".into(),
                Some("unsupported") => {
                    "Hermes Desktop bridge is unsupported; update the local Desktop bridge".into()
                }
                Some("rejected") => "Hermes Desktop rejected the handoff".into(),
                _ => {
                    let correlation = value
                        .get("requestId")
                        .and_then(|v| v.as_str())
                        .and_then(|s| uuid::Uuid::parse_str(s).ok())
                        .map(|id| format!(" (request {id})"))
                        .unwrap_or_default();
                    format!(
                        "Hermes Desktop delivery is unconfirmed{correlation}; check Desktop before sending again"
                    )
                }
            });
        }
        if value.get("version").and_then(|v| v.as_u64()) != Some(1)
            || value.get("destination").and_then(|v| v.as_str()) != Some("hermes-desktop")
            || value
                .get("requestId")
                .and_then(|v| v.as_str())
                .and_then(|s| uuid::Uuid::parse_str(s).ok())
                .is_none()
        {
            return Err(
                "Hermes Desktop delivery is unconfirmed; check Desktop before sending again".into(),
            );
        }
        Ok(())
    }
}
