//! The launcher as a wlr-layer-shell overlay instead of a toplevel window.
//!
//! With Hyprland's `input:follow_mouse = 1`, pointer motion over a window moves
//! keyboard focus to it. That took focus from the toplevel launcher, and equally
//! from an on-demand layer surface: Hyprland 0.56.2 `mouseMoveUnified` refocuses the
//! window under the pointer unless the focused layer has *exclusive* keyboard
//! interactivity. With exclusive interactivity the compositor also sends every
//! pointer event to the layer, so a click elsewhere arrives here with coordinates
//! outside our surface and is not passed to the window under it. We dismiss on that
//! click, as a menu does. GDK 3 drops pointer events outside a window unless it holds
//! a grab, so the mapped overlay holds a client-side pointer grab (owner events on, so
//! clicks inside still reach the page). Measured on real GTK3 layer surfaces and the
//! installed app with `scripts/installed-layer-acceptance.py`.
//!
//! gtk-layer-shell is opened at runtime: first `lib/` next to the executable (the
//! installer ships it there), then the system soname. Without it, or off Wayland,
//! the window stays a toplevel and `overlay.rs` placement applies.

use crate::overlay::Monitor;
use gtk::glib::translate::ToGlibPtr;
use gtk::prelude::*;
use std::ffi::{c_char, c_int};
use std::sync::OnceLock;

type Win = *mut gtk::ffi::GtkWindow;

const LAYER_OVERLAY: c_int = 3;
const EDGE_TOP: c_int = 2;
const KEYBOARD_EXCLUSIVE: c_int = 1;
const NAMESPACE: &[u8] = b"soft-launcher\0";

struct Api {
    _lib: libloading::Library,
    is_supported: unsafe extern "C" fn() -> c_int,
    init_for_window: unsafe extern "C" fn(Win),
    set_namespace: unsafe extern "C" fn(Win, *const c_char),
    set_layer: unsafe extern "C" fn(Win, c_int),
    set_keyboard_mode: unsafe extern "C" fn(Win, c_int),
    set_anchor: unsafe extern "C" fn(Win, c_int, c_int),
    set_margin: unsafe extern "C" fn(Win, c_int, c_int),
    set_monitor: unsafe extern "C" fn(Win, *mut gtk::gdk::ffi::GdkMonitor),
}

fn load() -> Result<Api, String> {
    let bundled = std::env::current_exe()
        .ok()
        .and_then(|exe| Some(exe.parent()?.join("lib/libgtk-layer-shell.so.0")));
    // SAFETY: loading gtk-layer-shell runs only its ELF constructors, which register
    // nothing until a window is initialised. Each symbol is typed exactly as declared in
    // gtk-layer-shell.h 0.10 (enums and gboolean are C ints), and the library is kept
    // in the same struct as the copied function pointers, which never outlive it.
    unsafe {
        let lib = match bundled.filter(|p| p.exists()) {
            Some(path) => libloading::Library::new(path),
            None => libloading::Library::new("libgtk-layer-shell.so.0"),
        }
        .map_err(|e| format!("gtk-layer-shell unavailable: {e}"))?;
        macro_rules! sym {
            ($name:literal) => {
                *lib.get(concat!($name, "\0").as_bytes())
                    .map_err(|_| format!("gtk-layer-shell lacks {}", $name))?
            };
        }
        Ok(Api {
            is_supported: sym!("gtk_layer_is_supported"),
            init_for_window: sym!("gtk_layer_init_for_window"),
            set_namespace: sym!("gtk_layer_set_namespace"),
            set_layer: sym!("gtk_layer_set_layer"),
            set_keyboard_mode: sym!("gtk_layer_set_keyboard_mode"),
            set_anchor: sym!("gtk_layer_set_anchor"),
            set_margin: sym!("gtk_layer_set_margin"),
            set_monitor: sym!("gtk_layer_set_monitor"),
            _lib: lib,
        })
    }
}

static API: OnceLock<Api> = OnceLock::new();

/// Whether the launcher window was made a layer surface. Set once at startup.
pub fn active() -> bool {
    API.get().is_some()
}

/// Wanted on Wayland unless the user opted out with `SOFT_LAUNCHER_NO_LAYER_SHELL`.
pub fn wanted(var: impl Fn(&str) -> Option<std::ffi::OsString>) -> bool {
    var("WAYLAND_DISPLAY").is_some_and(|v| !v.is_empty())
        && var("SOFT_LAUNCHER_NO_LAYER_SHELL").is_none()
}

/// Turn the not-yet-shown window into an overlay layer surface: horizontally
/// centred, exclusive keyboard. Monitor and top edge are set per show.
/// `on_outside_press` runs on the GTK thread for a button press outside the surface.
/// Must run on the GTK thread before the window is first mapped.
pub fn init(
    window: &gtk::ApplicationWindow,
    on_outside_press: impl Fn() + 'static,
) -> Result<(), String> {
    if API.get().is_some() {
        return Ok(());
    }
    if !window.display().backend().is_wayland() {
        return Err("not a Wayland display".into());
    }
    if window.is_realized() {
        return Err("window already realised".into());
    }
    let api = load()?;
    let w: Win = window.upcast_ref::<gtk::Window>().to_glib_none().0;
    // SAFETY: `w` is a live, unrealised GtkWindow owned by tao for the app's lifetime,
    // and we are on the GTK main thread.
    unsafe {
        if (api.is_supported)() == 0 {
            return Err("compositor lacks wlr-layer-shell".into());
        }
        (api.init_for_window)(w);
        (api.set_namespace)(w, NAMESPACE.as_ptr().cast());
        (api.set_layer)(w, LAYER_OVERLAY);
        (api.set_keyboard_mode)(w, KEYBOARD_EXCLUSIVE);
    }
    let _ = API.set(api);
    // A layer window sized only by its default size maps blank and never resizes;
    // the size request is what gtk-layer-shell sends to the compositor.
    set_size(window, crate::overlay::WIDTH, crate::overlay::MIN_HEIGHT);
    window.connect_button_press_event(move |window, event| {
        let (x, y) = event.position();
        let size = window.allocation();
        if crate::overlay::outside(x, y, size.width(), size.height()) {
            on_outside_press();
            return gtk::glib::Propagation::Stop;
        }
        gtk::glib::Propagation::Proceed
    });
    // Client-side only: the compositor already routes the pointer to an exclusive
    // layer. Without the grab GDK discards presses outside the surface.
    window.connect_map_event(|window, _| {
        if let (Some(gdk_window), Some(seat)) = (window.window(), window.display().default_seat())
        {
            let status = seat.grab(
                &gdk_window,
                gtk::gdk::SeatCapabilities::ALL_POINTING,
                true,
                None,
                None,
                None,
            );
            if status != gtk::gdk::GrabStatus::Success {
                eprintln!("soft-launcher: pointer grab failed ({status:?}); click outside will not dismiss");
            }
        }
        gtk::glib::Propagation::Proceed
    });
    window.connect_unmap_event(|window, _| {
        if let Some(seat) = window.display().default_seat() {
            seat.ungrab();
        }
        gtk::glib::Propagation::Proceed
    });
    Ok(())
}

/// Size the layer surface. Call on the GTK thread.
pub fn set_size(window: &gtk::ApplicationWindow, width: i32, height: i32) {
    window.set_size_request(width, height);
    window.resize(width, height);
}

/// Run `f` with the launcher's GTK window on the GTK main thread: now if already
/// there, otherwise queued on Tauri's event loop ahead of later window requests.
pub fn on_gtk<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    f: impl FnOnce(&gtk::ApplicationWindow) + Send + 'static,
) {
    use tauri::Manager;
    let handle = app.clone();
    let run = move || {
        if let Some(window) = handle
            .get_webview_window("main")
            .and_then(|w| w.gtk_window().ok())
        {
            f(&window);
        }
    };
    if gtk::is_initialized_main_thread() {
        run();
    } else {
        let _ = app.run_on_main_thread(run);
    }
}

/// Before a show: put the surface on `monitor` (the focused one) with its top edge
/// where a `MAX_HEIGHT` panel would be centred. `None` lets the compositor choose and
/// centres vertically by anchoring nothing.
pub fn prepare(window: &gtk::ApplicationWindow, monitor: Option<&Monitor>) {
    let Some(api) = API.get() else { return };
    let w: Win = window.upcast_ref::<gtk::Window>().to_glib_none().0;
    let gdk_monitor = monitor.and_then(|m| {
        let display = window.display();
        (0..display.n_monitors())
            .filter_map(|i| display.monitor(i))
            .find(|g| {
                let r = g.geometry();
                (r.x(), r.y()) == (m.x, m.y)
            })
    });
    // The margin only means something on the monitor it was computed for.
    let margin = gdk_monitor
        .as_ref()
        .and(monitor)
        .map(crate::overlay::layer_top_margin);
    // SAFETY: as in `init`; `gdk_monitor` is a live GdkMonitor owned by the display,
    // or null, which gtk-layer-shell documents as "compositor decides".
    unsafe {
        let ptr = gdk_monitor
            .as_ref()
            .map_or(std::ptr::null_mut(), |g| g.to_glib_none().0);
        (api.set_monitor)(w, ptr);
        (api.set_anchor)(w, EDGE_TOP, c_int::from(margin.is_some()));
        (api.set_margin)(w, EDGE_TOP, margin.unwrap_or(0));
    }
}
