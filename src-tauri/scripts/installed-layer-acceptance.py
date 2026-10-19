#!/usr/bin/env python3
"""RETIRED host-input acceptance script. Never run against a desktop.

Global input cannot be scoped by a private D-Bus or display. Both the CLI and
Devices constructor refuse unconditionally. The retained Run methods exist for
nonvisual regression tests and historical evidence only. New GUI acceptance must
use the standalone isolated harness with private display/bus verification and
socket-scoped input. See VALIDATION.md.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("probe", HERE / "host-binding-probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
hypr, cursor, own_layers, service_pid = probe.hypr, probe.cursor, probe.own_layers, probe.service_pid

TARGET = r'''
import time, gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
GLib.set_prgname("sl-qa-target")  # must not contain "soft-launcher": the app skips those as foreground
def log(*a): print(round(time.monotonic(), 3), *a, flush=True)
w = Gtk.Window(title="qa target"); w.set_size_request(420, 260); w.set_resizable(False)
w.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK)
w.connect("button-press-event", lambda *a: log("click") or False)
w.connect("key-press-event", lambda w_, ev: log("key", Gdk.keyval_name(ev.keyval)) or False)
w.connect("focus-in-event", lambda *a: log("focus-in") or False)
w.connect("focus-out-event", lambda *a: log("focus-out") or False)
w.add(Gtk.Label(label="soft-launcher QA target")); w.connect("destroy", Gtk.main_quit)
w.show_all(); GLib.timeout_add_seconds(120, Gtk.main_quit); Gtk.main()
'''


def dispatch(lua):
    out = subprocess.check_output(["hyprctl", "dispatch", f"function() {lua} end"], timeout=2)
    if out.decode().strip() != "ok":
        raise RuntimeError(out.decode()[:200])


def warp(x, y):
    dispatch(f"hl.dispatch(hl.dsp.cursor.move({{x={x},y={y}}}))")


def refuse_host_input():
    raise RuntimeError(
        "retired host-input harness: global input is forbidden; use "
        "run-hermes-desktop-isolated-acceptance.py with verified private display/bus")


class Devices:
    """Retired. There is deliberately no global input implementation here."""

    def __init__(self):
        refuse_host_input()


def utc():
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"


class Run:
    def __init__(self, shots, pid=None, toggle_cmd=None, enter=False):
        self.enter = enter
        self.pid = pid or service_pid()
        self.toggle_cmd = toggle_cmd
        self.shots = shots
        self.steps = []
        self.dev = Devices()
        if toggle_cmd:  # a pre-install copy on a private bus, not the binding
            self.dev.hotkey = lambda: subprocess.run(toggle_cmd, shell=True, check=True, timeout=5)
        self.target = None
        self.tlog = []

    def tdrain(self):
        while True:
            line = self.target.stdout.readline()
            if not line:
                return self.tlog
            self.tlog.append(line.split(None, 1)[1].strip())

    def layer(self):
        found = own_layers(self.pid)
        return found[0] if found else None

    def toplevels(self):
        return [c["address"] for c in hypr("clients")
                if c.get("pid") == self.pid and c.get("mapped")]

    def drawn(self, layer=None):
        """The overlay is really on screen: its rectangle shows the opaque panel with
        the accent outline. Sampled in memory; nothing is written. A layer surface is
        listed by hyprctl before it has a buffer, so presence alone is not enough."""
        from io import BytesIO
        from PIL import Image
        layer = layer or self.layer()
        if not layer:
            return False
        (x, y), (w, h) = layer["at"], layer["size"]
        png = subprocess.run(["grim", "-g", f"{x},{y} {w}x{h}", "-"], capture_output=True,
                             check=True, timeout=5).stdout
        im = Image.open(BytesIO(png)).convert("RGB")
        w, h = im.size
        near = lambda p, c: max(abs(a - b) for a, b in zip(im.getpixel(p), c)) <= 12
        inside = [(px, py) for px in range(20, w - 20, 40) for py in (8, h - 9)]
        edge = [(0, h // 2), (w - 1, h // 2), (w // 2, 0), (w // 2, h - 1)]
        return sum(near(p, (0x28, 0x28, 0x28)) for p in inside) >= 0.9 * len(inside) \
            and all(near(p, (0xA8, 0xB6, 0x65)) for p in edge)

    def wait_layer(self, present, timeout=1.5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            layer = self.layer()
            if (bool(layer) and self.drawn(layer)) if present else not layer:
                return True
            time.sleep(0.03)
        return False

    def shot(self, name):
        layer = self.layer()
        if not (self.shots and layer and self.drawn(layer)):
            return None
        (x, y), (w, h) = layer["at"], layer["size"]
        path = self.shots / f"installed-{name}.png"
        subprocess.run(["grim", "-g", f"{x},{y} {w}x{h}", str(path)], check=True, timeout=5)
        return path.name

    def step(self, name, **checks):
        self.steps.append({"step": name, "pass": all(v for v in checks.values()
                                                     if isinstance(v, bool)), **checks})
        print(json.dumps(self.steps[-1]), flush=True)

    def setup_target(self):
        self.p0 = cursor()
        self.a0 = hypr("activewindow").get("address", "")
        self.target = subprocess.Popen(["python3", "-c", TARGET], text=True,
                                       env={**os.environ, "GDK_BACKEND": "wayland"},
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.set_blocking(self.target.stdout.fileno(), False)
        end = time.monotonic() + 3
        t = None
        while time.monotonic() < end and not t:
            t = next((c for c in hypr("clients") if c["pid"] == self.target.pid and c["mapped"]), None)
            time.sleep(0.05)
        if not t:
            raise RuntimeError("target did not map")
        self.taddr = t["address"]
        ws = hypr("activeworkspace")
        mon = next(m for m in hypr("monitors") if m["id"] == ws["monitorID"])
        self.mon, self.ws = mon["id"], ws["id"]
        tx, ty = mon["x"] + mon["reserved"][0] + 60, mon["y"] + mon["reserved"][1] + 60
        dispatch(f'local w = hl.get_window("address:{self.taddr}"); '
                 f'if not w.floating then hl.dispatch(hl.dsp.window.float({{action="enable", window=w}})) end; '
                 f'hl.dispatch(hl.dsp.window.move({{x={tx}, y={ty}, relative=false, window=w}}))')
        time.sleep(0.2)
        self.tcentre = (tx + 150, ty + 120)
        warp(*self.tcentre)
        time.sleep(0.1)
        self.dev.nudge(steps=1, dx=1, dy=0)  # real motion: follow_mouse focuses the target
        time.sleep(0.4)
        self.tdrain()
        if hypr("activewindow").get("address") != self.taddr:
            dispatch(f'hl.dispatch(hl.dsp.focus({{ window = hl.get_window("address:{self.taddr}") }}))')
            time.sleep(0.3)
        return hypr("activewindow").get("address") == self.taddr

    def run_leader(self, name):
        """Enter runs the leading card. Only after a person has checked the shot
        shows the expected card leading: <shots>/enter-<name>.ok (or .skip)."""
        if not (self.enter and self.shots):
            return None
        ok, skip = self.shots / f"enter-{name}.ok", self.shots / f"enter-{name}.skip"
        print(f"waiting for {ok} or {skip}", flush=True)
        end = time.monotonic() + 120
        while time.monotonic() < end and not ok.exists() and not skip.exists():
            time.sleep(0.2)
        if not ok.exists() or not self.drawn():
            return {"executed": False}
        n = len(self.tdrain())
        started = utc()
        self.dev.tap("ENTER")
        time.sleep(1.5)
        self.tdrain()
        return {"executed": True, "utc": started, "still_shown": self.drawn(),
                "target_got_no_keys": self.keys_to_target(n) == 0,
                "shot": self.shot(f"{name}-executed")}

    def keys_to_target(self, since):
        """Typed keys that leaked to the target. Modifier presses are not leaks: a
        bind's modifier (Super) reaches the focused window before the bind fires."""
        mods = ("Super_L", "Super_R", "Shift_L", "Shift_R")
        return sum(1 for l in self.tlog[since:]
                   if l.startswith("key") and l.split()[-1] not in mods)

    def run(self):
        ok = self.setup_target()
        self.step("setup", target_focused=ok, monitor=self.mon, workspace=self.ws, utc=utc())

        # still
        n = len(self.tdrain())
        started = utc()
        self.dev.hotkey()
        shown = self.wait_layer(True)
        time.sleep(1.2)
        self.tdrain()
        layer = self.layer()
        self.step("still", utc=started, shown=shown, still_shown_1200ms=self.drawn(layer),
                  surface_is_layer=bool(layer), on_focused_monitor=bool(layer) and layer["monitor"] == self.mon,
                  geometry=layer and {"at": layer["at"], "size": layer["size"]},
                  no_toplevel=not self.toplevels(),
                  keyboard_left_target="focus-out" in self.tlog[n:],
                  workspace_unchanged=hypr("activeworkspace")["id"] == self.ws)

        # calc
        n = len(self.tdrain())
        started = utc()
        self.dev.type("2+2")
        time.sleep(1.2)
        self.tdrain()
        self.step("calc", utc=started, input="2+2", shown=self.drawn(),
                  target_got_no_keys=self.keys_to_target(n) == 0, shot=self.shot("calc"))
        enter = self.run_leader("calc")
        if enter:
            self.step("calc_enter", **enter)

        # nudge
        n = len(self.tdrain())
        started = utc()
        self.dev.nudge()
        time.sleep(1.5)
        self.tdrain()
        self.step("nudge", utc=started, shown_after_1500ms=self.drawn(),
                  target_not_refocused="focus-in" not in self.tlog[n:], target_events=self.tlog[n:])

        # convert
        n = len(self.tdrain())
        started = utc()
        self.dev.tap("BACKSPACE", 12)  # clears "2+2" and an executed result line
        self.dev.type("5 km to mi")
        time.sleep(1.5)
        self.tdrain()
        self.step("convert", utc=started, input="5 km to mi", shown=self.drawn(),
                  target_got_no_keys=self.keys_to_target(n) == 0, shot=self.shot("convert"))
        enter = self.run_leader("convert")
        if enter:
            self.step("convert_enter", **enter)

        # click inside the panel (the input): the pointer grab must not dismiss it
        layer = self.layer()
        if layer:
            (lx, ly) = layer["at"]
            warp(lx + 200, ly + 35)
            time.sleep(0.15)
            n = len(self.tdrain())
            started = utc()
            self.dev.click()
            time.sleep(0.6)
            self.tdrain()
            self.step("click_inside", utc=started, still_shown=self.drawn(),
                      target_got_nothing=not self.tlog[n:])
            warp(*self.tcentre)
            time.sleep(0.15)

        # escape
        n = len(self.tdrain())
        started = utc()
        # The first Escape dismisses an executed-result panel, the next one the launcher.
        presses = 0
        hidden = False
        while not hidden and presses < 3:
            self.dev.tap("ESC")
            presses += 1
            hidden = self.wait_layer(False, timeout=0.8)
        time.sleep(0.4)
        self.tdrain()
        self.step("escape", utc=started, hidden=hidden, escape_presses=presses,
                  focus_restored=hypr("activewindow").get("address") == self.taddr,
                  pointer_not_warped=cursor() == tuple(self.tcentre) or list(cursor()) == list(self.tcentre),
                  target_focus_in="focus-in" in self.tlog[n:])

        # reopen + second hotkey
        n = len(self.tdrain())
        started = utc()
        self.dev.hotkey()
        shown = self.wait_layer(True)
        time.sleep(1.0)
        self.tdrain()
        took = "focus-out" in self.tlog[n:]
        self.dev.type("7-2")
        time.sleep(0.8)
        self.tdrain()
        reshot = self.shot("reopen")
        m = len(self.tlog)
        self.dev.hotkey()
        hidden = self.wait_layer(False)
        time.sleep(0.4)
        self.tdrain()
        self.step("reopen_toggle", utc=started, shown=shown, keyboard_left_target=took,
                  target_got_no_keys=self.keys_to_target(n) == 0, shot=reshot,
                  hidden_by_hotkey=hidden,
                  focus_restored=hypr("activewindow").get("address") == self.taddr,
                  target_focus_in="focus-in" in self.tlog[m:])

        # click outside, on the target
        n = len(self.tdrain())
        started = utc()
        self.dev.hotkey()
        shown = self.wait_layer(True)
        time.sleep(1.0)
        self.tdrain()
        m = len(self.tlog)
        self.dev.click()
        hidden = self.wait_layer(False, timeout=1.0)
        time.sleep(0.3)
        self.tdrain()
        # The exclusive overlay receives the dismissing click; the window under the
        # pointer does not (recorded, not asserted), then gets focus back.
        self.step("click_outside", utc=started, shown=shown, hidden=hidden,
                  click_passed_to_target="yes" if "click" in self.tlog[m:] else "no",
                  target_active=hypr("activewindow").get("address") == self.taddr)

    def other_monitor(self):
        """Hotkey on another monitor: no typing there, the user's window is underneath."""
        mons = [m for m in hypr("monitors") if m["id"] != self.mon and not m.get("disabled")]
        results = []
        for mon in mons[:2]:
            # Bottom-left corner area of the usable region, away from the centred overlay.
            x = mon["x"] + mon["reserved"][0] + 40
            y = mon["y"] + mon["height"] - mon["reserved"][3] - 80
            warp(x, y)
            time.sleep(0.1)
            self.dev.nudge(steps=1, dx=1, dy=0)
            time.sleep(0.3)
            ws = hypr("activeworkspace")
            before = hypr("activewindow").get("address", "")
            started = utc()
            self.dev.hotkey()
            shown = self.wait_layer(True)
            time.sleep(1.2)
            layer = self.layer()
            layer = layer if self.drawn(layer) else None
            on_mon = bool(layer) and layer["monitor"] == mon["id"]
            ws_same = hypr("activeworkspace")["id"] == ws["id"]
            self.dev.hotkey()
            hidden = self.wait_layer(False)
            time.sleep(0.3)
            self.step(f"monitor_{mon['id']}", utc=started, workspace=ws["id"], shown=shown,
                      still_shown_1200ms=bool(layer), on_that_monitor=on_mon,
                      geometry=layer and {"at": layer["at"], "size": layer["size"]},
                      workspace_unchanged=ws_same, no_toplevel=not self.toplevels(),
                      hidden_by_hotkey=hidden,
                      focus_restored=hypr("activewindow").get("address", "") == before)
            results.append(mon["id"])
        return results

    def cleanup(self):
        """Close owned process/device resources only. Never send desktop input.

        Failed setup may leave no target, no saved focus and no compositor.
        Resource failures must not skip other closes or trigger focus recovery.
        """
        errors = []
        target = getattr(self, "target", None)
        if target is not None:
            try:
                if target.poll() is None:
                    target.terminate()
                    try:
                        target.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        target.kill()
                        target.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                errors.append("owned-target-close-failed")
        dev = getattr(self, "dev", None)
        if dev is not None:
            try:
                dev.close()
            except Exception:
                errors.append("device-close-failed")
        try:
            hidden = not self.layer() and not self.toplevels()
        except Exception:
            hidden = False
            errors.append("launcher-visibility-unavailable")
        return {"launcher_hidden": hidden, "resources_closed": not errors,
                "input_sent": False, "focus_restoration_attempted": False,
                "errors": errors}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shots", type=Path, help="directory for launcher-only screenshots")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--steps", choices=["all", "core"], default="all")
    ap.add_argument("--pid", type=int, help="launcher pid (default: the installed service)")
    ap.add_argument("--toggle-cmd", help="show/hide command instead of Super+Space")
    ap.add_argument("--enter", action="store_true",
                    help="run calc/convert with Enter after a person checks the shot (flag files)")
    args = ap.parse_args()
    refuse_host_input()
    if args.shots:
        args.shots.mkdir(parents=True, exist_ok=True)
    pid = args.pid or service_pid()
    exe = os.readlink(f"/proc/{pid}/exe")
    sha = subprocess.check_output(["sha256sum", exe]).split()[0].decode()
    r = Run(args.shots, pid, args.toggle_cmd, args.enter)
    result = {"service_pid": r.pid, "service_sha256": sha, "started": utc()}
    try:
        r.run()
        if args.steps == "all":
            r.other_monitor()
    finally:
        result["cleanup"] = r.cleanup()
        result["finished"] = utc()
        result["steps"] = r.steps
        result["pass"] = all(s["pass"] for s in r.steps) and all(result["cleanup"].values())
        if args.output:
            args.output.write_text(json.dumps(result, indent=1))
        print(json.dumps({k: result[k] for k in ("service_pid", "service_sha256", "started",
                                                 "finished", "cleanup", "pass")}))


if __name__ == "__main__":
    main()
