#!/usr/bin/env python3
"""Send confirmed text once to Hermes Desktop's local file/socket bridge."""
import json
import os
from pathlib import Path
import selectors
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid

HERMES = Path.home() / ".local/bin/hermes"
RECEIPTS = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "soft-launcher/hermes-desktop"
DEADLINE_SECONDS = 45


def private_directory(path):
    info = path.lstat()
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077


def private_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)
        handle.write("\n")


def record(request_id, status, cleanup_failed=False):
    # Correlation survives an ambiguous timeout, without retaining task text.
    RECEIPTS.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not private_directory(RECEIPTS):
        raise OSError("unsafe receipt directory")
    temp = RECEIPTS / (request_id + ".new")
    value = {"version": 1, "requestId": request_id, "status": status, "at": int(time.time() * 1000)}
    if cleanup_failed:
        value["cleanup"] = "failed"
    private_json(temp, value)
    temp.replace(RECEIPTS / (request_id + ".json"))


def receive_ack(connection, request_id, deadline):
    if hasattr(socket, "SO_PEERCRED"):
        _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            return "unknown"
    connection.settimeout(max(0.001, deadline - time.monotonic()))
    with connection.makefile("rb") as stream:
        raw = stream.readline(4097)
    if len(raw) > 4096 or not raw.endswith(b"\n"):
        return "unknown"
    try:
        ack = json.loads(raw)
    except (ValueError, UnicodeError):
        return "unknown"
    if (not isinstance(ack, dict) or ack.get("version") != 1
            or ack.get("handler") != "hermes-desktop-launcher-handoff" or ack.get("requestId") != request_id):
        return "unknown"
    if ack.get("status") == "accepted" and all(
        isinstance(ack.get(key), str) and 0 < len(ack[key]) <= 256 and not any(ord(c) < 32 for c in ack[key])
                for key in ("sessionId", "runtimeSessionId", "turnId")
    ):
        return "accepted"
    if ack.get("status") == "error" and ack.get("error") in ("desktop", "unsupported", "rejected"):
        return ack["error"]
    return "unknown"


def wait_for_ack(listener, child, request_id, deadline):
    # Block on socket/process readiness. A successful second instance may exit
    # before the primary renderer accepts the turn; that exit is not an ACK.
    started = time.monotonic()
    with selectors.DefaultSelector() as events:
        events.register(listener, selectors.EVENT_READ, "ack")
        process_fd = os.pidfd_open(child.pid) if hasattr(os, "pidfd_open") else None
        try:
            if process_fd is not None:
                events.register(process_fd, selectors.EVENT_READ, "process")
            while True:
                ready = events.select(max(0, deadline - time.monotonic()))
                if not ready:
                    return "timeout"
                # Prefer a real ACK when process exit and reply arrive together.
                ready.sort(key=lambda event: event[0].data != "ack")
                for key, _ in ready:
                    if key.data == "ack":
                        connection, _ = listener.accept()
                        with connection:
                            return receive_ack(connection, request_id, deadline)
                    events.unregister(process_fd)
                    code = child.wait()
                    if code:
                        return "unsupported" if code == 2 and time.monotonic() - started <= 2 else "unknown"
        finally:
            if process_fd is not None:
                os.close(process_fd)


def handoff(text):
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 4096 or "\0" in text:
        return {"status": "error", "error": "input"}
    if not os.access(HERMES, os.X_OK):
        return {"status": "error", "error": "desktop"}
    request_id = str(uuid.uuid4())
    result = {"status": "error", "error": "desktop", "requestId": request_id}
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    if not runtime.is_absolute() or (runtime != Path("/tmp") and not private_directory(runtime)):
        return result
    # Short names keep AF_UNIX paths below Linux's 108-byte sockaddr limit.
    folder = Path(tempfile.mkdtemp(prefix="slhd-", dir=runtime))
    try:
        reply_socket = folder / "ack.sock"
        if len(os.fsencode(reply_socket)) > 100 or not private_directory(folder):
            return result
        record(request_id, "opening")
        deadline = time.monotonic() + DEADLINE_SECONDS
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(reply_socket))
            reply_socket.chmod(0o600)
            listener.listen(1)
            task = folder / "request.json"
            private_json(task, {
                "version": 1, "requestId": request_id, "text": text,
                "replySocket": str(reply_socket), "expiresAt": int(time.time() * 1000 + (DEADLINE_SECONDS - 3) * 1000),
                "provider": "openai-codex", "model": "gpt-6.1-sol",
            })
            env = dict(os.environ)
            for name in ("HERMES_YOLO_MODE", "HERMES_ACCEPT_HOOKS", "HERMES_SINGLE_QUERY_SESSION"):
                env.pop(name, None)
            child = subprocess.Popen([str(HERMES), "desktop", "--skip-build", "--launcher-handoff", str(task)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, cwd=Path.home(), env=env)
            try:
                status = wait_for_ack(listener, child, request_id, deadline)
            except Exception:
                status = "unknown"
            finally:
                try:
                    threading.Thread(target=child.wait, daemon=True).start()
                except RuntimeError:
                    pass
            if status == "accepted":
                result = {"status": "accepted", "requestId": request_id, "version": 1, "destination": "hermes-desktop"}
            else:
                result["error"] = status
            try:
                record(request_id, status)
            except OSError:
                # A failed receipt write cannot undo an accepted turn or make
                # an uncertain delivery safe to repeat.
                pass
            return result
    finally:
        # Desktop owns its lifecycle, including a pre-existing primary instance.
        # Never terminate it when a receipt is late or the outcome is unknown.
        try:
            shutil.rmtree(folder)
        except OSError:
            # Cleanup cannot undo a successful submit or make an uncertain one
            # safe to repeat. Retain the delivery result and flag the residue.
            result["cleanup"] = "failed"
            try:
                record(request_id, result.get("error", result["status"]), cleanup_failed=True)
            except OSError:
                pass


def main():
    if sys.argv[1:] == ["--capabilities"]:
        print(json.dumps({"version": 1, "destination": "hermes-desktop"}))
        return 0
    try:
        # 4,096 valid input bytes can expand sixfold when JSON escapes control
        # characters. Leave room for the envelope as well.
        raw = sys.stdin.buffer.read(32 * 1024 + 1)
        request = json.loads(raw) if len(raw) <= 32 * 1024 else None
        result = handoff(request["text"]) if isinstance(request, dict) and set(request) == {"text"} else {"status": "error", "error": "input"}
    except Exception:
        result = {"status": "error", "error": "desktop"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
