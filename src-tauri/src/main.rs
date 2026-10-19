use soft_launcher::{Engine, commands};
use std::{path::PathBuf, sync::Arc, time::Duration};
use tauri::{Emitter, Manager};
fn catalogue_path(app: &tauri::App) -> Result<PathBuf, Box<dyn std::error::Error>> {
    if let Some(path) = std::env::var_os("SOFT_LAUNCHER_CATALOGUE") {
        return Ok(path.into());
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../catalogue.toml");
    if cfg!(debug_assertions) && dev.exists() {
        return Ok(dev);
    }
    let dir = app.path().app_config_dir()?;
    std::fs::create_dir_all(&dir)?;
    let path = dir.join("catalogue.toml");
    if !path.exists() {
        std::fs::copy(app.path().resource_dir()?.join("catalogue.toml"), &path)?;
    }
    Ok(path)
}
/// Environment variables that already select a WebKitGTK/GDK rendering workaround.
/// If the user set any of them (to any value, including "0" or ""), we leave the
/// environment alone and respect that choice.
const RENDERING_OVERRIDES: [&str; 4] = [
    "GDK_GL",
    "__NV_DISABLE_EXPLICIT_SYNC",
    "WEBKIT_DISABLE_DMABUF_RENDERER",
    "WEBKIT_DISABLE_COMPOSITING_MODE",
];

/// Whether to disable NVIDIA explicit sync for this process.
///
/// On some Hyprland/NVIDIA setups, re-showing the hidden overlay intermittently kills the
/// app with Wayland protocol error 71, "Missing acquire timeline": WebKitGTK's
/// accelerated surface is recommitted without the explicit-sync acquire point the
/// compositor expects. The baseline crashed on first reopen in two independent
/// smoke runs. GDK_GL=disable, WEBKIT_DISABLE_DMABUF_RENDERER=1 and
/// __NV_DISABLE_EXPLICIT_SYNC=1 each passed 10 of 10 toggles. The last keeps GPU
/// acceleration and Tauri recommends it before broader renderer fallbacks, so it
/// is the default. It applies only to a native
/// Wayland session, and never when the user already chose a rendering override or a
/// non-Wayland GDK backend.
fn wants_explicit_sync_workaround(var: impl Fn(&str) -> Option<std::ffi::OsString>) -> bool {
    if !cfg!(target_os = "linux") || var("WAYLAND_DISPLAY").is_none_or(|v| v.is_empty()) {
        return false;
    }
    let wayland_backend = match var("GDK_BACKEND") {
        None => true,
        Some(v) => v.to_string_lossy().split(',').next().map(str::trim) == Some("wayland"),
    };
    wayland_backend && RENDERING_OVERRIDES.iter().all(|name| var(name).is_none())
}

/// Start without showing the window (login/systemd startup). Later invocations
/// with this flag are no-ops, so a service restart never pops the launcher up.
fn hidden(args: &[String]) -> bool {
    args.iter().any(|a| a == "--hidden" || a == "--background")
}

fn main() {
    if wants_explicit_sync_workaround(|name| std::env::var_os(name)) {
        // SAFETY: this is the first statement of main. No other thread exists yet
        // (Tauri, GTK, WebKit and our collectors all start below), so nothing can read
        // or write the environment concurrently. The change is process-local and is
        // inherited only by children; it writes no global configuration.
        unsafe { std::env::set_var("__NV_DISABLE_EXPLICIT_SYNC", "1") };
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, args, _| {
            if hidden(&args) {
                return;
            }
            if let Err(message) = commands::show(app, args.iter().any(|a| a == "--toggle")) {
                let _ = app.emit(
                    "native:error",
                    serde_json::json!({"source":"window","message":message}),
                );
            }
        }))
        .setup(|app| {
            let home = std::env::var_os("HOME")
                .map(PathBuf::from)
                .ok_or("HOME unavailable")?
                .canonicalize()?;
            let engine = Arc::new(Engine::open(catalogue_path(app)?, home)?);
            app.manage(engine.clone());
            #[cfg(target_os = "linux")]
            if soft_launcher::layer_shell::wanted(|name| std::env::var_os(name)) {
                let handle = app.handle().clone();
                // A click anywhere else dismisses, leaving focus to the compositor.
                // Hiding goes through Tauri's event loop, so not from this GTK handler.
                let dismiss = move || {
                    let handle = handle.clone();
                    std::thread::spawn(move || {
                        let _ = commands::hide(&handle, commands::Restore::Compositor);
                    });
                };
                let result = app
                    .get_webview_window("main")
                    .ok_or_else(|| "window unavailable".to_string())
                    .and_then(|w| w.gtk_window().map_err(|e| e.to_string()))
                    .and_then(|w| soft_launcher::layer_shell::init(&w, dismiss));
                if let Err(message) = result {
                    eprintln!("soft-launcher: layer-shell off, using a floating window: {message}");
                }
            }
            if !hidden(&std::env::args().collect::<Vec<_>>()) {
                commands::show(app.handle(), false)?;
            }
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let mut last_error = None;
                loop {
                    std::thread::sleep(Duration::from_millis(400));
                    let Ok(mut store) = engine.store.try_lock() else {
                        continue;
                    };
                    match store.reload() {
                        Ok(true) => {
                            last_error = None;
                            let _ = handle.emit("native:catalogue", &store.catalogue);
                        }
                        Ok(false) => {}
                        Err(message) => {
                            if last_error.as_ref() != Some(&message) {
                                let _ = handle.emit(
                                    "native:error",
                                    serde_json::json!({"source":"catalogue","message":message}),
                                );
                                last_error = Some(message);
                            }
                        }
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| match event {
            tauri::WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close();
                let _ = commands::hide(window.app_handle(), commands::Restore::Original);
            }
            // Like a quick launcher: clicking elsewhere or switching away dismisses.
            tauri::WindowEvent::Focused(focused) => {
                commands::focus_changed(window.app_handle(), *focused);
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_state,
            commands::get_apps,
            commands::execute,
            commands::preview,
            commands::reload,
            commands::propose_action,
            commands::save_proposal,
            commands::hide_window,
            commands::fit_overlay,
            commands::overlay_surface
        ])
        .run(tauri::generate_context!())
        .expect("soft-launcher desktop failed");
}

#[cfg(test)]
mod tests {
    use super::{hidden, wants_explicit_sync_workaround};

    #[test]
    fn hidden_startup_flags() {
        let args = |a: &[&str]| a.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert!(hidden(&args(&["soft-launcher", "--hidden"])));
        assert!(hidden(&args(&["soft-launcher", "--background"])));
        assert!(!hidden(&args(&["soft-launcher", "--toggle"])));
        assert!(!hidden(&args(&["soft-launcher"])));
    }
    use std::{collections::HashMap, ffi::OsString};

    fn env(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<OsString> {
        let map: HashMap<String, OsString> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), OsString::from(v)))
            .collect();
        move |name| map.get(name).cloned()
    }

    #[test]
    fn applies_on_plain_wayland() {
        assert_eq!(
            wants_explicit_sync_workaround(env(&[("WAYLAND_DISPLAY", "wayland-1")])),
            cfg!(target_os = "linux")
        );
        assert_eq!(
            wants_explicit_sync_workaround(env(&[
                ("WAYLAND_DISPLAY", "wayland-1"),
                ("GDK_BACKEND", "wayland,x11")
            ])),
            cfg!(target_os = "linux")
        );
    }

    #[test]
    fn skips_without_wayland_or_with_other_backend() {
        assert!(!wants_explicit_sync_workaround(env(&[])));
        assert!(!wants_explicit_sync_workaround(env(&[(
            "WAYLAND_DISPLAY",
            ""
        )])));
        assert!(!wants_explicit_sync_workaround(env(&[
            ("WAYLAND_DISPLAY", "wayland-1"),
            ("GDK_BACKEND", "x11")
        ])));
        assert!(!wants_explicit_sync_workaround(env(&[
            ("WAYLAND_DISPLAY", "wayland-1"),
            ("GDK_BACKEND", "x11,wayland")
        ])));
    }

    #[test]
    fn respects_any_explicit_user_override_including_zero() {
        for name in [
            "GDK_GL",
            "__NV_DISABLE_EXPLICIT_SYNC",
            "WEBKIT_DISABLE_DMABUF_RENDERER",
            "WEBKIT_DISABLE_COMPOSITING_MODE",
        ] {
            for value in ["0", "", "1"] {
                assert!(
                    !wants_explicit_sync_workaround(env(&[
                        ("WAYLAND_DISPLAY", "wayland-1"),
                        (name, value)
                    ])),
                    "{name}={value}"
                );
            }
        }
    }
}
