//! Hyprland placement for the launcher's own window.
//!
//! Without user window rules Hyprland tiles the overlay, may maximise it, and may
//! insert it into an auto-group. After every show we find *our* client (pid and
//! class) in `hyprctl clients -j` and send one Lua dispatch (Hyprland 0.55+ runs
//! `hyprctl dispatch` as Lua). The Lua resolves the window by `address:` into a window
//! object, re-checks pid and class, and passes that object to every dispatcher: leave
//! the group, clear fullscreen/maximise, float, resize, move to the centre of the
//! monitor's usable area. Dispatchers fall back to the *active* window when no window
//! is given, so a window object is always passed and an unmatched address aborts.
//! Nothing writes compositor config or runtime keywords.
//!
//! The window is exactly the panel: opaque, [`WIDTH`] wide and as tall as the
//! frontend's content (clamped to [`MIN_HEIGHT`]..=[`MAX_HEIGHT`]). A larger
//! transparent window lets Hyprland blur the desktop around the panel and puts the
//! compositor border far from it. The top edge stays where a [`MAX_HEIGHT`] window
//! would be centred, so growing results or settings extend downwards only.

use crate::process::{CommandSpec, Runner};
use serde::Deserialize;

pub const WIDTH: i32 = 760;
pub const MIN_HEIGHT: i32 = 232;
pub const MAX_HEIGHT: i32 = 600;
pub const CLASS: &str = "soft-launcher";

#[derive(Debug, Deserialize)]
pub struct Client {
    pub address: String,
    #[serde(default)]
    pub mapped: bool,
    #[serde(default)]
    pub floating: bool,
    #[serde(default)]
    pub fullscreen: i64,
    #[serde(default)]
    pub monitor: i64,
    #[serde(default)]
    pub class: String,
    #[serde(default)]
    pub pid: i64,
    #[serde(default)]
    pub grouped: Vec<String>,
    #[serde(default)]
    pub at: [i32; 2],
    #[serde(default)]
    pub size: [i32; 2],
}

#[derive(Debug, Deserialize)]
pub struct Monitor {
    pub id: i64,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    #[serde(default = "one")]
    pub scale: f64,
    #[serde(default)]
    pub transform: i32,
    /// Reserved edges (bars): left, top, right, bottom.
    #[serde(default)]
    pub reserved: [i32; 4],
    #[serde(default)]
    pub focused: bool,
}
fn one() -> f64 {
    1.0
}

#[derive(Debug, PartialEq, Eq)]
pub struct Placement {
    pub address: String,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    /// True when geometry already matched and no dispatch was sent.
    pub unchanged: bool,
}

/// Placement is wanted on Hyprland unless the user opted out (any value).
pub fn wanted(var: impl Fn(&str) -> Option<std::ffi::OsString>) -> bool {
    var("HYPRLAND_INSTANCE_SIGNATURE").is_some_and(|v| !v.is_empty())
        && var("SOFT_LAUNCHER_NO_PLACEMENT").is_none()
}

fn valid_address(address: &str) -> bool {
    address.len() > 2
        && address.len() <= 18
        && address.starts_with("0x")
        && address[2..].bytes().all(|c| c.is_ascii_hexdigit())
}

/// Our mapped client: same pid and class. None until the compositor has mapped it.
pub fn own_client(clients: &[Client], pid: u32) -> Option<&Client> {
    clients.iter().find(|c| {
        c.mapped
            && c.pid == i64::from(pid)
            && c.class.eq_ignore_ascii_case(CLASS)
            && valid_address(&c.address)
    })
}

/// Height the frontend asked for, clamped to the supported range.
pub fn clamp_height(height: f64) -> i32 {
    if !height.is_finite() {
        return MIN_HEIGHT;
    }
    (height.ceil() as i64).clamp(i64::from(MIN_HEIGHT), i64::from(MAX_HEIGHT)) as i32
}

/// Top-left for a `WIDTH`×`height` window: horizontally centred, top edge where a
/// `MAX_HEIGHT` window would be centred in the usable area.
pub fn anchor(monitor: &Monitor) -> (i32, i32) {
    centre(monitor, WIDTH, MAX_HEIGHT)
}

/// Top margin for the layer-shell surface. A layer with no exclusive zone is laid
/// out inside the usable area (below bars), so this is the same top edge as
/// [`anchor`], measured from the usable area's top.
pub fn layer_top_margin(monitor: &Monitor) -> i32 {
    anchor(monitor).1 - monitor.y - monitor.reserved[1]
}

/// Whether a pointer position in surface coordinates lies outside a `width`×`height`
/// surface. An exclusive-keyboard layer receives clicks made anywhere on screen.
pub fn outside(x: f64, y: f64, width: i32, height: i32) -> bool {
    !(x >= 0.0 && y >= 0.0 && x < f64::from(width) && y < f64::from(height))
}

/// Whether Hyprland still has a mapped layer surface of ours (pid and namespace).
pub fn own_layer_mapped(runner: &dyn Runner, pid: u32) -> Result<bool, String> {
    #[derive(Deserialize)]
    struct Layer {
        #[serde(default)]
        namespace: String,
        #[serde(default)]
        pid: i64,
    }
    #[derive(Deserialize)]
    struct Output {
        #[serde(default)]
        levels: std::collections::HashMap<String, Vec<Layer>>,
    }
    let outputs: std::collections::HashMap<String, Output> = query(runner, "layers")?;
    Ok(outputs
        .values()
        .flat_map(|o| o.levels.values().flatten())
        .any(|l| l.pid == i64::from(pid) && l.namespace == CLASS))
}

/// The monitor Hyprland has focused: where the hotkey was pressed.
pub fn focused_monitor(runner: &dyn Runner) -> Result<Monitor, String> {
    let monitors: Vec<Monitor> = query(runner, "monitors")?;
    monitors
        .into_iter()
        .find(|m| m.focused)
        .ok_or_else(|| "no focused monitor".into())
}

/// Top-left that centres a `w`×`h` window in the monitor's usable logical area.
pub fn centre(monitor: &Monitor, w: i32, h: i32) -> (i32, i32) {
    let scale = if monitor.scale > 0.0 {
        monitor.scale
    } else {
        1.0
    };
    let (pw, ph) = if monitor.transform % 2 == 1 {
        (monitor.height, monitor.width)
    } else {
        (monitor.width, monitor.height)
    };
    let lw = (f64::from(pw) / scale).round() as i32;
    let lh = (f64::from(ph) / scale).round() as i32;
    let [left, top, right, bottom] = monitor.reserved;
    let uw = (lw - left - right).max(w);
    let uh = (lh - top - bottom).max(h);
    (
        monitor.x + left + (uw - w) / 2,
        monitor.y + top + (uh - h) / 2,
    )
}

/// Lua for `hyprctl dispatch`. Only validated integers and a hex address are
/// interpolated; every dispatcher receives the resolved window object `w`.
pub fn lua(client: &Client, pid: u32, x: i32, y: i32, height: i32) -> String {
    let a = &client.address;
    debug_assert!(valid_address(a));
    format!(
        "function() \
local w = hl.get_window(\"address:{a}\"); \
if not w or w.pid ~= {pid} or string.lower(w.class) ~= \"{CLASS}\" then error(\"soft-launcher window not found\") end; \
if w.group then hl.dispatch(hl.dsp.window.move({{ out_of_group = true, window = w }})) end; \
if w.fullscreen ~= 0 then hl.dispatch(hl.dsp.window.fullscreen_state({{ internal = 0, client = 0, action = \"set\", window = w }})) end; \
if not w.floating then hl.dispatch(hl.dsp.window.float({{ action = \"enable\", window = w }})) end; \
hl.dispatch(hl.dsp.window.resize({{ x = {WIDTH}, y = {height}, relative = false, window = w }})); \
hl.dispatch(hl.dsp.window.move({{ x = {x}, y = {y}, relative = false, window = w }})) \
end"
    )
}

fn query<T: for<'de> Deserialize<'de>>(runner: &dyn Runner, what: &str) -> Result<T, String> {
    let mut spec = CommandSpec::new("hyprctl", [what, "-j"]);
    spec.timeout_ms = 300;
    spec.output_limit = 512 * 1024;
    let bytes = runner.run(&spec)?;
    serde_json::from_slice(&bytes).map_err(|_| format!("cannot parse hyprctl {what}"))
}

/// One placement attempt. `Ok(None)` means our window is not mapped yet.
pub fn place_once(runner: &dyn Runner, pid: u32, height: i32) -> Result<Option<Placement>, String> {
    let height = height.clamp(MIN_HEIGHT, MAX_HEIGHT);
    let clients: Vec<Client> = query(runner, "clients")?;
    let Some(client) = own_client(&clients, pid) else {
        return Ok(None);
    };
    let monitors: Vec<Monitor> = query(runner, "monitors")?;
    let monitor = monitors
        .iter()
        .find(|m| m.id == client.monitor)
        .ok_or("overlay monitor not found")?;
    let (x, y) = anchor(monitor);
    let unchanged = client.floating
        && client.fullscreen == 0
        && client.grouped.is_empty()
        && client.size == [WIDTH, height]
        && client.at == [x, y];
    if !unchanged {
        let mut spec = CommandSpec::new("hyprctl", ["dispatch", &lua(client, pid, x, y, height)]);
        spec.timeout_ms = 500;
        let reply = runner.run(&spec)?;
        // Success prints exactly "ok"; Lua errors and dispatcher warnings print text.
        let text = String::from_utf8_lossy(&reply);
        if text.trim() != "ok" {
            let first = text
                .lines()
                .map(str::trim)
                .find(|l| !l.is_empty())
                .unwrap_or("no reply");
            return Err(format!(
                "hyprctl rejected overlay placement: {}",
                first.chars().take(160).collect::<String>()
            ));
        }
    }
    Ok(Some(Placement {
        address: client.address.clone(),
        x,
        y,
        width: WIDTH,
        height,
        unchanged,
    }))
}

/// Place our window, polling until the compositor maps it. `keep_going` lets the
/// caller abandon a stale attempt (window hidden or shown again meanwhile); an
/// abandoned attempt returns `Ok(None)` and reports nothing.
pub fn place(
    runner: &dyn Runner,
    pid: u32,
    height: impl Fn() -> i32,
    attempts: u32,
    wait: impl Fn(),
    keep_going: impl Fn() -> bool,
) -> Result<Option<Placement>, String> {
    for attempt in 0..attempts.max(1) {
        if !keep_going() {
            return Ok(None);
        }
        if let Some(placement) = place_once(runner, pid, height())? {
            return Ok(Some(placement));
        }
        if attempt + 1 < attempts {
            wait();
        }
    }
    if !keep_going() {
        return Ok(None);
    }
    Err("overlay window was not mapped in time; it may be tiled".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    struct Script {
        clients: Vec<String>,
        monitors: String,
        reply: String,
        calls: Mutex<Vec<Vec<String>>>,
    }
    impl Runner for Script {
        fn run(&self, spec: &CommandSpec) -> Result<Vec<u8>, String> {
            assert_eq!(spec.program, "hyprctl");
            let mut calls = self.calls.lock().unwrap();
            calls.push(spec.args.clone());
            let n = calls.iter().filter(|a| a[0] == "clients").count();
            Ok(match spec.args[0].as_str() {
                "layers" => self.clients[0].clone(),
                "clients" => self.clients[(n - 1).min(self.clients.len() - 1)].clone(),
                "monitors" => self.monitors.clone(),
                "dispatch" => self.reply.clone(),
                other => panic!("unexpected hyprctl {other}"),
            }
            .into_bytes())
        }
    }
    impl Script {
        fn dispatches(&self) -> Vec<String> {
            let calls = self.calls.lock().unwrap();
            calls
                .iter()
                .filter(|a| a[0] == "dispatch")
                .map(|a| a[1].clone())
                .collect()
        }
    }

    const MONITORS: &str = r#"[{"id":1,"x":2560,"y":0,"width":2560,"height":1440,"scale":1,"transform":0,"reserved":[0,32,0,0]},
        {"id":2,"x":0,"y":0,"width":3840,"height":2160,"scale":1.5,"transform":0,"reserved":[0,0,0,0]}]"#;

    fn client(pid: u32, class: &str, extra: &str) -> String {
        format!(
            r#"{{"address":"0xabc{pid}","mapped":true,"floating":false,"fullscreen":0,"monitor":1,"class":"{class}","pid":{pid},"grouped":[],"at":[0,0],"size":[1268,1396]{extra}}}"#
        )
    }

    fn script(clients: Vec<String>) -> Script {
        Script {
            clients,
            monitors: MONITORS.into(),
            reply: "ok".into(),
            calls: Mutex::new(Vec::new()),
        }
    }

    fn go() -> bool {
        true
    }

    fn min() -> i32 {
        MIN_HEIGHT
    }

    #[test]
    fn centres_in_usable_logical_area() {
        let m: Vec<Monitor> = serde_json::from_str(MONITORS).unwrap();
        assert_eq!(centre(&m[0], 1100, 420), (2560 + 730, 32 + 494));
        // 3840x2160 at scale 1.5 is 2560x1440 logical.
        assert_eq!(centre(&m[1], 1100, 420), (730, 510));
        let rotated = Monitor {
            transform: 1,
            ..serde_json::from_str::<Vec<Monitor>>(MONITORS)
                .unwrap()
                .remove(0)
        };
        assert_eq!(centre(&rotated, 1100, 420).0, 2560 + (1440 - 1100) / 2);
    }

    #[test]
    fn anchors_top_where_the_tallest_panel_would_be_centred() {
        let m: Vec<Monitor> = serde_json::from_str(MONITORS).unwrap();
        // Usable area of monitor 1 is 2560x1408 below a 32 px bar.
        assert_eq!(
            anchor(&m[0]),
            (2560 + (2560 - 760) / 2, 32 + (1408 - 600) / 2)
        );
        assert_eq!(clamp_height(10.0), MIN_HEIGHT);
        assert_eq!(clamp_height(301.2), 302);
        assert_eq!(clamp_height(5000.0), MAX_HEIGHT);
        assert_eq!(clamp_height(f64::NAN), MIN_HEIGHT);
        // Same top edge as a layer margin below the 32 px bar.
        assert_eq!(layer_top_margin(&m[0]), (1408 - 600) / 2);
        assert_eq!(layer_top_margin(&m[1]), (1440 - 600) / 2);
    }

    #[test]
    fn finds_our_mapped_layer() {
        let r = script(vec![
            r#"{"DP-2":{"levels":{"0":[],"3":[{"namespace":"soft-launcher","pid":7},{"namespace":"quickshell","pid":8}]}}}"#.into(),
        ]);
        assert!(own_layer_mapped(&r, 7).unwrap());
        assert!(!own_layer_mapped(&r, 8).unwrap());
        assert!(!own_layer_mapped(&r, 9).unwrap());
    }

    #[test]
    fn tells_clicks_outside_the_surface() {
        assert!(!outside(0.0, 0.0, 760, 232));
        assert!(!outside(759.5, 231.9, 760, 232));
        assert!(outside(-834.0, -439.0, 760, 232));
        assert!(outside(760.0, 10.0, 760, 232));
        assert!(outside(10.0, 232.0, 760, 232));
        assert!(outside(f64::NAN, 10.0, 760, 232));
    }

    #[test]
    fn finds_the_focused_monitor() {
        let mut r = script(vec!["[]".into()]);
        r.monitors = MONITORS.replace(r#""id":2,"#, r#""id":2,"focused":true,"#);
        assert_eq!(focused_monitor(&r).unwrap().id, 2);
        r.monitors = MONITORS.into();
        assert!(focused_monitor(&r).is_err());
    }

    #[test]
    fn targets_only_our_client_through_a_window_object() {
        let pid = 4242;
        let r = script(vec![format!(
            "[{},{},{}]",
            client(1, "soft-launcher", ""),
            client(pid, "kitty", ""),
            client(pid, "soft-launcher", "")
        )]);
        let p = place(&r, pid, || 300, 1, || {}, go).unwrap().unwrap();
        assert_eq!(p.address, format!("0xabc{pid}"));
        assert_eq!((p.x, p.y, p.width, p.height), (3460, 436, 760, 300));
        let lua = &r.dispatches()[0];
        assert!(lua.starts_with("function() "));
        assert!(lua.contains(&format!("hl.get_window(\"address:0xabc{pid}\")")));
        assert!(lua.contains(&format!("w.pid ~= {pid}")));
        assert!(lua.contains("x = 760, y = 300, relative = false, window = w"));
        assert!(lua.contains("x = 3460, y = 436, relative = false, window = w"));
        // Every dispatcher gets the resolved window object, never the active window.
        assert_eq!(
            lua.matches("hl.dsp.window.").count(),
            lua.matches("window = w }").count()
        );
        assert!(!lua.contains("activewindow") && !lua.contains("center("));
    }

    #[test]
    fn handles_groups_and_maximise_inside_the_lua() {
        let lua = lua(
            &serde_json::from_str::<Client>(&client(7, "soft-launcher", "")).unwrap(),
            7,
            1,
            2,
            MIN_HEIGHT,
        );
        assert!(lua.contains("if w.group then hl.dispatch(hl.dsp.window.move({ out_of_group = true, window = w })) end"));
        assert!(lua.contains(
            "fullscreen_state({ internal = 0, client = 0, action = \"set\", window = w })"
        ));
        assert!(lua.contains("float({ action = \"enable\", window = w })"));
    }

    #[test]
    fn waits_for_map_then_places() {
        let r = script(vec![
            "[]".into(),
            format!("[{}]", client(9, "soft-launcher", ""))
                .replace(r#""mapped":true"#, r#""mapped":false"#),
            format!("[{}]", client(9, "soft-launcher", "")),
        ]);
        let waits = Mutex::new(0);
        assert!(
            place(&r, 9, min, 5, || *waits.lock().unwrap() += 1, go)
                .unwrap()
                .is_some()
        );
        assert_eq!(*waits.lock().unwrap(), 2);
    }

    #[test]
    fn abandons_quietly_when_superseded() {
        let r = script(vec!["[]".into()]);
        let polls = Mutex::new(0);
        let result = place(
            &r,
            1,
            min,
            10,
            || {},
            || {
                *polls.lock().unwrap() += 1;
                *polls.lock().unwrap() < 3
            },
        );
        assert_eq!(result, Ok(None));
        assert!(r.dispatches().is_empty());
    }

    #[test]
    fn reports_unmapped_and_rejected_dispatches() {
        let r = script(vec!["[]".into()]);
        assert!(
            place(&r, 1, min, 3, || {}, go)
                .unwrap_err()
                .contains("not mapped")
        );
        let mut r = script(vec![format!("[{}]", client(3, "soft-launcher", ""))]);
        r.reply = "warning: =[C]:-1: Window is fullscreen\n".into();
        let err = place(&r, 3, min, 1, || {}, go).unwrap_err();
        assert!(
            err.contains("rejected") && err.contains("Window is fullscreen"),
            "{err}"
        );
    }

    #[test]
    fn skips_dispatch_when_already_placed() {
        let r = script(vec![format!(
            "[{}]",
            client(5, "soft-launcher", "")
                .replace(r#""floating":false"#, r#""floating":true"#)
                .replace(
                    r#""at":[0,0],"size":[1268,1396]"#,
                    r#""at":[3460,436],"size":[760,420]"#
                )
        )]);
        assert!(
            place(&r, 5, || 420, 1, || {}, go)
                .unwrap()
                .unwrap()
                .unchanged
        );
        assert!(r.dispatches().is_empty());
        // A new content height is a new placement, even at the same position.
        assert!(
            !place(&r, 5, || 480, 1, || {}, go)
                .unwrap()
                .unwrap()
                .unchanged
        );
        assert_eq!(r.dispatches().len(), 1);
    }

    #[test]
    fn clamps_requested_height() {
        let r = script(vec![format!("[{}]", client(6, "soft-launcher", ""))]);
        let p = place(&r, 6, || 9000, 1, || {}, go).unwrap().unwrap();
        assert_eq!((p.width, p.height), (WIDTH, MAX_HEIGHT));
        assert!(r.dispatches()[0].contains(&format!("x = {WIDTH}, y = {MAX_HEIGHT}, relative")));
    }

    #[test]
    fn respects_opt_out_and_needs_hyprland() {
        use std::ffi::OsString;
        let env = |pairs: &'static [(&'static str, &'static str)]| {
            move |n: &str| {
                pairs
                    .iter()
                    .find(|(k, _)| *k == n)
                    .map(|(_, v)| OsString::from(v))
            }
        };
        assert!(wanted(env(&[("HYPRLAND_INSTANCE_SIGNATURE", "x")])));
        assert!(!wanted(env(&[])));
        assert!(!wanted(env(&[
            ("HYPRLAND_INSTANCE_SIGNATURE", "x"),
            ("SOFT_LAUNCHER_NO_PLACEMENT", "0")
        ])));
    }
}
