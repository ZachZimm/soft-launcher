#!/usr/bin/env python3
"""Installed launcher: one Enter press opens a real app, through Super+Space.

Uses installed-layer-acceptance.py's owned GTK target window and short-lived
uinput devices, so nothing is typed into the user's windows. Records only window
addresses, classes of the launched apps, timings and launcher-only screenshots;
no titles.

Steps:
  sanity    hotkey shows the overlay; Escape hides it and refocuses the target
  brave     hotkey, type "br", Brave is the highlighted card; one Enter press and
            release (no hold) hides the launcher and maps one new Brave window,
            which becomes the active window
  calc      the same with "calculator" for GNOME Calculator
Each launched window is new (the app had no window before) and is closed again
through the compositor; nothing else is touched. Refuses to run if either app
already has a window, so no existing browser session or tab is involved.
"""
import argparse
import importlib.util
import json
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("acceptance", HERE / "installed-layer-acceptance.py")
acc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acc)
hypr, dispatch, utc = acc.hypr, acc.dispatch, acc.utc

APPS = {
    "brave": {"query": "br", "classes": {"brave-browser", "Brave-browser"}, "name": "Brave"},
    "calc": {"query": "calculator", "classes": {"org.gnome.Calculator", "gnome-calculator"}, "name": "Calculator"},
}


def windows(classes):
    return {c["address"]: c for c in hypr("clients") if c.get("class") in classes and c.get("mapped")}


def close_window(address):
    dispatch(f'local w = hl.get_window("address:{address}"); if w then hl.dispatch(hl.dsp.window.close({{window=w}})) end')


class AppRun(acc.Run):
    def launch(self, key, allow_existing=False):
        spec = APPS[key]
        before = windows(spec["classes"])
        # With --allow-existing (Brave already running), the launch must add one new
        # window; only that new window is closed, existing ones are never touched.
        assert allow_existing or not before, f"{spec['name']} already has a window; refusing to touch an existing session"
        n = len(self.tdrain())
        started = utc()
        self.dev.hotkey()
        shown = self.wait_layer(True)
        time.sleep(0.5)
        self.dev.type(spec["query"])
        time.sleep(0.9)
        shot = self.shot(f"app-{key}-typed")
        self.tdrain()
        keys_before_enter = self.keys_to_target(n)
        # One press and release, about 20 ms apart: no hold.
        t0 = time.monotonic()
        self.dev.tap("ENTER")
        hidden = self.wait_layer(False, timeout=2.0)
        hidden_ms = round((time.monotonic() - t0) * 1000)
        new, mapped_ms = None, None
        end = time.monotonic() + 20
        while time.monotonic() < end and not new:
            now = windows(spec["classes"])
            fresh = [a for a in now if a not in before]
            if fresh:
                new = fresh[0]
                mapped_ms = round((time.monotonic() - t0) * 1000)
            else:
                time.sleep(0.05)
        focused_ms = None
        if new:
            end = time.monotonic() + 3
            while time.monotonic() < end:
                if hypr("activewindow").get("address") == new:
                    focused_ms = round((time.monotonic() - t0) * 1000)
                    break
                time.sleep(0.05)
        time.sleep(1.5)
        still_focused = bool(new) and hypr("activewindow").get("address") == new
        self.tdrain()
        count = len(windows(spec["classes"])) - len(before)
        self.step(f"{key}_one_enter", utc=started, shown=shown, shot=shot,
                  target_got_no_typed_keys=keys_before_enter == 0 and self.keys_to_target(n) == 0,
                  launcher_hidden=hidden, hidden_ms=hidden_ms,
                  new_window=bool(new), mapped_ms=mapped_ms, exactly_one_new_window=count == 1,
                  new_window_focused=focused_ms is not None, focused_ms=focused_ms,
                  still_focused_after_1500ms=still_focused)
        closed = None
        if new:
            close_window(new)
            end = time.monotonic() + 10
            while time.monotonic() < end and new in windows(spec["classes"]):
                time.sleep(0.1)
            closed = new not in windows(spec["classes"])
            # Give focus back to the owned target for the next step.
            dispatch(f'hl.dispatch(hl.dsp.focus({{ window = hl.get_window("address:{self.taddr}") }}))')
            time.sleep(0.4)
        left = windows(spec["classes"])
        self.step(f"{key}_closed", closed_new_window_only=bool(closed),
                  existing_windows_untouched=all(a in left for a in before),
                  no_new_windows_left=not [a for a in left if a not in before])

    def look(self, query):
        """Type a query, keep a launcher-only screenshot, Escape twice. Launches nothing."""
        n = len(self.tdrain())
        started = utc()
        self.dev.hotkey()
        shown = self.wait_layer(True)
        time.sleep(0.5)
        self.dev.type(query)
        time.sleep(0.9)
        shot = self.shot(f"app-look-{query.replace(' ', '-')}")
        self.dev.tap("ESC")  # clears the selection/hint level if any, else hides
        hidden = self.wait_layer(False, timeout=0.8)
        if not hidden:
            self.dev.tap("ESC")
            hidden = self.wait_layer(False, timeout=1.0)
        time.sleep(0.4)
        self.tdrain()
        self.step(f"look_{query.replace(' ', '_')}", utc=started, shown=shown, shot=shot, hidden_by_escape=hidden,
                  target_got_no_typed_keys=self.keys_to_target(n) == 0,
                  focus_restored=hypr("activewindow").get("address") == self.taddr)

    def sanity(self):
        n = len(self.tdrain())
        started = utc()
        self.dev.hotkey()
        shown = self.wait_layer(True)
        time.sleep(0.8)
        shot = self.shot("app-sanity-empty")
        self.dev.tap("ESC")
        hidden = self.wait_layer(False, timeout=1.0)
        time.sleep(0.4)
        self.tdrain()
        self.step("sanity", utc=started, shown=shown, shot=shot, hidden_by_escape=hidden,
                  keyboard_left_target="focus-out" in self.tlog[n:],
                  focus_restored=hypr("activewindow").get("address") == self.taddr)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shots", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--apps", default="brave,calc")
    ap.add_argument("--allow-existing", action="store_true", help="the app is already running; only its new window is closed")
    ap.add_argument("--look", default="", help="comma-separated queries to screenshot without launching")
    args = ap.parse_args()
    args.shots.mkdir(parents=True, exist_ok=True)
    pid = acc.service_pid()
    exe = subprocess.check_output(["readlink", f"/proc/{pid}/exe"]).decode().strip()
    sha = subprocess.check_output(["sha256sum", exe]).split()[0].decode()
    r = AppRun(args.shots, pid)
    result = {"service_pid": pid, "service_sha256": sha, "started": utc()}
    try:
        ok = r.setup_target()
        r.step("setup", target_focused=ok, monitor=r.mon, workspace=r.ws, utc=utc())
        r.sanity()
        for query in filter(None, args.look.split(",")):
            r.look(query)
        for key in filter(None, args.apps.split(",")):
            r.launch(key, args.allow_existing)
    finally:
        result["cleanup"] = r.cleanup()
        result["finished"] = utc()
        result["steps"] = r.steps
        # Pointer restoration is recorded, not required (it was best-effort in earlier runs too).
        result["pass"] = all(s["pass"] for s in r.steps) and result["cleanup"]["launcher_hidden"] and result["cleanup"]["focus_restored"]
        args.output.write_text(json.dumps(result, indent=1))
        print(json.dumps({k: result[k] for k in ("service_pid", "service_sha256", "started", "finished", "cleanup", "pass")}))


if __name__ == "__main__":
    main()
