use crate::{
    Engine, ExecuteRequest, ExecuteResult, NativeState, Preview, PreviewRequest, Proposal,
    catalogue::Catalogue,
};
use std::sync::Arc;
use tauri::{Emitter, Manager, State};
#[tauri::command]
pub async fn get_state(
    engine: State<'_, Arc<Engine>>,
    input: Option<String>,
) -> Result<NativeState, String> {
    let engine = engine.inner().clone();
    tauri::async_runtime::spawn_blocking(move || engine.state(input.as_deref().unwrap_or("")))
        .await
        .map_err(|_| "context task failed")?
}
/// Cached installed metadata only. No foreground, clipboard or inference work.
#[tauri::command]
pub fn get_apps(engine: State<'_, Arc<Engine>>) -> Vec<crate::collectors::apps::App> {
    engine.collectors.apps()
}
#[tauri::command]
pub async fn execute(
    app: tauri::AppHandle,
    engine: State<'_, Arc<Engine>>,
    request: ExecuteRequest,
) -> Result<ExecuteResult, String> {
    let engine = engine.inner().clone();
    let timer_engine = engine.clone();
    let action_app = app.clone();
    let (result, timer) = tauri::async_runtime::spawn_blocking(move || {
        engine.execute_with_hook(request, |plan| {
            if matches!(plan, crate::exec::Plan::Commands(_))
                || matches!(plan, crate::exec::Plan::Command(c) if c.program == "hyprshot")
            {
                let window = action_app
                    .get_webview_window("main")
                    .ok_or("window unavailable")?;
                cancel_placement();
                window
                    .hide()
                    .map_err(|_| "cannot hide window before focus-sensitive action")?;
                engine.collectors.set_overlay_active(false);
                engine.collectors.focus_original()?;
                let _ = action_app.emit("native:visibility", serde_json::json!({"visible":false}));
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Ok(())
        })
    })
    .await
    .map_err(|_| "execution task failed")??;
    if let Some(seconds) = timer {
        let app = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_secs(seconds));
            timer_engine.timer_finished();
            let _ = app.emit(
                "native:timer",
                serde_json::json!({"actionId":"set_timer","seconds":seconds}),
            );
        });
    }
    Ok(result)
}
/// Read-only result for a card (calc, convert). Never executes or records anything.
#[tauri::command]
pub async fn preview(
    engine: State<'_, Arc<Engine>>,
    request: PreviewRequest,
) -> Result<Preview, String> {
    let engine = engine.inner().clone();
    tauri::async_runtime::spawn_blocking(move || engine.preview(request))
        .await
        .map_err(|_| "preview task failed")?
}
#[tauri::command]
pub async fn reload(
    app: tauri::AppHandle,
    engine: State<'_, Arc<Engine>>,
) -> Result<Catalogue, String> {
    let engine = engine.inner().clone();
    let catalogue = tauri::async_runtime::spawn_blocking(move || engine.reload())
        .await
        .map_err(|_| "reload task failed")??;
    let _ = app.emit("native:catalogue", &catalogue);
    Ok(catalogue)
}
#[tauri::command]
pub async fn propose_action(
    engine: State<'_, Arc<Engine>>,
    inputs: Vec<String>,
) -> Result<Proposal, String> {
    let engine = engine.inner().clone();
    tauri::async_runtime::spawn_blocking(move || engine.propose(inputs))
        .await
        .map_err(|_| "text task failed")?
}
#[tauri::command]
pub async fn save_proposal(
    app: tauri::AppHandle,
    engine: State<'_, Arc<Engine>>,
    proposal_id: String,
    reviewed_toml: String,
) -> Result<Catalogue, String> {
    let engine = engine.inner().clone();
    let catalogue =
        tauri::async_runtime::spawn_blocking(move || engine.save(&proposal_id, &reviewed_toml))
            .await
            .map_err(|_| "save task failed")??;
    let _ = app.emit("native:catalogue", &catalogue);
    Ok(catalogue)
}
/// Dismiss from the UI (Escape, or after an action ran). Hands focus back to the
/// window that was active before the launcher opened.
#[tauri::command]
pub fn hide_window(app: tauri::AppHandle) -> Result<(), String> {
    hide(&app, Restore::Original)
}

/// Fit the window to the panel's content height (CSS px). The width is fixed.
#[tauri::command]
pub fn fit_overlay(app: tauri::AppHandle, height: f64) -> Result<(), String> {
    use std::sync::atomic::Ordering;
    let height = crate::overlay::clamp_height(height);
    if OVERLAY_HEIGHT.swap(height, Ordering::SeqCst) == height {
        return Ok(());
    }
    let window = app.get_webview_window("main").ok_or("window unavailable")?;
    if !window.is_visible().unwrap_or(false) {
        return Ok(());
    }
    let engine = app
        .try_state::<Arc<Engine>>()
        .ok_or("launcher is still starting")?;
    if placement_wanted() {
        // Supersede any in-flight placement; the new one sizes and re-anchors.
        // Focus already gained in this show carries over to the new generation.
        let previous = SHOW_GENERATION.fetch_add(1, Ordering::SeqCst);
        let generation = previous + 1;
        let _ = FOCUSED_GENERATION.compare_exchange(
            previous,
            generation,
            Ordering::SeqCst,
            Ordering::SeqCst,
        );
        place_overlay(&app, engine.inner().clone(), generation);
        Ok(())
    } else {
        #[cfg(target_os = "linux")]
        if layer_surface() {
            crate::layer_shell::on_gtk(&app, move |w| {
                crate::layer_shell::set_size(w, crate::overlay::WIDTH, height)
            });
            return Ok(());
        }
        window
            .set_size(tauri::LogicalSize::new(
                f64::from(crate::overlay::WIDTH),
                f64::from(height),
            ))
            .map_err(|_| "cannot resize window".into())
    }
}

/// Whether the launcher is a layer-shell overlay rather than a toplevel window.
pub fn layer_surface() -> bool {
    #[cfg(target_os = "linux")]
    return crate::layer_shell::active();
    #[cfg(not(target_os = "linux"))]
    false
}

/// Hyprland placement applies to the toplevel window only; a layer surface is
/// positioned by its anchor and margin.
fn placement_wanted() -> bool {
    !layer_surface() && crate::overlay::wanted(|name| std::env::var_os(name))
}

/// "layer" or "window", for the frontend's framing.
#[tauri::command]
pub fn overlay_surface() -> &'static str {
    if layer_surface() { "layer" } else { "window" }
}

/// Where focus goes when the launcher hides.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Restore {
    /// Back to the window that was active before the launcher opened.
    Original,
    /// Leave it to the compositor: the user clicked elsewhere.
    Compositor,
}

pub fn hide(app: &tauri::AppHandle, restore: Restore) -> Result<(), String> {
    let window = app.get_webview_window("main").ok_or("window unavailable")?;
    cancel_placement();
    let was_visible = window.is_visible().unwrap_or(false);
    window.hide().map_err(|_| "cannot hide window")?;
    if let Some(engine) = app.try_state::<Arc<Engine>>() {
        engine.collectors.set_overlay_active(false);
        if was_visible && restore == Restore::Original {
            if layer_surface() {
                // Hyprland refuses window focus while our exclusive layer is mapped
                // (its focus dispatcher still warps the pointer), and hiding from
                // another thread unmaps asynchronously. Once the layer is gone,
                // Hyprland has usually refocused the original itself; restore only
                // if not. A show in between supersedes the restore.
                let engine = engine.inner().clone();
                let generation = SHOW_GENERATION.load(std::sync::atomic::Ordering::SeqCst);
                std::thread::spawn(move || {
                    let pid = std::process::id();
                    for _ in 0..40 {
                        if SHOW_GENERATION.load(std::sync::atomic::Ordering::SeqCst) != generation {
                            return;
                        }
                        if !crate::overlay::own_layer_mapped(engine.runner(), pid).unwrap_or(false)
                        {
                            break;
                        }
                        std::thread::sleep(std::time::Duration::from_millis(15));
                    }
                    let _ = engine.collectors.restore_original();
                });
            } else {
                // Best effort: the original window may have closed meanwhile.
                let _ = engine.collectors.focus_original();
            }
        }
    }
    let _ = app.emit("native:visibility", serde_json::json!({"visible":false}));
    Ok(())
}

/// Content height the frontend last asked for.
static OVERLAY_HEIGHT: std::sync::atomic::AtomicI32 =
    std::sync::atomic::AtomicI32::new(crate::overlay::MIN_HEIGHT);
/// The show generation in which the window last gained focus. Focus loss only
/// dismisses a window that actually had focus during the current show, so the
/// map/float/resize sequence right after showing can never hide it.
static FOCUSED_GENERATION: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
/// How long focus must stay elsewhere before the launcher dismisses itself.
/// Hyprland briefly reports focus changes while it floats and resizes a window.
const BLUR_DISMISS_MS: u64 = 120;

/// Track focus for dismiss-on-focus-loss. Call from the window event handler.
pub fn focus_changed(app: &tauri::AppHandle, focused: bool) {
    use std::sync::atomic::Ordering;
    let generation = SHOW_GENERATION.load(Ordering::SeqCst);
    if focused {
        FOCUSED_GENERATION.store(generation, Ordering::SeqCst);
        return;
    }
    if FOCUSED_GENERATION.load(Ordering::SeqCst) != generation
        || std::env::var_os("SOFT_LAUNCHER_KEEP_ON_BLUR").is_some()
    {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(BLUR_DISMISS_MS));
        let Some(window) = app.get_webview_window("main") else {
            return;
        };
        // Shown again, hidden, re-placed or refocused meanwhile: not a dismissal.
        if SHOW_GENERATION.load(Ordering::SeqCst) != generation
            || window.is_focused().unwrap_or(true)
            || !window.is_visible().unwrap_or(false)
        {
            return;
        }
        if let Err(message) = hide(&app, Restore::Compositor) {
            let _ = app.emit(
                "native:error",
                serde_json::json!({"source":"window","message":message}),
            );
        }
    });
}

/// Bumped on every show and hide, so a placement thread can tell it was overtaken.
static SHOW_GENERATION: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Abandon any in-flight overlay placement. Call before every hide.
pub fn cancel_placement() {
    SHOW_GENERATION.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
}

/// Float, size and centre our own window on Hyprland once it maps. Each show remaps
/// the surface, so this runs every time, on its own thread: polling for the map on
/// the GTK thread would block the very event loop that maps the window. Failure
/// never blocks showing; it is reported to the UI as a native error.
fn place_overlay(app: &tauri::AppHandle, engine: Arc<Engine>, generation: u64) {
    use std::sync::atomic::Ordering;
    if !placement_wanted() {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        let started = std::time::Instant::now();
        let current = || SHOW_GENERATION.load(Ordering::SeqCst) == generation;
        let result = crate::overlay::place(
            engine.runner(),
            std::process::id(),
            || OVERLAY_HEIGHT.load(Ordering::SeqCst),
            60,
            || std::thread::sleep(std::time::Duration::from_millis(10)),
            current,
        );
        match result {
            Ok(Some(p)) => {
                if std::env::var_os("SOFT_LAUNCHER_DEBUG_PLACEMENT").is_some() {
                    eprintln!(
                        "soft-launcher: placed {} at {},{} {}x{} in {} ms{}",
                        p.address,
                        p.x,
                        p.y,
                        p.width,
                        p.height,
                        started.elapsed().as_millis(),
                        if p.unchanged { " (unchanged)" } else { "" }
                    );
                }
            }
            Ok(None) => {}
            Err(message) if current() => {
                let _ = app.emit(
                    "native:error",
                    serde_json::json!({"source":"window","message":message}),
                );
            }
            Err(_) => {}
        }
    });
}

pub fn show(app: &tauri::AppHandle, toggle: bool) -> Result<(), String> {
    let window = app.get_webview_window("main").ok_or("window unavailable")?;
    let engine = app
        .try_state::<Arc<Engine>>()
        .ok_or("launcher is still starting")?;
    if toggle && window.is_visible().unwrap_or(false) {
        return hide(app, Restore::Original);
    }
    // Capture the original foreground window before we take focus.
    engine.collectors.capture_foreground();
    engine.collectors.set_overlay_active(true);
    let generation = SHOW_GENERATION.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
    let height = OVERLAY_HEIGHT.load(std::sync::atomic::Ordering::SeqCst);
    #[cfg(target_os = "linux")]
    if layer_surface() {
        // On the monitor where the hotkey was pressed; the compositor decides if
        // Hyprland can't say. This may run on the single-instance D-Bus thread, so
        // the GTK calls go to the GTK thread, ahead of the show below.
        let monitor = crate::overlay::focused_monitor(engine.runner()).ok();
        crate::layer_shell::on_gtk(app, move |w| {
            crate::layer_shell::prepare(w, monitor.as_ref());
            crate::layer_shell::set_size(w, crate::overlay::WIDTH, height);
        });
        // The exclusive layer takes the keyboard when it maps; no activation needed.
        window.show().map_err(|_| "cannot show window")?;
        let _ = app.emit("native:visibility", serde_json::json!({"visible":true}));
        return Ok(());
    }
    if !placement_wanted() {
        let _ = window.set_size(tauri::LogicalSize::new(
            f64::from(crate::overlay::WIDTH),
            f64::from(height),
        ));
    }
    window.show().map_err(|_| "cannot show window")?;
    window.set_focus().map_err(|_| "cannot focus window")?;
    place_overlay(app, engine.inner().clone(), generation);
    let _ = app.emit("native:visibility", serde_json::json!({"visible":true}));
    Ok(())
}
