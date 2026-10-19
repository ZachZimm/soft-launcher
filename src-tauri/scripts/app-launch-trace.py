#!/usr/bin/env python3
"""Trace one installed one-Enter app launch at the compositor event level.

Hotkey, type the query, one Enter press/release; records Hyprland socket2 events
(openwindow/closewindow/activewindowv2/openlayer/closelayer) with addresses,
classes and monotonic milliseconds only. Titles are never recorded. Nothing is
closed automatically: the trace only observes, so an existing browser session is
never acted on. Uses installed-layer-acceptance.py's owned target window.
"""
import argparse
import importlib.util
import json
import os
import socket
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("acceptance", HERE / "installed-layer-acceptance.py")
acc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acc)
hypr, utc = acc.hypr, acc.utc

KEEP = {"openwindow", "closewindow", "activewindowv2", "openlayer", "closelayer", "windowtitlev2"}


class Trace:
    def __init__(self, classes):
        self.classes = classes
        self.t0 = time.monotonic()
        self.events = []
        self.stop = False
        path = f"{os.environ['XDG_RUNTIME_DIR']}/hypr/{os.environ['HYPRLAND_INSTANCE_SIGNATURE']}/.socket2.sock"
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.sock.settimeout(0.2)
        self.known = {}
        threading.Thread(target=self.read, daemon=True).start()

    def ms(self):
        return round((time.monotonic() - self.t0) * 1000)

    def mark(self, what):
        self.events.append({"ms": self.ms(), "mark": what})

    def read(self):
        buf = b""
        while not self.stop:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                name, _, data = line.decode(errors="replace").partition(">>")
                if name not in KEEP:
                    continue
                parts = data.split(",")
                ev = {"ms": self.ms(), "event": name}
                if name == "openwindow":
                    addr, cls = "0x" + parts[0], parts[2] if len(parts) > 2 else ""
                    self.known[addr] = cls
                    ev.update(address=addr, workspace=parts[1], cls=cls if cls in self.classes or cls.startswith("sl-qa") else "other")
                elif name in ("closewindow", "activewindowv2"):
                    addr = "0x" + parts[0] if parts[0] else ""
                    cls = self.known.get(addr) or next((c["class"] for c in hypr("clients") if c["address"] == addr), "")
                    ev.update(address=addr, cls=cls if cls in self.classes or cls.startswith("sl-qa") else ("other" if addr else ""))
                elif name == "windowtitlev2":
                    addr = "0x" + parts[0]
                    if self.known.get(addr) not in self.classes:
                        continue
                    ev = {"ms": self.ms(), "event": "title-changed", "address": addr}  # title text dropped
                else:
                    if parts[0] != "soft-launcher":
                        continue
                    ev.update(namespace=parts[0])
                self.events.append(ev)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", default="br")
    ap.add_argument("--classes", default="brave-browser")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--observe", type=float, default=8.0)
    args = ap.parse_args()
    classes = set(args.classes.split(","))
    pid = acc.service_pid()
    r = acc.Run(None, pid)
    result = {"service_pid": pid, "started": utc(), "query": args.query}
    try:
        ok = r.setup_target()
        before = sorted(c["address"] for c in hypr("clients") if c["class"] in classes)
        result.update(target_focused=ok, target=r.taddr, app_windows_before=before,
                      app_pids_before=sorted({c["pid"] for c in hypr("clients") if c["class"] in classes}))
        tr = Trace(classes)
        tr.mark("hotkey")
        r.dev.hotkey()
        r.wait_layer(True)
        time.sleep(0.5)
        r.dev.type(args.query)
        time.sleep(0.9)
        tr.mark("enter-press-release")
        r.dev.tap("ENTER")
        time.sleep(args.observe)
        tr.mark("end")
        tr.stop = True
        after = [c for c in hypr("clients") if c["class"] in classes]
        result.update(events=tr.events, app_windows_after=sorted(c["address"] for c in after),
                      app_pids_after=sorted({c["pid"] for c in after}),
                      active_after=hypr("activewindow").get("address"))
    finally:
        result["cleanup"] = r.cleanup()
        result["finished"] = utc()
        args.output.write_text(json.dumps(result, indent=1))
        print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
