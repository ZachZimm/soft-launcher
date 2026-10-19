#!/usr/bin/env python3
"""Bounded local transport tests; no GUI, inference, auth or installed changes."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import socket
import time
import tempfile
import unittest
from unittest.mock import patch
import catalogue_migration as migration

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lab = load("lab_bridge", "lab-codex-bridge.py")
hermes = load("hermes_handoff", "hermes-handoff.py")


class BackendTest(unittest.TestCase):
    def handoff_fixture(self, mode="accepted"):
        folder = tempfile.TemporaryDirectory(prefix="slh-test-")
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        base.chmod(0o700)
        binary = base / "fake-hermes"
        capture = base / "capture.json"
        binary.write_text('''#!/usr/bin/python3
import json, os, pathlib, socket, stat, sys
mode = os.environ['HERMES_TEST_MODE']
if mode == 'unsupported':
    sys.exit(2)
p = pathlib.Path(sys.argv[-1])
request = json.loads(p.read_text())
pathlib.Path(os.environ['HERMES_TEST_CAPTURE']).write_text(json.dumps({
    'request': request, 'argv': sys.argv[1:],
    'directoryMode': stat.S_IMODE(p.parent.stat().st_mode),
    'fileMode': stat.S_IMODE(p.stat().st_mode),
    'socketMode': stat.S_IMODE(pathlib.Path(request['replySocket']).stat().st_mode),
    'bypassPresent': any(k in os.environ for k in ('HERMES_YOLO_MODE', 'HERMES_ACCEPT_HOOKS', 'HERMES_SINGLE_QUERY_SESSION')),
}))
if mode == 'no-ack':
    sys.exit(0)
ack = {'version': 1, 'handler': 'hermes-desktop-launcher-handoff', 'requestId': request['requestId'], 'status': 'accepted',
       'sessionId': 'public-session', 'runtimeSessionId': 'public-runtime', 'turnId': 'public-session:user:1'}
if mode == 'rejected':
    ack = dict(ack, status='error', error='rejected')
elif mode == 'wrong-id':
    ack['requestId'] = 'not-the-request'
elif mode == 'no-turn':
    del ack['turnId']
with socket.socket(socket.AF_UNIX) as conn:
    conn.connect(request['replySocket'])
    conn.sendall((json.dumps(ack) + '\\n').encode())
''')
        binary.chmod(0o700)
        receipts = base / "receipts"
        self.enterContext(patch.object(hermes, "RECEIPTS", receipts))
        self.enterContext(patch.object(hermes, "HERMES", binary))
        self.enterContext(patch.dict(os.environ, {
            "XDG_RUNTIME_DIR": str(base), "HERMES_TEST_MODE": mode,
            "HERMES_TEST_CAPTURE": str(capture), "HERMES_YOLO_MODE": "1",
            "HERMES_ACCEPT_HOOKS": "1", "HERMES_SINGLE_QUERY_SESSION": "1",
        }))
        return base, capture, receipts

    def test_desktop_real_socket_preserves_exact_text_and_removes_payload(self):
        base, capture, receipts = self.handoff_fixture()
        text = '  Public QA: "literal"; $(never execute) café.\nSecond line.  '
        result = hermes.handoff(text)
        self.assertEqual(result["status"], "accepted")
        evidence = json.loads(capture.read_text())
        request = evidence["request"]
        self.assertEqual(request["text"], text)
        self.assertEqual(request["requestId"], result["requestId"])
        self.assertEqual(request["provider"], "openai-codex")
        self.assertEqual(request["model"], "gpt-6.1-sol")
        self.assertGreater(request["expiresAt"], time.time() * 1000)
        self.assertEqual(evidence["argv"][:3], ["desktop", "--skip-build", "--launcher-handoff"])
        self.assertFalse(any(text in arg for arg in evidence["argv"]))
        self.assertFalse(evidence["bypassPresent"])
        self.assertEqual([evidence[k] for k in ("directoryMode", "fileMode", "socketMode")], [0o700, 0o600, 0o600])
        self.assertFalse(Path(request["replySocket"]).parent.exists())
        receipt = receipts / (result["requestId"] + ".json")
        self.assertEqual(json.loads(receipt.read_text())["status"], "accepted")
        self.assertNotIn(text, receipt.read_text())
        self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
        self.assertEqual(list(base.glob("slhd-*")), [])

    def test_desktop_rejection_and_bad_receipts_never_become_success(self):
        for mode, expected in [("rejected", "rejected"), ("wrong-id", "unknown"), ("no-turn", "unknown"), ("unsupported", "unsupported")]:
            with self.subTest(mode=mode):
                base, _, receipts = self.handoff_fixture(mode)
                result = hermes.handoff("Public QA")
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["error"], expected)
                self.assertEqual(list(base.glob("slhd-*")), [])
                self.assertEqual(len(list(receipts.glob("*.json"))), 1)

    def test_successful_process_exit_without_ack_times_out_once(self):
        base, capture, receipts = self.handoff_fixture("no-ack")
        with patch.object(hermes, "DEADLINE_SECONDS", 0.15), \
                patch.object(hermes.subprocess, "Popen", wraps=hermes.subprocess.Popen) as spawn:
            result = hermes.handoff("Public QA")
            self.assertEqual(result["error"], "timeout")
            spawn.assert_called_once()
        self.assertEqual(json.loads(next(receipts.glob("*.json")).read_text())["status"], "timeout")
        self.assertEqual(list(base.glob("slhd-*")), [])
        self.assertTrue(capture.exists())

    def test_cleanup_failure_preserves_accepted_and_uncertain_results(self):
        for mode, status in [("accepted", "accepted"), ("wrong-id", "error")]:
            with self.subTest(mode=mode):
                base, _, receipts = self.handoff_fixture(mode)
                with patch.object(hermes.shutil, "rmtree", side_effect=OSError("inert cleanup failure")):
                    result = hermes.handoff("Public QA")
                self.assertEqual(result["status"], status)
                self.assertEqual(result["cleanup"], "failed")
                if status == "error":
                    self.assertEqual(result["error"], "unknown")
                receipt = json.loads(next(receipts.glob("*.json")).read_text())
                self.assertEqual(receipt["cleanup"], "failed")
                self.assertEqual(receipt["status"], "accepted" if status == "accepted" else "unknown")
                self.assertEqual(len(list(base.glob("slhd-*"))), 1)

    def test_unexpected_post_spawn_failure_is_uncertain_and_has_no_retry(self):
        base, _, _ = self.handoff_fixture("no-ack")
        with patch.object(hermes, "wait_for_ack", side_effect=RuntimeError("inert transport failure")), \
                patch.object(hermes.subprocess, "Popen", wraps=hermes.subprocess.Popen) as spawn:
            result = hermes.handoff("Public QA")
        self.assertEqual(result["error"], "unknown")
        self.assertIn("requestId", result)
        spawn.assert_called_once()
        self.assertEqual(list(base.glob("slhd-*")), [])

    def test_invalid_input_unsafe_runtime_and_receipt_symlink_never_launch(self):
        base, _, receipts = self.handoff_fixture()
        with patch.object(hermes.subprocess, "Popen") as spawn:
            for text in (None, "", "  ", "nul\0text", "é" * 2049):
                self.assertEqual(hermes.handoff(text)["error"], "input")
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(base / "link")}):
                (base / "link").symlink_to(base)
                self.assertEqual(hermes.handoff("Public QA")["error"], "desktop")
            receipts.symlink_to(base)
            with self.assertRaises(OSError):
                hermes.handoff("Public QA")
            spawn.assert_not_called()
        self.assertEqual(list(base.glob("slhd-*")), [])

    def test_ack_requires_same_peer_uid_and_bounded_matching_response(self):
        for raw in [b'{}\n', b'x' * 4097, b'{"version":1,"requestId":"request","status":"accepted"}\n']:
            left, right = socket.socketpair()
            with left, right:
                right.sendall(raw)
                right.shutdown(socket.SHUT_WR)
                self.assertEqual(hermes.receive_ack(left, "request", time.monotonic() + 1), "unknown")
        left, right = socket.socketpair()
        with left, right:
            class ForeignPeer:
                def getsockopt(self, *args):
                    return hermes.struct.pack("3i", 1, os.getuid() + 1, 1)
            self.assertEqual(hermes.receive_ack(ForeignPeer(), "request", time.monotonic() + 1), "unknown")

    def test_migration_preserves_custom_entries_and_is_idempotent(self):
        defaults = (HERE.parents[1] / "catalogue.toml").read_text()
        import tomllib
        old = ""
        for a in migration.OLD.values():
            old += '\n[[action]]\nid=' + json.dumps(a['id']) + '\ndescription=' + json.dumps(a['description']) + '\nexec=' + '{' + ','.join(k+'='+json.dumps(v) for k,v in a['exec'].items()) + '}\n'
        custom = '\n# Custom bytes stay exact\n[[action]]\nid="custom_reply"\ndescription="My reply recipe"\nexec={kind="bonsai",arg="clipboard",template="reply"}\n'
        updated, names = migration.migrate(old + custom, defaults)
        self.assertEqual(set(names), set(migration.OLD))
        self.assertIn(custom, updated)
        entries = tomllib.loads(updated)["action"]
        self.assertEqual(entries[0]["id"], "send_to_agent")
        self.assertEqual(entries[1]["exec"]["template"], "summarize")
        self.assertEqual(entries[3]["description"], "Show the configured Clef decision gateway status")
        self.assertEqual(migration.migrate(updated, defaults), (updated, []))
        edited = old.replace("Draft a short reply", "Draft MY short reply") + custom
        updated, names = migration.migrate(edited, defaults)
        self.assertNotIn("draft_reply", names)
        self.assertIn("Draft MY short reply", updated)
        collision = old + '\n[[action]]\nid="send_to_agent"\ndescription="My custom action"\nexec={kind="none"}\n'
        self.assertNotIn("draft_reply", migration.migrate(collision, defaults)[1])
        custom_status = old.replace("Show local Ollama model availability including clef-flash-q8:latest", "Show MY configured status") + custom
        updated, names = migration.migrate(custom_status, defaults)
        self.assertNotIn("clef_status", names)
        self.assertIn('description="Show MY configured status"', updated)

    def test_lab_requires_actual_codex_model_and_complete_text(self):
        cfg = {"host": "127.0.0.1", "port": 4141, "authToken": ""}
        request = {"model": "proven-model", "system": "Return a summary", "input": "Public text"}
        catalog = {"data": [{"id": "proven-model", "owned_by": "openai-codex"}]}
        completed = {"choices": [{"finish_reason": "stop", "message": {"content": "Completed public text"}}]}
        with patch.object(lab, "fetch", side_effect=[catalog, completed]) as fetch:
            self.assertEqual(lab.complete(request, cfg), {"ok": True, "text": "Completed public text"})
            payload = fetch.call_args_list[1].args[3]
            self.assertNotIn("tools", payload)
            self.assertEqual(payload["messages"][1]["content"], "Public text")
        remote_cfg = dict(cfg, host="proxy.example.test")
        with patch.dict(os.environ, {"SOFT_LAUNCHER_CODEX_PROXY_HOSTS": ""}):
            with patch.object(lab, "fetch") as fetch:
                with self.assertRaises(lab.SafeError): lab.complete(request, remote_cfg)
                fetch.assert_not_called()
        with patch.dict(os.environ, {"SOFT_LAUNCHER_CODEX_PROXY_HOSTS": "proxy.example.test"}):
            with patch.object(lab, "fetch", side_effect=[catalog, completed]):
                self.assertTrue(lab.complete(request, remote_cfg)["ok"])
        for reply in [
            {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "x", "tool_calls": ["fake destructive tool"]}}]},
            {"choices": []},
        ]:
            with patch.object(lab, "fetch", side_effect=[catalog, reply]):
                with self.assertRaises(lab.SafeError): lab.complete(request, cfg)
        with patch.object(lab, "fetch", return_value={"data": [{"id": "proven-model", "owned_by": "api-key-provider"}]}):
            with self.assertRaises(lab.SafeError): lab.complete(request, cfg)
        with self.assertRaises(lab.SafeError): lab.complete(dict(request, input="x" * 8193), cfg)



if __name__ == "__main__":
    unittest.main()
