#!/usr/bin/env python3
"""Minimal GTK3 layer-shell reproduction of the blank first show and the stuck resize.

A GTK3 window made a layer surface while hidden (as tao/Tauri create the launcher),
filled with magenta, is shown, hidden, shown again, grown and shrunk. Reports per
sample how much of the layer rectangle is magenta and the layer size from hyprctl.
Only the probe's own rectangle is sampled, in memory.

    python3 gtk3-layer-repro.py default   # size by default size/resize: first show blank, no resize
    python3 gtk3-layer-repro.py request   # size request + resize (the fix): all shows draw, resizes apply
Add --webkit to use a WebKitGTK view instead of a plain widget (same result).

Loads the bundled ~/.local/lib/soft-launcher/lib/libgtk-layer-shell.so.0 through
ctypes. Sets __NV_DISABLE_EXPLICIT_SYNC=1 like the app; without it NVIDIA re-shows
fail with Wayland protocol error 71.
"""
import json
import os
import subprocess
import sys
import time
from io import BytesIO

CHILD = r'''
import ctypes, sys, time, gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
mode, webkit = sys.argv[1], sys.argv[2] == "webkit"
lib = ctypes.CDLL(sys.argv[3])
def ptr(obj):
    f = ctypes.pythonapi.PyCapsule_GetPointer; f.restype = ctypes.c_void_p
    f.argtypes = [ctypes.py_object, ctypes.c_char_p]; return f(obj.__gpointer__, None)
w = Gtk.Window(); box = Gtk.Box(); w.add(box)
def size(h):
    if mode == "request": w.set_size_request(400, h)
    w.resize(400, h)
if mode == "request": w.set_size_request(400, 150)
else: w.set_default_size(400, 150)
if webkit:
    gi.require_version("WebKit2", "4.1"); from gi.repository import WebKit2
    v = WebKit2.WebView(); v.load_html("<body style='margin:0;background:#f0f;height:100vh'></body>", None)
else:
    v = Gtk.EventBox(); v.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 0, 1, 1))
v.set_hexpand(True); v.set_vexpand(True); box.pack_start(v, True, True, 0)
w.hide()
p = ctypes.c_void_p(ptr(w))
lib.gtk_layer_init_for_window(p); lib.gtk_layer_set_namespace(p, b"soft-launcher-repro")
lib.gtk_layer_set_layer(p, 3); lib.gtk_layer_set_keyboard_mode(p, 0)
steps = [(1.5, w.show_all), (3.0, w.hide), (4.0, w.show_all), (5.5, lambda: size(260)),
         (7.0, lambda: size(150)), (8.5, Gtk.main_quit)]
for t, f in steps:
    GLib.timeout_add(int(t * 1000), lambda f=f: f() and False)
Gtk.main()
'''

PHASES = [(1.5, "first show"), (3.0, "hidden"), (4.0, "second show"), (5.5, "grown to 260"),
          (7.0, "shrunk to 150")]


def sample(pid):
    from PIL import Image
    layers = [l for m in json.loads(subprocess.check_output(["hyprctl", "-j", "layers"])).values()
              for lv in m["levels"].values() for l in lv if l["pid"] == pid]
    if not layers:
        return None
    l = layers[0]
    png = subprocess.run(["grim", "-g", f"{l['x']},{l['y']} {l['w']}x{l['h']}", "-"],
                         capture_output=True, check=True).stdout
    im = Image.open(BytesIO(png)).convert("RGB")
    pts = [(x, y) for x in range(10, im.width - 10, 30) for y in range(10, im.height - 10, 30)]
    return {"magenta": round(sum(im.getpixel(q) == (255, 0, 255) for q in pts) / len(pts), 2),
            "size": [l["w"], l["h"]]}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("default", "request") else "request"
    webkit = "webkit" if "--webkit" in sys.argv else "plain"
    lib = os.path.expanduser("~/.local/lib/soft-launcher/lib/libgtk-layer-shell.so.0")
    child = subprocess.Popen(["python3", "-c", CHILD, mode, webkit, lib],
                             env={**os.environ, "__NV_DISABLE_EXPLICIT_SYNC": "1"},
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0, result = time.monotonic(), []
    for at, phase in PHASES:
        time.sleep(max(0.0, t0 + at + 0.8 - time.monotonic()))
        result.append({"phase": phase, "layer": sample(child.pid)})
    child.wait(5)
    print(json.dumps({"mode": mode, "content": webkit, "samples": result}, indent=1))


if __name__ == "__main__":
    main()
