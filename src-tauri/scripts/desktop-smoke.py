#!/usr/bin/env python3
"""Bounded Hyprland show/hide smoke. Never invokes an action or changes config."""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def hypr(command):
    return json.loads(subprocess.check_output(["hyprctl", "-j", command], timeout=2))


def launcher_clients():
    return [c for c in hypr("clients") if "soft-launcher" in c.get("class", "").lower() or "soft-launcher" in c.get("initialClass", "").lower()]


# Must match WIDTH, MIN_HEIGHT and MAX_HEIGHT in src/overlay.rs.
WIDTH, MIN_HEIGHT, MAX_HEIGHT = 760, 232, 600


def overlay_geometry(client, monitors):
    """The window is WIDTH wide, content-tall, horizontally centred, with its top
    edge where a MAX_HEIGHT window would be centred in the usable area."""
    monitor = next((m for m in monitors if m["id"] == client.get("monitor")), None)
    if monitor is None:
        return False, {"error": "monitor unavailable"}
    width, height = monitor["width"], monitor["height"]
    if monitor.get("transform", 0) % 2:
        width, height = height, width
    scale = monitor.get("scale", 1)
    width, height = round(width / scale), round(height / scale)
    left, top, right, bottom = monitor.get("reserved", [0, 0, 0, 0])
    expected = (monitor["x"] + left + (width - left - right - WIDTH) // 2,
                monitor["y"] + top + (height - top - bottom - MAX_HEIGHT) // 2)
    size = client.get("size", [0, 0])
    at = client.get("at", [0, 0])
    error = (at[0] - expected[0], at[1] - expected[1])
    valid = (bool(client.get("floating")) and abs(size[0] - WIDTH) <= 2
             and MIN_HEIGHT - 2 <= size[1] <= MAX_HEIGHT + 2 and all(abs(v) <= 8 for v in error))
    return valid, {"class": client.get("class"), "floating": client.get("floating"), "size": size,
                   "at": at, "anchorErrorPx": list(error), "geometryPassed": valid}


def wait_visible(want, monitors, timeout=3):
    deadline = time.monotonic() + timeout
    last_geometry = None
    while time.monotonic() < deadline:
        mapped = [c for c in launcher_clients() if c.get("mapped", True) and not c.get("hidden", False)]
        if not want and not mapped:
            return None
        if want and mapped:
            valid, last_geometry = overlay_geometry(mapped[0], monitors)
            if valid:
                return last_geometry
        time.sleep(0.005)
    raise RuntimeError(f"launcher visibility/overlay did not become {want}; geometry={last_geometry}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--binary", type=Path, default=root / "target/debug/soft-launcher")
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--startup-timeout", type=float, default=20)
    args = parser.parse_args()
    if not 2 <= args.cycles <= 20:
        parser.error("cycles must be 2..20")
    binary = args.binary.resolve()
    if launcher_clients():
        raise RuntimeError("an existing soft-launcher window is present; close it before this isolated smoke")
    # Do not disturb an existing hidden launcher process either.
    processes = subprocess.run(["pgrep", "-x", "soft-launcher"], capture_output=True, text=True, check=False)
    if processes.returncode == 0:
        raise RuntimeError("an existing soft-launcher process is present; stop it before smoke")
    monitors = hypr("monitors")
    original = hypr("activewindow").get("address")
    with tempfile.TemporaryDirectory(prefix="soft-launcher-smoke-") as task_dir:
        task_path = Path(task_dir)
        catalogue = task_path / "catalogue.toml"
        shutil.copy(root.parent / "catalogue.toml", catalogue)
        env = os.environ.copy()
        env.pop("LD_LIBRARY_PATH", None)
        env["GDK_BACKEND"] = "wayland"
        env["SOFT_LAUNCHER_CATALOGUE"] = str(catalogue)
        log = task_path / "desktop.log"
        with log.open("w") as stream:
            started = time.monotonic()
            process = subprocess.Popen([str(binary)], env=env, stdout=stream, stderr=stream)
            try:
                appearance = wait_visible(True, monitors, timeout=args.startup_timeout)
                startup = (time.monotonic() - started) * 1000
                latencies = []
                for i in range(args.cycles):
                    want = bool(i % 2)
                    start = time.monotonic()
                    toggle = subprocess.Popen([str(binary), "--toggle"], env=env, stdout=stream, stderr=stream)
                    geometry = wait_visible(want, monitors)
                    if geometry is not None:
                        appearance = geometry
                    # Observe mapping before waiting for process exit. Python's
                    # wait(timeout) uses polling that can inflate elapsed time.
                    latencies.append((time.monotonic() - start) * 1000)
                    if toggle.wait(timeout=3) != 0:
                        raise RuntimeError("toggle IPC process failed")
                    # More than one mapped window would indicate single-instance failure.
                    if len(launcher_clients()) > 1:
                        raise RuntimeError("multiple launcher windows detected")
                result = {"startupMs": round(startup, 2), "samplesMs": [round(n, 2) for n in latencies], "p50Ms": round(sorted(latencies)[len(latencies)//2], 2), "p95Ms": round(sorted(latencies)[math.ceil(len(latencies)*.95)-1], 2), "cycles": args.cycles, "singleInstance": True, "actionsExecuted": 0, "window": appearance, "latencyDefinition": "process spawn to compositor visibility and overlay geometry observation"}
                if args.output:
                    args.output.write_text(json.dumps(result, indent=2) + "\n")
                print(json.dumps(result, indent=2))
            except Exception:
                print("\n".join(line for line in log.read_text().splitlines() if any(marker in line for marker in ["wl_display#1.error", "Gdk-Message:", "Error 71", "panicked at"])))
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                if original and original.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in original[2:]):
                    subprocess.run(["hyprctl", "dispatch", f'function() local w=hl.get_window("address:{original}"); if w then hl.dispatch(hl.dsp.focus({{ window=w }})) end end'], check=False, capture_output=True, timeout=2)


if __name__ == "__main__":
    main()
