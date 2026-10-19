#!/usr/bin/env python3
"""Check that the visible launcher window is a compact, opaque panel.

Reads the running launcher's Hyprland geometry, captures only that window's
rectangle with grim, and samples pixels just inside the window edge. A transparent
window lets the compositor's blurred desktop through, so those samples vary and
do not match the panel background. Never launches, restarts or invokes anything.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys

from PIL import Image

# Must match WIDTH and the height limits in src-tauri/src/overlay.rs and --bg in
# src/styles/app.css (native mode paints the whole window with it).
WIDTH = 760
MIN_HEIGHT = 232
MAX_HEIGHT = 600
BACKGROUND = (0x28, 0x28, 0x28)
# Hyprland draws rounded corners and a border; stay clear of both.
INSET = 10
TOLERANCE = 8


def hypr(command):
    return json.loads(subprocess.check_output(["hyprctl", "-j", command], timeout=2))


def launcher():
    shown = [c for c in hypr("clients") if c.get("class", "").lower() == "soft-launcher" and c.get("mapped") and not c.get("hidden")]
    if len(shown) != 1:
        raise SystemExit(f"expected one visible soft-launcher window, found {len(shown)}")
    return shown[0]


def edge_samples(image):
    w, h = image.size
    points = []
    for x in range(INSET * 2, w - INSET * 2, 16):
        points += [(x, INSET), (x, h - 1 - INSET)]
    for y in range(INSET * 2, h - INSET * 2, 16):
        points += [(INSET, y), (w - 1 - INSET, y)]
    return [(p, image.getpixel(p)[:3]) for p in points]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, default=Path("/tmp/soft-launcher-visual.png"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    client = launcher()
    (x, y), (w, h) = client["at"], client["size"]
    subprocess.run(["grim", "-g", f"{x},{y} {w}x{h}", str(args.capture)], check=True, timeout=5)
    image = Image.open(args.capture).convert("RGB")

    samples = edge_samples(image)
    off = [(p, c) for p, c in samples if max(abs(a - b) for a, b in zip(c, BACKGROUND)) > TOLERANCE]
    checks = {
        "floating": bool(client.get("floating")),
        "width": abs(w - WIDTH) <= 2,
        "heightInRange": MIN_HEIGHT - 2 <= h <= MAX_HEIGHT + 2,
        # Edge pixels are the panel background: no transparent margin around the panel.
        "opaqueEdges": len(off) <= len(samples) // 50,
    }
    result = {
        "size": [w, h],
        "at": [x, y],
        "expected": {"width": WIDTH, "height": [MIN_HEIGHT, MAX_HEIGHT], "background": "#%02x%02x%02x" % BACKGROUND},
        "edgeSamples": len(samples),
        "edgeSamplesOffBackground": len(off),
        "edgeMedian": [statistics.median(c[i] for _, c in samples) for i in range(3)] if samples else None,
        "firstOff": [{"at": list(p), "rgb": list(c)} for p, c in off[:5]],
        "checks": checks,
        "passed": all(checks.values()),
        "capture": str(args.capture),
    }
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(text + "\n")
    print(text)
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
