pub mod catalogue;
pub mod collectors;
#[cfg(feature = "desktop")]
pub mod commands;
pub mod confirmation;
pub mod exec;
#[cfg(all(feature = "desktop", target_os = "linux"))]
pub mod layer_shell;
pub mod overlay;
pub mod process;
use catalogue::{Catalogue, Store};
use collectors::{Collectors, Context};
use confirmation::Confirmations;
use exec::{ExecArgs, Plan};
use process::{Runner, SystemRunner};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeState {
    pub catalogue: Catalogue,
    #[serde(flatten)]
    pub context: Context,
}
#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ExecuteRequest {
    pub action_id: String,
    #[serde(default)]
    pub args: ExecArgs,
    pub confirmation_token: Option<String>,
    #[serde(default)]
    pub expected_app: bool,
}
#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PreviewRequest {
    pub action_id: String,
    #[serde(default)]
    pub args: ExecArgs,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Preview {
    pub action_id: String,
    pub text: String,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecuteResult {
    pub status: String,
    pub action_id: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub confirmation_token: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub expires_in_ms: Option<u64>,
}
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Proposal {
    pub proposal_id: String,
    pub toml: String,
    pub expires_in_ms: u64,
}
pub struct Engine {
    pub store: Mutex<Store>,
    pub collectors: Collectors,
    confirmations: Mutex<Confirmations>,
    proposals: Mutex<HashMap<String, Instant>>,
    text_busy: AtomicBool,
    active_timers: AtomicUsize,
    runner: Arc<dyn Runner>,
    codex: exec::codex::Codex,
    hermes: exec::hermes::Hermes,
}
struct BusyGuard<'a>(&'a AtomicBool);
impl Drop for BusyGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::SeqCst);
    }
}
impl Engine {
    /// Command runner shared with collectors and executors (overlay placement uses it).
    pub fn runner(&self) -> &dyn Runner {
        self.runner.as_ref()
    }
    pub fn open(path: PathBuf, home: PathBuf) -> Result<Self, String> {
        let runner: Arc<dyn Runner> = Arc::new(SystemRunner);
        Self::with_runner(path, home, runner)
    }
    pub fn with_runner(
        path: PathBuf,
        home: PathBuf,
        runner: Arc<dyn Runner>,
    ) -> Result<Self, String> {
        let codex = exec::codex::Codex::from_env(&home);
        let hermes = exec::hermes::Hermes::from_env(&home);
        Self::with_backends(path, home, runner, codex, hermes)
    }
    pub fn with_backends(
        path: PathBuf,
        home: PathBuf,
        runner: Arc<dyn Runner>,
        codex: exec::codex::Codex,
        hermes: exec::hermes::Hermes,
    ) -> Result<Self, String> {
        Ok(Self {
            store: Mutex::new(Store::open(path)?),
            collectors: Collectors::new(home.clone(), runner.clone()),
            confirmations: Mutex::new(Confirmations::default()),
            proposals: Mutex::new(HashMap::new()),
            text_busy: AtomicBool::new(false),
            active_timers: AtomicUsize::new(0),
            runner,
            codex,
            hermes,
        })
    }
    pub fn state(&self, input: &str) -> Result<NativeState, String> {
        if input.len() > 4096 {
            return Err("input exceeds 4096 bytes".into());
        }
        let catalogue = self.store.lock().unwrap().catalogue.clone();
        Ok(NativeState {
            catalogue,
            context: self.collectors.snapshot(input),
        })
    }
    pub fn reload(&self) -> Result<Catalogue, String> {
        let mut store = self.store.lock().unwrap();
        store.reload()?;
        self.collectors.invalidate();
        self.collectors.refresh_apps();
        Ok(store.catalogue.clone())
    }
    pub fn execute(&self, request: ExecuteRequest) -> Result<(ExecuteResult, Option<u64>), String> {
        self.execute_with_hook(request, |_| Ok(()))
    }
    /// Read-only calculator/conversion preview. This never enters execution,
    /// confirmation, command runners, collectors, timers or text generation.
    pub fn preview(&self, request: PreviewRequest) -> Result<Preview, String> {
        if !matches!(request.action_id.as_str(), "calc" | "convert") {
            return Err("only calc and convert support read-only preview".into());
        }
        let store = self.store.lock().unwrap();
        let action = store
            .catalogue
            .actions
            .iter()
            .find(|action| action.id == request.action_id)
            .ok_or("unknown action ID")?;
        Ok(Preview {
            action_id: action.id.clone(),
            text: exec::preview(action, &request.args)?,
        })
    }
    pub fn execute_with_hook(
        &self,
        request: ExecuteRequest,
        before: impl FnOnce(&Plan) -> Result<(), String>,
    ) -> Result<(ExecuteResult, Option<u64>), String> {
        if request.action_id.len() > 64
            || request
                .confirmation_token
                .as_ref()
                .is_some_and(|t| t.len() > 64)
        {
            return Err("invalid action ID or confirmation token".into());
        }
        // Keep generation stable until dispatch. A catalogue reload cannot replace
        // an action between validation, confirmation consumption and execution.
        let store = self.store.lock().unwrap();
        let action = store
            .catalogue
            .actions
            .iter()
            .find(|a| a.id == request.action_id)
            .ok_or("unknown action ID")?;
        if request.expected_app
            && (action.id != "open_app"
                || action.exec.kind != "app"
                || action.dangerous()
                || action.needs_text)
        {
            return Err(
                "application opening is unavailable in the current action catalogue".into(),
            );
        }
        let plan = exec::build(action, &request.args, &self.collectors)?;
        if action.dangerous() {
            use sha2::{Digest, Sha256};
            let plan_hash = format!("{:x}", Sha256::digest(format!("{plan:?}").as_bytes()));
            let mut confirmations = self.confirmations.lock().unwrap();
            if let Some(token) = &request.confirmation_token {
                confirmations.consume_plan(
                    token,
                    store.catalogue.generation,
                    &action.id,
                    &request.args,
                    &plan_hash,
                    Instant::now(),
                )?;
            } else {
                let token = confirmations.issue_plan(
                    store.catalogue.generation,
                    &action.id,
                    &request.args,
                    &plan_hash,
                    Instant::now(),
                )?;
                return Ok((
                    ExecuteResult {
                        status: "confirmationRequired".into(),
                        action_id: action.id.clone(),
                        message: "Review this action and its arguments, then confirm again".into(),
                        text: None,
                        confirmation_token: Some(token),
                        expires_in_ms: Some(15_000),
                    },
                    None,
                ));
            }
        } else if request.confirmation_token.is_some() {
            return Err("unexpected confirmation token".into());
        }
        let _busy = if matches!(&plan, Plan::Codex { .. } | Plan::Hermes { .. }) {
            Some(self.reserve_text()?)
        } else {
            None
        };
        before(&plan)?;
        if matches!(plan, Plan::Timer(_))
            && self
                .active_timers
                .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |n| {
                    (n < 64).then_some(n + 1)
                })
                .is_err()
        {
            return Err("too many active timers".into());
        }
        let text = exec::perform(&plan, self.runner.as_ref(), &self.codex, &self.hermes)?;
        let timer = if let Plan::Timer(seconds) = plan {
            Some(seconds)
        } else {
            None
        };
        Ok((
            ExecuteResult {
                status: "executed".into(),
                action_id: action.id.clone(),
                message: if timer.is_some() {
                    "Timer started"
                } else if matches!(plan, Plan::Hermes { .. }) {
                    "Sent to Hermes Desktop"
                } else {
                    "Action completed"
                }
                .into(),
                text,
                confirmation_token: None,
                expires_in_ms: None,
            },
            timer,
        ))
    }
    pub fn timer_finished(&self) {
        let _ = self
            .active_timers
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |n| n.checked_sub(1));
    }
    fn reserve_text(&self) -> Result<BusyGuard<'_>, String> {
        if self.text_busy.swap(true, Ordering::SeqCst) {
            return Err("a text request is already running".into());
        }
        Ok(BusyGuard(&self.text_busy))
    }
    pub fn propose(&self, inputs: Vec<String>) -> Result<Proposal, String> {
        if inputs.is_empty()
            || inputs.len() > 10
            || inputs
                .iter()
                .any(|s| s.is_empty() || s.chars().count() > 400)
        {
            return Err("teaching needs 1..10 inputs of at most 400 characters".into());
        }
        let _busy = self.reserve_text()?;
        let ids: Vec<String> = self
            .store
            .lock()
            .unwrap()
            .catalogue
            .actions
            .iter()
            .map(|a| a.id.clone())
            .collect();
        let output = self
            .codex
            .propose_with_ids(self.runner.as_ref(), &inputs, &ids)?;
        self.register_proposal(&output)
    }
    /// Validate a draft for review. Does not write a file or run any action.
    pub fn register_proposal(&self, text: &str) -> Result<Proposal, String> {
        let action = catalogue::parse_proposal(text)?;
        if self
            .store
            .lock()
            .unwrap()
            .catalogue
            .actions
            .iter()
            .any(|a| a.id == action.id)
        {
            return Err("proposed action ID already exists".into());
        }
        let mut proposals = self.proposals.lock().unwrap();
        let now = Instant::now();
        proposals.retain(|_, deadline| *deadline > now);
        if proposals.len() >= 4 {
            return Err("review or discard pending proposals before creating another".into());
        }
        let id = uuid::Uuid::new_v4().to_string();
        proposals.insert(id.clone(), now + Duration::from_secs(600));
        Ok(Proposal {
            proposal_id: id,
            toml: text.into(),
            expires_in_ms: 600_000,
        })
    }
    pub fn save(&self, id: &str, reviewed: &str) -> Result<Catalogue, String> {
        let mut proposals = self.proposals.lock().unwrap();
        if proposals
            .get(id)
            .is_none_or(|deadline| *deadline <= Instant::now())
        {
            return Err("proposal missing or expired; generate and review again".into());
        }
        let mut store = self.store.lock().unwrap();
        store.append(reviewed)?;
        proposals.remove(id);
        Ok(store.catalogue.clone())
    }
}
