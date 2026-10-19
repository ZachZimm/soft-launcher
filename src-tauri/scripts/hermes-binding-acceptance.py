#!/usr/bin/env python3
"""RETIRED host-binding Hermes acceptance. CLI refuses before any side effect.

Use run-hermes-desktop-isolated-acceptance.py for explicit isolated GUI acceptance.
Keyboard contains no global input implementation. Retained main/accept seams are
covered by fake-input safety regression, not an authorization to use host input.
See VALIDATION.md for current test scope.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("acceptance", HERE / "installed-layer-acceptance.py")
acc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acc)
hypr, dispatch, utc = acc.hypr, acc.dispatch, acc.utc

TEXT = "Reply exactly: soft-launcher QA ok. Use no tools."
LAB = os.environ.get("SOFT_LAUNCHER_QA_CLEF_HOST", "127.0.0.1:11434")
HERMES_CLASS = "soft-launcher-hermes"


class Keyboard(acc.Devices):
    """Compatibility name only. Construction always refuses global input."""


def descendants(pid):
    out, todo = set(), [pid]
    while todo:
        p = todo.pop()
        try:
            kids = Path(f"/proc/{p}/task/{p}/children").read_text().split()
        except OSError:
            kids = []
        for k in map(int, kids):
            if k not in out:
                out.add(k)
                todo.append(k)
    return out


def lab_connections(pid):
    """Established TCP connections to the lab gateway from the launcher or its WebKit children."""
    pids = {pid} | descendants(pid)
    rows = subprocess.check_output(["ss", "-Htnp", "state", "established"], text=True).splitlines()
    return [r for r in rows if LAB in r and any(f"pid={p}," in r for p in pids)]


def hermes_windows():
    return {c["address"]: c for c in hypr("clients") if c.get("class") == HERMES_CLASS and c.get("mapped")}


def gate(shots, name, timeout=240):
    path = shots / name
    print(f"waiting for {path}", flush=True)
    end = time.monotonic() + timeout
    while time.monotonic() < end and not path.exists():
        time.sleep(0.2)
    return path.read_text().strip() if path.exists() else None


def grab_window(client, path):
    (x, y), (w, h) = client["at"], client["size"]
    subprocess.run(["grim", "-g", f"{x},{y} {w}x{h}", str(path)], check=True, timeout=5)
    return path.name


class HermesRun(acc.Run):
    def require_target(self):
        if getattr(self, "_target_ready", False) is not True:
            raise RuntimeError("QA target setup was not verified")
        target = getattr(self, "target", None)
        if target is None or not any(
                c.get("address") == self.taddr and c.get("pid") == target.pid and c.get("mapped")
                for c in hypr("clients")):
            raise RuntimeError("QA target is not owned and mapped")
        if hypr("activewindow").get("address") != self.taddr:
            raise RuntimeError("QA target lost focus before toggle")

    def require_launcher(self):
        if getattr(self, "_target_ready", False) is not True:
            raise RuntimeError("QA target setup was not verified")
        layer = self.layer()
        if not layer or not self.drawn(layer):
            raise RuntimeError("launcher layer missing or not drawn")
        # A layer surface may leave activewindow pointing to its prior toplevel.
        # Any unrelated toplevel, or a target focus-in after focus-out, aborts.
        if hypr("activewindow").get("address", "") not in ("", self.taddr):
            raise RuntimeError("launcher lost keyboard ownership")
        focus = [event for event in self.tdrain() if event in ("focus-in", "focus-out")]
        if not focus or focus[-1] != "focus-out":
            raise RuntimeError("launcher did not take keyboard from QA target")

    def launcher_input(self, method, *args):
        self.require_launcher()
        return getattr(self.dev, method)(*args)

    def accept(self):
        self.require_target()
        before = hermes_windows()
        if before:
            raise RuntimeError("an owned-class Hermes window already exists; refusing")
        n = len(self.tdrain())
        started = utc()
        self.dev.hotkey()
        shown = self.wait_layer(True)
        if shown is not True:
            raise RuntimeError("launcher layer did not appear; no followup input sent")
        time.sleep(0.6)
        self.launcher_input("chord", self.dev.e.KEY_LEFTCTRL, self.dev.e.KEY_COMMA)
        time.sleep(0.6)
        settings_shot = self.shot("hermes-settings")
        self.launcher_input("tap", "ESC")  # closes settings only
        time.sleep(0.3)
        self.step("show_settings", utc=started, shown=shown, shot=settings_shot)

        typed_at = utc()
        self.launcher_input("type", TEXT)
        lab, decided_ms, t0 = [], None, time.monotonic()
        end = t0 + 190  # lab cold load measured 94 s; budget 180 s
        shot = None
        while time.monotonic() < end:
            lab = lab or lab_connections(self.pid)
            time.sleep(1.0)
            shot = self.shot("hermes-typed")
            if lab and time.monotonic() - t0 > 3:
                decided_ms = round((time.monotonic() - t0) * 1000)
                break
        self.step("typed_lab_decision", utc=typed_at, lab_connection=bool(lab), lab_connection_rows=len(lab),
                  wait_ms=decided_ms, shot=shot, target_got_no_typed_keys=self.keys_to_target(n) == 0)
        keys = gate(self.shots, "select.txt")
        if keys is None:
            raise RuntimeError("no selection instruction")
        for k in keys.split():
            if k == "SHIFTTAB":
                self.launcher_input("chord", self.dev.e.KEY_LEFTSHIFT, self.dev.e.KEY_TAB)
            elif k in ("TAB",):
                self.launcher_input("tap", "TAB")
            elif k == "REFRESH":
                pass
            time.sleep(0.15)
        time.sleep(0.4)
        selected = self.shot("hermes-selected")
        self.step("selected", keys=keys, shot=selected)
        if gate(self.shots, "selected.ok") is None:
            raise RuntimeError("selection not confirmed")

        hold_at = utc()
        self.launcher_input("hold", "ENTER", 300)
        time.sleep(0.8)
        confirm = self.shot("hermes-confirm")
        no_window_yet = not hermes_windows()
        self.step("confirm_shown", utc=hold_at, shot=confirm, no_terminal_before_second_enter=no_window_yet,
                  launcher_shown=self.drawn())
        if gate(self.shots, "confirm.ok") is None:
            raise RuntimeError("confirmation not checked")

        n = len(self.tdrain())
        send_at = utc()
        t0 = time.monotonic()
        self.launcher_input("tap", "ENTER")
        hidden = self.wait_layer(False, timeout=8.0)
        hidden_ms = round((time.monotonic() - t0) * 1000)
        new, mapped_ms = None, None
        end = time.monotonic() + 10
        while time.monotonic() < end and not new:
            fresh = [a for a in hermes_windows() if a not in before]
            if fresh:
                new, mapped_ms = fresh[0], round((time.monotonic() - t0) * 1000)
            time.sleep(0.05)
        client = hermes_windows().get(new) if new else None
        focused = bool(new) and hypr("activewindow").get("address") == new
        self.step("sent", utc=send_at, launcher_hidden=hidden, hidden_ms=hidden_ms, terminal_mapped=bool(new),
                  mapped_ms=mapped_ms, exactly_one_terminal=len(hermes_windows()) == 1, terminal_focused=focused,
                  terminal_pid=client and client.get("pid"), terminal_class=client and client.get("class"),
                  target_got_no_typed_keys=self.keys_to_target(n) == 0)
        if not new:
            return
        shots, i = [], 0
        end = time.monotonic() + 300
        while time.monotonic() < end and not (self.shots / "close.ok").exists():
            client = hermes_windows().get(new)
            if not client:
                break
            shots.append(grab_window(client, self.shots / f"hermes-terminal-{i:02d}.png"))
            i += 1
            time.sleep(4)
        self.step("terminal_observed", shots=shots[-3:], still_open=new in hermes_windows())
        closed_at = utc()
        if new in hermes_windows():
            dispatch(f'local w = hl.get_window("address:{new}"); if w then hl.dispatch(hl.dsp.window.close({{window=w}})) end')
            end = time.monotonic() + 10
            while time.monotonic() < end and new in hermes_windows():
                time.sleep(0.1)
        self.step("closed", utc=closed_at, owned_terminal_closed=new not in hermes_windows(),
                  no_hermes_windows_left=not hermes_windows(), launcher_hidden=not self.layer())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shots", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    acc.refuse_host_input()
    args.shots.mkdir(parents=True, exist_ok=True)
    pid = acc.service_pid()
    exe = subprocess.check_output(["readlink", f"/proc/{pid}/exe"]).decode().strip()
    sha = subprocess.check_output(["sha256sum", exe]).split()[0].decode()
    acc.Devices = Keyboard
    r = HermesRun(args.shots, pid)
    result = {"service_pid": pid, "service_exe": exe, "service_sha256": sha, "started": utc(), "text": TEXT}
    completed = False
    try:
        r._target_ready = False
        ok = r.setup_target()
        r.step("setup", target_focused=ok, monitor=r.mon, workspace=r.ws, utc=utc())
        if ok is not True:
            raise RuntimeError("QA target setup failed; acceptance aborted without input")
        r._target_ready = True
        r.accept()
        completed = True
    finally:
        result["cleanup"] = r.cleanup()
        result["finished"] = utc()
        result["steps"] = r.steps
        result["pass"] = completed and bool(r.steps) and all(s["pass"] for s in r.steps) and result["cleanup"]["launcher_hidden"] and result["cleanup"].get("resources_closed", False)
        args.output.write_text(json.dumps(result, indent=1))
        print(json.dumps({k: result[k] for k in ("service_pid", "service_sha256", "started", "finished", "cleanup", "pass")}))


if __name__ == "__main__":
    main()
