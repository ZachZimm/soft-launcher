pub mod apps;
pub mod clipboard;
pub mod files;
pub mod hyprland;
pub mod recent;
use crate::process::{CommandSpec, Runner};
use serde::Serialize;
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Context {
    pub window: Option<hyprland::Window>,
    pub clipboard: clipboard::Clipboard,
    pub recent: Vec<files::FileCandidate>,
    pub candidates: Vec<files::FileCandidate>,
    pub apps: Vec<apps::App>,
    pub time: String,
    pub warnings: Vec<String>,
    pub files_indexed: usize,
}
pub struct Collectors {
    pub home: PathBuf,
    apps: Mutex<Vec<apps::App>>,
    paths: Arc<Mutex<Vec<PathBuf>>>,
    refreshing: Arc<AtomicBool>,
    last_scan: Mutex<Instant>,
    cache: Mutex<Option<(Instant, Context)>>,
    runner: Arc<dyn Runner>,
    foreground: Mutex<(Option<hyprland::Window>, bool)>,
    overlay_active: AtomicBool,
}
impl Collectors {
    pub fn new(home: PathBuf, runner: Arc<dyn Runner>) -> Self {
        Self::with_apps(home.clone(), runner, apps::collect(&home))
    }
    pub fn with_apps(home: PathBuf, runner: Arc<dyn Runner>, apps: Vec<apps::App>) -> Self {
        let result = Self {
            apps: Mutex::new(apps),
            home,
            paths: Arc::new(Mutex::new(Vec::new())),
            refreshing: Arc::new(AtomicBool::new(false)),
            last_scan: Mutex::new(Instant::now()),
            cache: Mutex::new(None),
            runner,
            foreground: Mutex::new((None, true)),
            overlay_active: AtomicBool::new(false),
        };
        result.refresh_files();
        result
    }
    pub fn refresh_files(&self) {
        if self.refreshing.swap(true, Ordering::SeqCst) {
            return;
        }
        *self.last_scan.lock().unwrap() = Instant::now();
        let home = self.home.clone();
        let paths = self.paths.clone();
        let busy = self.refreshing.clone();
        std::thread::spawn(move || {
            *paths.lock().unwrap() = files::scan(&home);
            busy.store(false, Ordering::SeqCst);
        });
    }
    pub fn capture_foreground(&self) {
        let mut command = CommandSpec::new("hyprctl", ["activewindow", "-j"]);
        command.timeout_ms = 200;
        if let Some(mut window) = self
            .runner
            .run(&command)
            .ok()
            .and_then(|b| hyprland::parse(&b))
        {
            let class = window.class.to_lowercase();
            if class.contains("soft-launcher") || class.contains("soft_launcher") {
                return;
            }
            let private = hyprland::private(&window);
            hyprland::redact(&mut window);
            *self.foreground.lock().unwrap() = (Some(window), private);
        } else {
            *self.foreground.lock().unwrap() = (None, true);
        }
    }
    pub fn clipboard_text(&self) -> Result<String, String> {
        if self.snapshot("").clipboard.kind == "private" {
            return Err("clipboard belongs to a private or unknown window".into());
        }
        let mut command = CommandSpec::new("wl-paste", ["--no-newline", "--type", "text"]);
        command.output_limit = 64 * 1024 + 1;
        command.timeout_ms = 500;
        let bytes = self.runner.run(&command)?;
        if bytes.is_empty() || bytes.len() > 64 * 1024 {
            return Err("clipboard text is empty or exceeds 64 KiB".into());
        }
        String::from_utf8(bytes).map_err(|_| "clipboard text is not UTF-8".into())
    }
    pub fn focus_original(&self) -> Result<(), String> {
        let address = self
            .foreground
            .lock()
            .unwrap()
            .0
            .as_ref()
            .and_then(|w| w.address.clone())
            .ok_or("original window unavailable")?;
        let response = self.runner.run(&hyprland::focus_command(&address)?)?;
        hyprland::dispatch_ok(&response)?;
        Ok(())
    }
    /// Focus the original window unless it already has focus. Hyprland's focus
    /// dispatcher warps the pointer to the window, so skip it when the compositor
    /// has already handed focus back (it does when an exclusive layer unmaps).
    pub fn restore_original(&self) -> Result<(), String> {
        let wanted = self
            .foreground
            .lock()
            .unwrap()
            .0
            .as_ref()
            .and_then(|w| w.address.clone())
            .ok_or("original window unavailable")?;
        let mut command = CommandSpec::new("hyprctl", ["activewindow", "-j"]);
        command.timeout_ms = 200;
        let current = self
            .runner
            .run(&command)
            .ok()
            .and_then(|b| hyprland::parse(&b))
            .and_then(|w| w.address);
        if current.as_deref() == Some(wanted.as_str()) {
            return Ok(());
        }
        self.focus_original()
    }
    pub fn set_overlay_active(&self, active: bool) {
        self.overlay_active.store(active, Ordering::SeqCst);
        *self.cache.lock().unwrap() = None;
    }
    pub fn invalidate(&self) {
        *self.cache.lock().unwrap() = None;
        self.refresh_files();
    }
    pub fn snapshot(&self, input: &str) -> Context {
        if self.last_scan.lock().unwrap().elapsed() > Duration::from_secs(600) {
            self.refresh_files();
        }
        let mut cache = self.cache.lock().unwrap();
        if cache
            .as_ref()
            .is_none_or(|(at, _)| at.elapsed() > Duration::from_millis(500))
        {
            let mut warnings = Vec::new();
            if !self.overlay_active.load(Ordering::SeqCst) {
                self.capture_foreground();
            }
            let (window, private) = self.foreground.lock().unwrap().clone();
            if window.is_none() {
                warnings.push("active window unavailable".into());
            }
            let mut types = CommandSpec::new("wl-paste", ["--list-types"]);
            types.timeout_ms = 200;
            let mime = self.runner.run(&types).unwrap_or_else(|_| {
                warnings.push("clipboard unavailable".into());
                Vec::new()
            });
            let mut paste = CommandSpec::new("wl-paste", ["--no-newline", "--type", "text"]);
            paste.timeout_ms = 200;
            paste.output_limit = 4096;
            let bytes = if private
                || !String::from_utf8_lossy(&mime)
                    .lines()
                    .any(|l| l.starts_with("text/"))
            {
                Vec::new()
            } else {
                self.runner.run(&paste).unwrap_or_default()
            };
            let clipboard = clipboard::parse(&mime, &bytes, private);
            let recent_path = self.home.join(".local/share/recently-used.xbel");
            let recent = if fs::metadata(&recent_path).is_ok_and(|m| m.len() <= 2 * 1024 * 1024) {
                fs::read_to_string(recent_path)
                    .ok()
                    .map(|s| recent::parse(&s, &self.home))
                    .unwrap_or_default()
            } else {
                Vec::new()
            };
            *cache = Some((
                Instant::now(),
                Context {
                    window,
                    clipboard,
                    recent,
                    candidates: Vec::new(),
                    apps: self
                        .apps()
                        .into_iter()
                        .map(|mut app| {
                            app.icon = None;
                            app
                        })
                        .collect(),
                    time: String::new(),
                    warnings,
                    files_indexed: 0,
                },
            ));
        }
        let mut ctx = cache.as_ref().unwrap().1.clone();
        drop(cache);
        let paths = self.paths.lock().unwrap();
        ctx.files_indexed = paths.len();
        ctx.candidates = files::fuzzy(&paths, input);
        use chrono::Timelike;
        let now = chrono::Local::now();
        let period = match now.hour() {
            5..=11 => "morning",
            12..=17 => "afternoon",
            18..=22 => "evening",
            _ => "night",
        };
        ctx.time = format!("{}, {period}", now.format("%a %H:%M"));
        ctx
    }
    pub fn apps(&self) -> Vec<apps::App> {
        self.apps.lock().unwrap().clone()
    }
    pub fn refresh_apps(&self) {
        let apps = apps::collect(&self.home);
        *self.apps.lock().unwrap() = apps;
        *self.cache.lock().unwrap() = None;
    }
    pub fn app(&self, id: &str) -> Option<apps::App> {
        self.apps
            .lock()
            .unwrap()
            .iter()
            .find(|a| a.id == id)
            .cloned()
    }
    pub fn home(&self) -> &Path {
        &self.home
    }
}
