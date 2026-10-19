#!/usr/bin/env python3
"""Red-capable probe of the installed launcher through the real Super+Space binding.

Presses Super+Space on a short-lived uinput keyboard (or, with --trigger exec, runs
the bound dispatcher through hyprctl), then records the launcher's window lifecycle
from Hyprland's event socket and from polling `hyprctl clients`. The pointer is
never moved. Records only addresses, workspace and monitor ids, geometry and
timings; window titles are dropped.

The launcher is either a toplevel window (until the layer-shell fix) or a
layer-shell overlay surface (namespace soft-launcher). Asserts that it
  - appears on the monitor (and, for a window, the workspace) where the key was pressed,
  - holds keyboard focus: a window stays the active window; for a layer surface no
    other window becomes active,
  - stays shown for --hold seconds, also with --nudge, which moves the pointer 25 px
    outside the launcher 700 ms after the press like a resting hand (the run-2 red),
  - and that the active workspace never changes.
Afterwards a second press must hide it and return focus to the original window.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

CLASS = "soft-launcher"
SERVICE = "soft-launcher.service"
KEEP_EVENTS = {
    "openwindow", "closewindow", "activewindowv2", "workspacev2", "focusedmonv2",
    "movewindowv2", "changefloatingmode", "fullscreen", "moveworkspacev2",
    "activespecialv2",
}


def hypr(*args, j=True):
    out = subprocess.check_output(["hyprctl", *(["-j"] if j else []), *args], timeout=2)
    return json.loads(out) if j else out.decode()


def cursor():
    x, y = hypr("cursorpos", j=False).strip().split(",")
    return [int(x), int(y.strip())]


def service_pid():
    out = subprocess.check_output(
        ["systemctl", "--user", "show", SERVICE, "-p", "MainPID", "--value"], timeout=2)
    return int(out.strip())


def clean(event, data):
    """Keep ids only. openwindow carries class and title; keep the class only for ours."""
    parts = data.split(",")
    if event == "openwindow":
        cls = parts[2] if len(parts) > 2 else ""
        return {"address": "0x" + parts[0], "workspace": parts[1],
                "ours": cls.lower() == CLASS}
    if event in ("activewindowv2", "closewindow", "changefloatingmode", "fullscreen"):
        return {"address": ("0x" + parts[0]) if parts[0] else "", "rest": parts[1:2]}
    if event == "movewindowv2":
        return {"address": "0x" + parts[0], "workspace": parts[1]}
    if event in ("workspacev2", "focusedmonv2", "moveworkspacev2", "activespecialv2"):
        return {"ids": parts[:1] if event != "focusedmonv2" else parts[1:2]}
    return {}


class Recorder:
    def __init__(self, pid, t0):
        self.pid, self.t0 = pid, t0
        self.events, self.samples = [], []
        self.stop = threading.Event()

    def now(self):
        return round((time.monotonic() - self.t0) * 1000, 1)

    def listen(self):
        path = Path(os.environ["XDG_RUNTIME_DIR"], "hypr",
                    os.environ["HYPRLAND_INSTANCE_SIGNATURE"], ".socket2.sock")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(path))
        s.settimeout(0.1)
        buf = b""
        while not self.stop.is_set():
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                continue
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for line in lines:
                event, _, data = line.decode(errors="replace").partition(">>")
                if event in KEEP_EVENTS:
                    self.events.append({"ms": self.now(), "event": event, **clean(event, data)})
        s.close()

    def poll(self):
        while not self.stop.is_set():
            ms = self.now()
            try:
                ours = [c for c in hypr("clients")
                        if c.get("pid") == self.pid and c.get("class", "").lower() == CLASS]
                ours += own_layers(self.pid)
                active = hypr("activewindow").get("address", "")
                ws = hypr("activeworkspace")
                sample = {
                    "ms": ms,
                    "launcher": [{"address": c["address"], "mapped": c["mapped"],
                                  "hidden": c["hidden"], "workspace": c["workspace"] and c["workspace"]["id"],
                                  "monitor": c["monitor"], "at": c["at"], "size": c["size"],
                                  "floating": c["floating"], "layer": c.get("layer", False)}
                                 for c in ours],
                    "active": active,
                    "workspace": ws["id"],
                    "monitor": ws["monitorID"],
                    "cursor": cursor(),
                }
            except Exception as e:  # noqa: BLE001 - recorded, not fatal
                sample = {"ms": ms, "error": str(e)[:120]}
            self.samples.append(sample)
            time.sleep(0.02)


def own_layers(pid):
    """Our layer surfaces, shaped like clients. Monitor names map to ids."""
    ids = {m["name"]: m["id"] for m in hypr("monitors")}
    found = []
    for name, mon in hypr("layers").items():
        for level in mon.get("levels", {}).values():
            for l in level:
                if l.get("pid") == pid and l.get("namespace") == CLASS:
                    found.append({"address": l["address"], "mapped": True, "hidden": False,
                                  "workspace": None, "monitor": ids.get(name),
                                  "at": [l["x"], l["y"]], "size": [l["w"], l["h"]],
                                  "floating": None, "layer": True, "class": CLASS,
                                  "pid": pid})
    return found


def shown(pid):
    return any(c.get("pid") == pid and c.get("mapped") and not c.get("hidden")
               and c.get("class", "").lower() == CLASS for c in hypr("clients")) \
        or bool(own_layers(pid))


class Keyboard:
    """A uinput keyboard that exists only for this probe."""

    def __init__(self):
        from evdev import UInput, ecodes as e
        self.e = e
        # Real relative motion: a hyprctl cursor warp does not trigger follow_mouse.
        self.mouse = UInput({e.EV_KEY: [e.BTN_LEFT], e.EV_REL: [e.REL_X, e.REL_Y]},
                            name="soft-launcher-probe-mouse")
        time.sleep(0.5)
        self.ui = UInput({e.EV_KEY: [e.KEY_LEFTMETA, e.KEY_SPACE, e.KEY_ESC]},
                         name="soft-launcher-probe")
        time.sleep(1.0)  # let libinput and Hyprland pick the devices up

    def nudge(self, steps=5, dx=5, dy=1):
        for _ in range(steps):
            self.mouse.write(self.e.EV_REL, self.e.REL_X, dx)
            self.mouse.write(self.e.EV_REL, self.e.REL_Y, dy)
            self.mouse.syn()
            time.sleep(0.016)

    def chord(self, *keys):
        for k in keys:
            self.ui.write(self.e.EV_KEY, k, 1)
            self.ui.syn()
            time.sleep(0.02)
        for k in reversed(keys):
            self.ui.write(self.e.EV_KEY, k, 0)
            self.ui.syn()
            time.sleep(0.02)

    def hotkey(self):
        self.chord(self.e.KEY_LEFTMETA, self.e.KEY_SPACE)

    def escape(self):
        self.chord(self.e.KEY_ESC)

    def close(self):
        self.ui.close()
        self.mouse.close()


def trigger(kind, kb):
    if kind == "key":
        kb.hotkey()
    else:
        cmd = 'function() hl.dispatch(hl.dsp.exec_cmd(' + json.dumps(str(Path.home() / '.local/lib/soft-launcher/toggle')) + ')) end'
        subprocess.run(["hyprctl", "dispatch", cmd], check=True, timeout=2,
                       stdout=subprocess.DEVNULL)


def launcher_visible(sample):
    return any(c["mapped"] and not c["hidden"] for c in sample.get("launcher", []))


def analyse(pre, rec, hold_ms, nudged):
    samples = [s for s in rec.samples if "error" not in s]
    shown = [s for s in samples if launcher_visible(s)]
    checks = {}
    first = shown[0] if shown else None
    checks["maps_within_1500ms"] = bool(first and first["ms"] <= 1500)
    if not first:
        return checks, {}
    win = next(c for c in first["launcher"] if c["mapped"])
    addr = win["address"]
    layer = win.get("layer", False)
    after = [s for s in samples if s["ms"] >= first["ms"]]
    # First sample at which it is no longer visible, or no longer active.
    gone = next((s["ms"] for s in after if not launcher_visible(s)), None)
    # A layer surface takes the keyboard without becoming Hyprland's active window;
    # any *change* of active window would mean focus moved elsewhere.
    unfocused = next((s["ms"] for s in after
                      if (s["active"] != pre["active"] if layer else s["active"] != addr)
                      and s["ms"] > first["ms"] + 300), None)
    workspaces = sorted({c["workspace"] for s in shown for c in s["launcher"] if c["mapped"]})
    monitors = sorted({c["monitor"] for s in shown for c in s["launcher"] if c["mapped"]})
    checks["same_workspace"] = workspaces == ([None] if layer else [pre["workspace"]])
    checks["same_monitor"] = monitors == [pre["monitor"]]
    checks["becomes_active"] = layer or any(s["active"] == addr for s in after)
    checks["stays_visible_hold"] = gone is None
    checks["stays_active_hold"] = unfocused is None
    checks["active_workspace_unchanged"] = all(s["workspace"] == pre["workspace"] for s in samples)
    if not nudged:
        checks["pointer_unchanged"] = all(s["cursor"] == pre["cursor"] for s in samples)
    last = after[-1]
    lw = next((c for c in last["launcher"] if c["mapped"]), None)
    facts = {
        "address": addr,
        "surface": "layer" if layer else "window",
        "first_visible_ms": first["ms"],
        "first_geometry": {"at": win["at"], "size": win["size"], "floating": win["floating"]},
        "final_geometry": lw and {"at": lw["at"], "size": lw["size"], "floating": lw["floating"]},
        "launcher_workspaces": workspaces,
        "launcher_monitors": monitors,
        "hidden_at_ms": gone,
        "lost_active_at_ms": unfocused,
        "active_after_loss": sorted({s["active"] == pre["active"] and "original" or
                                     (s["active"] or "none") for s in after
                                     if s["active"] != addr}),
    }
    return checks, facts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trigger", choices=["key", "exec"], default="key")
    ap.add_argument("--hold", type=float, default=5.0)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--no-cleanup", action="store_true")
    ap.add_argument("--nudge", action="store_true",
                    help="move the pointer 25 px outside the launcher 700 ms after the press")
    args = ap.parse_args()

    pid = service_pid()
    kb = Keyboard() if args.trigger == "key" or args.nudge else None
    pre_ws = hypr("activeworkspace")
    pre = {"workspace": pre_ws["id"], "monitor": pre_ws["monitorID"],
           "active": hypr("activewindow").get("address", ""), "cursor": cursor()}
    if shown(pid):
        sys.exit("launcher already visible; refusing to toggle it closed as a 'show'")

    t0 = time.monotonic()
    started = {"utc": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) +
               f".{int(time.time() * 1000) % 1000:03d}Z", "monotonic": round(t0, 3)}
    rec = Recorder(pid, t0)
    threads = [threading.Thread(target=rec.listen, daemon=True),
               threading.Thread(target=rec.poll, daemon=True)]
    for t in threads:
        t.start()
    time.sleep(0.15)
    rec.events.append({"ms": rec.now(), "event": "probe:trigger"})
    trigger(args.trigger, kb)
    if args.nudge:
        time.sleep(0.7)
        kb.nudge()
        rec.events.append({"ms": rec.now(), "event": "probe:nudge", "to": cursor()})
        time.sleep(max(0.0, args.hold - 0.7))
    else:
        time.sleep(args.hold)
    rec.stop.set()
    for t in threads:
        t.join(1)
    checks, facts = analyse(pre, rec, args.hold * 1000, args.nudge)

    cleanup = {}
    if not args.no_cleanup:
        visible = shown(pid)
        cleanup["visible_before_cleanup"] = visible
        if visible:
            trigger(args.trigger, kb)
            time.sleep(0.6)
            cleanup["hidden_by_second_hotkey"] = not shown(pid)
        if args.nudge:
            x, y = pre["cursor"]
            subprocess.run(["hyprctl", "dispatch",
                            f"function() hl.dispatch(hl.dsp.cursor.move({{x={x},y={y}}})) end"],
                           check=True, timeout=2, stdout=subprocess.DEVNULL)
            time.sleep(0.2)
        cleanup["focus_restored"] = hypr("activewindow").get("address", "") == pre["active"]
        cleanup["workspace_restored"] = hypr("activeworkspace")["id"] == pre["workspace"]
    if kb:
        kb.close()

    result = {
        "started": started,
        "trigger": args.trigger,
        "service_pid": pid,
        "pre": {**pre, "active": "original"},
        "pre_active_address": pre["active"],
        "checks": checks,
        "facts": facts,
        "cleanup": cleanup,
        "events": rec.events,
        "samples": rec.samples,
        "pass": bool(checks) and all(checks.values()),
    }
    text = json.dumps(result, indent=1)
    if args.output:
        args.output.write_text(text)
    summary = {k: result[k] for k in ("started", "pre", "checks", "facts", "cleanup", "pass")}
    print(json.dumps(summary, indent=1))
    print("events:", json.dumps(rec.events))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
