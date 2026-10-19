#!/usr/bin/env python3
"""Read installed service state without desktop input or private context output."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import urllib.request


HOME = Path.home()
RUNTIME = HOME / ".local/lib/soft-launcher"


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def service(name):
    result = subprocess.run(
        ["systemctl", "--user", "show", name, "-p", "MainPID", "-p", "ActiveState",
         "-p", "SubState", "-p", "NRestarts", "-p", "ExecMainStartTimestampMonotonic", "-p", "ControlGroup"],
        check=True, capture_output=True, text=True,
    )
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    group = Path("/sys/fs/cgroup" + values.get("ControlGroup", "")) / "cgroup.procs"
    values["processIds"] = sorted(int(value) for value in group.read_text().split()) if values.get("ControlGroup") and group.exists() else []
    pid = int(values["MainPID"])
    process = Path(f"/proc/{pid}")
    if process.exists():
        values["runningExecutableSha256"] = sha256(process / "exe")
    return values


def get_json(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return response.status, json.load(response)


def context_counts(serialized):
    state = json.loads(serialized)
    return {
        "bytes": len(serialized.encode()),
        "inputBytes": len(str(state.get("input", "")).encode()),
        "hasWindow": state.get("window") is not None,
        "clipboardHeadBytes": len(str(state.get("clipboard", {}).get("head", "")).encode()),
        **{key: len(state.get(key, [])) for key in ("recent", "candidates", "apps")},
    }


def collect(since):
    manifest = json.loads((RUNTIME / "install.json").read_text())
    services = {name: service(name) for name in ("soft-launcher.service", "soft-if.service", "clef-flash.service")}
    library = RUNTIME / "lib/libgtk-layer-shell.so.0.10.1"
    library_status = None
    if library.exists():
        maps = Path(f"/proc/{services['soft-launcher.service']['MainPID']}/maps").read_text()
        mapped = {line.split()[-1] for line in maps.splitlines() if "libgtk-layer-shell.so.0.10.1" in line}
        library_status = {"sha256": sha256(library), "mappedLibraryCount": len(mapped),
                          "mappedFromInstalledBundle": str(library) in mapped,
                          "manifestSha256": sha256(RUNTIME / "lib/gtk-layer-shell.bundle.json")}
    database = sqlite3.connect(Path(manifest["database"]).as_uri() + "?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    database.execute("BEGIN")
    counts = {name: database.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in ("requests", "outcomes")}
    requests = []
    for row in database.execute(
        "SELECT id,ts,name,tick,backend,latency_ms,stale,error,state,questions "
        "FROM requests ORDER BY ts DESC LIMIT 12"
    ):
        data = dict(row)
        error = data.pop("error") or ""
        data["errorClass"] = next((label for pattern, label in (
            (r"timeout|deadline", "timeout"), (r"abort|supersed", "cancelled"),
            (r"HTTP\s+\d{3}", "http"), (r"fetch|network|connect", "network"),
        ) if re.search(pattern, error, re.I)), "other" if error else None)
        data["context"] = context_counts(data.pop("state"))
        data["questions"] = len(json.loads(data["questions"]))
        requests.append(data)
    outcomes = []
    for row in database.execute("SELECT request_id,ts,payload FROM outcomes ORDER BY ts DESC LIMIT 12"):
        payload = json.loads(row["payload"])
        keys = ("action", "chosen", "predicted", "primed", "hold_ms", "accepted",
                "success", "dismissed", "outcomeId", "tick", "override")
        outcomes.append({"requestId": row["request_id"], "ts": row["ts"],
                         "flags": {key: payload[key] for key in keys if key in payload}})
    database.close()
    status, health = get_json("http://127.0.0.1:7331/health")
    _, loaded = get_json("http://127.0.0.1:11434/api/ps")
    journal = subprocess.run(
        ["journalctl", "--user", "-u", "clef-flash.service", "--since", since,
         "-o", "json", "--no-pager"], check=True, capture_output=True, text=True,
    )
    http = []
    for line in journal.stdout.splitlines():
        row = json.loads(line)
        message = re.sub(r"\x1b\[[0-9;]*m", "", row.get("MESSAGE", ""))
        match = re.search(r'\|\s*(\d{3})\s*\|\s*([^|]+)\|[^|]+\|\s*(POST|OPTIONS)\s+"/v1/systemone"', message)
        if match:
            http.append({"epochMs": int(row["__REALTIME_TIMESTAMP"]) // 1000,
                         "status": int(match[1]), "duration": match[2].strip(), "method": match[3]})
    return {
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "services": services,
        "installedSha256": sha256(RUNTIME / "soft-launcher"),
        "manifestSha256": manifest["binarySha256"],
        "runtimeLibrary": library_status,
        "catalogueSha256": sha256(Path(manifest["catalogue"])),
        "policySha256": sha256(Path(manifest["policy"])),
        "bridge": {"status": status, "healthy": health.get("ok") is True},
        "loadedModels": [{key: item.get(key) for key in ("name", "context_length", "size_vram", "expires_at")}
                         for item in loaded.get("models", [])],
        "database": {"counts": counts, "latestRequests": requests, "latestOutcomes": outcomes},
        "systemoneHttpSince": since, "systemoneHttp": http,
        "desktopInput": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2026-10-17 23:26:00 UTC")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(collect(args.since), indent=2) + "\n"
    if args.output:
        args.output.write_text(result)
    else:
        print(result, end="")
