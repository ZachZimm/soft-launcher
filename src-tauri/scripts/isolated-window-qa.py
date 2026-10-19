#!/usr/bin/env python3
"""Window-behaviour QA for the release binary inside a hidden agent workspace.

Everything runs on the workspace's own X display with a private D-Bus and runtime
directory; host Wayland/Hyprland variables are removed. Input and screenshots go
through the workspace CLI only. It never touches the host display, focus or
compositor. Hyprland-specific placement is skipped here (no HYPRLAND_INSTANCE_SIGNATURE).

Checks: --hidden maps nothing; the hotkey command (--toggle) shows and focuses the
window; it does not dismiss itself after mapping; it is opaque and 760 px wide;
settings grow it without dismissing; a real click on another window dismisses it and
leaves that window focused; Escape and a second toggle dismiss; --hidden while
hidden stays hidden.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from PIL import Image

CLI = Path.home() / ".local/bin/agent-workspace-linux"
ROOT = Path(__file__).resolve().parents[2]
WIDTH, MIN_HEIGHT, MAX_HEIGHT = 760, 232, 600
BACKGROUND = (0x28, 0x28, 0x28)


def cli(*args, check=True):
    out = subprocess.run([str(CLI), "workspace", *args], capture_output=True, text=True, timeout=60)
    data = json.loads(out.stdout or "{}")
    if check and not data.get("ok"):
        raise RuntimeError(f"{args[0]} failed: {data.get('message')}")
    return data


class Workspace:
    def __init__(self, wid, runtime):
        self.id = wid
        self.runtime = runtime
        self.bus = f"unix:path={runtime}/sl-qa-bus"
        self.env = ["-u", "WAYLAND_DISPLAY", "-u", "HYPRLAND_INSTANCE_SIGNATURE", "-u", "LD_LIBRARY_PATH",
                    f"XDG_RUNTIME_DIR={runtime}", f"DBUS_SESSION_BUS_ADDRESS={self.bus}", "GDK_BACKEND=x11"]

    def launch(self, name, *command, wait_window=False):
        extra = ["--wait-window"] if wait_window else []
        return cli("launch", "--id", self.id, "--name", name, *extra, "--", "env", *self.env, *command)

    def run(self, *command):
        data = cli("run", "--id", self.id, "--timeout-ms", "20000", "--kill-on-timeout", "--", "env", *self.env, *command, check=False)
        if not data.get("launch", {}).get("ok"):
            raise RuntimeError(f"run failed: {data}")
        return data

    def windows(self, cls):
        return [w for w in cli("windows", "--id", self.id).get("windows", [])
                if w.get("wm_instance", "").lower() == cls or w.get("wm_class", "").lower() == cls]

    def launcher(self):
        shown = [w for w in self.windows("soft-launcher") if w.get("visible")]
        return shown[0] if shown else None

    def active(self):
        return (cli("active-window", "--id", self.id).get("active_window") or {})


def wait(pred, timeout=4.0, step=0.05):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        value = pred()
        if value:
            return value, round((time.monotonic() - start) * 1000)
        time.sleep(step)
    return None, round((time.monotonic() - start) * 1000)


def opaque_edges(path):
    image = Image.open(path).convert("RGB")
    w, h = image.size
    inset = 6
    points = [(x, inset) for x in range(20, w - 20, 16)] + [(x, h - 1 - inset) for x in range(20, w - 20, 16)]
    points += [(inset, y) for y in range(20, h - 20, 16)] + [(w - 1 - inset, y) for y in range(20, h - 20, 16)]
    off = [p for p in points if max(abs(a - b) for a, b in zip(image.getpixel(p), BACKGROUND)) > 8]
    return {"size": [w, h], "edgeSamples": len(points), "offBackground": len(off)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="soft-launcher-qa")
    parser.add_argument("--binary", type=Path, default=ROOT / "src-tauri/target/release/soft-launcher")
    parser.add_argument("--out", type=Path, default=ROOT / "src-tauri/visual-qa")
    args = parser.parse_args()

    status = cli("status", "--id", args.workspace, check=False)
    runtime = status["runtime_dir"]
    if not runtime.startswith(f"/run/user/{os.getuid()}/agent-workspace-linux/") or not status.get("ready"):
        raise SystemExit("not an isolated, ready agent workspace")
    ws = Workspace(args.workspace, runtime)
    args.out.mkdir(parents=True, exist_ok=True)
    binary = str(args.binary.resolve())
    catalogue = f"SOFT_LAUNCHER_CATALOGUE={ROOT / 'catalogue.toml'}"
    toggle = ("env", catalogue, binary, "--toggle")
    results, checks, apps = {"binary": binary, "display": status["display"]}, {}, []

    try:
        apps.append(ws.launch("qa-bus", "dbus-daemon", "--session", "--nofork", "--nopidfile", f"--address={ws.bus}")["apps"][0]["id"])
        time.sleep(0.5)
        apps.append(ws.launch("qa-target", "python3", "-I", str(ROOT / "src-tauri/scripts/qa-target-window.py"), wait_window=True)["apps"][0]["id"])
        target = ws.windows("soft-launcher-qa-target")[0]
        cli("move-window", "--id", ws.id, target["id"], "40", "640")
        time.sleep(0.3)
        target = ws.windows("soft-launcher-qa-target")[0]

        # 1. --hidden start maps nothing.
        apps.append(ws.launch("launcher", "env", catalogue, binary, "--hidden")["apps"][0]["id"])
        time.sleep(3.0)
        checks["hiddenStartNoWindow"] = ws.launcher() is None

        # 2. The hotkey's command shows and focuses the window.
        ws.run(*toggle)
        shown, ms = wait(lambda: ws.launcher() if ws.active().get("wm_instance", "").lower() == "soft-launcher" else None)
        results["toggleShowMs"] = ms
        checks["toggleShowsAndFocuses"] = bool(shown)

        # 3. No self-dismissal after mapping.
        time.sleep(1.5)
        checks["staysAfterMap"] = ws.launcher() is not None

        # 4. Opaque, 760 wide, card result before any action.
        cli("type", "--id", ws.id, "2+2")
        time.sleep(3.0)
        win = ws.launcher()
        results["calcGeometry"] = win and win["geometry"]
        shot = args.out / "isolated-calc.png"
        cli("screenshot-window", "--id", ws.id, "--window", win["id"], "--output", str(shot))
        results["calcEdges"] = opaque_edges(shot)
        checks["width760"] = abs(win["geometry"]["width"] - WIDTH) <= 2
        checks["heightInRange"] = MIN_HEIGHT - 2 <= win["geometry"]["height"] <= MAX_HEIGHT + 2
        checks["opaqueEdges"] = results["calcEdges"]["offBackground"] == 0

        # 5. Settings grow the window (fit_overlay) without dismissing it.
        cli("key", "--id", ws.id, "ctrl+comma")
        time.sleep(1.0)
        win = ws.launcher()
        results["settingsGeometry"] = win and win["geometry"]
        checks["settingsKeepsOpen"] = win is not None
        checks["settingsGrows"] = bool(win) and win["geometry"]["height"] > results["calcGeometry"]["height"]
        if win:
            cli("screenshot-window", "--id", ws.id, "--window", win["id"], "--output", str(args.out / "isolated-settings.png"))
        cli("key", "--id", ws.id, "Escape")
        time.sleep(0.6)
        checks["escapeClosesSettingsOnly"] = ws.launcher() is not None

        # 6. A real click on another window dismisses and leaves that window focused.
        tg = target["geometry"]
        cli("click", "--id", ws.id, str(tg["x"] + tg["width"] // 2), str(tg["y"] + tg["height"] // 2))
        gone, ms = wait(lambda: ws.launcher() is None, timeout=2.0)
        results["clickOutsideHideMs"] = ms
        checks["clickOutsideDismisses"] = bool(gone)
        time.sleep(0.3)
        checks["clickedWindowKeepsFocus"] = ws.active().get("id") == target["id"]

        # 7. Escape dismisses.
        ws.run(*toggle)
        wait(lambda: ws.launcher())
        time.sleep(0.5)
        cli("key", "--id", ws.id, "Escape")
        gone, _ = wait(lambda: ws.launcher() is None, timeout=2.0)
        checks["escapeDismisses"] = bool(gone)

        # 8. A second toggle dismisses.
        ws.run(*toggle)
        wait(lambda: ws.launcher())
        time.sleep(0.5)
        ws.run(*toggle)
        gone, _ = wait(lambda: ws.launcher() is None, timeout=2.0)
        checks["secondToggleDismisses"] = bool(gone)

        # 9. --hidden while running and hidden is a no-op.
        ws.run("env", catalogue, binary, "--hidden")
        time.sleep(1.0)
        checks["hiddenInvocationNoop"] = ws.launcher() is None
    finally:
        for app in reversed(apps):
            cli("kill-app", "--id", ws.id, app, check=False)

    results["checks"] = checks
    results["passed"] = all(checks.values())
    (args.out / "isolated-x11.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    raise SystemExit(0 if results["passed"] else 1)


if __name__ == "__main__":
    main()
