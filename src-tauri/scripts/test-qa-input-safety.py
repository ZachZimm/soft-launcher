#!/usr/bin/env python3
"""Nonvisual regression. All device, compositor and process boundaries are fakes."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("hermes_acceptance_safety", HERE / "hermes-binding-acceptance.py")
hermes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hermes)
acc = hermes.acc


class StopInput(RuntimeError):
    pass


class FakeDevice:
    e = types.SimpleNamespace(KEY_LEFTCTRL=1, KEY_COMMA=2, KEY_LEFTSHIFT=3, KEY_TAB=4)

    def __init__(self, events):
        self.events = events

    def hotkey(self):
        self.events.append("toggle")

    def __getattr__(self, name):
        def input_attempt(*args, **kwargs):
            self.events.append(name)
            raise StopInput(name)
        return input_attempt

    def close(self):
        self.events.append("device-close")


class GuardRegression(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        # No regression, including a broken guard, can talk to a compositor,
        # start a process, import the real evdev module or touch a real device.
        for name in ("run", "Popen", "check_call", "check_output"):
            self.stack.enter_context(patch.object(subprocess, name, side_effect=AssertionError("process boundary sealed")))
        self.stack.enter_context(patch.object(hermes.time, "sleep", return_value=None))
        self.stack.enter_context(patch.dict(sys.modules, {"evdev": Mock()}))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.events = []
        self.run = hermes.HermesRun.__new__(hermes.HermesRun)
        self.run.dev = FakeDevice(self.events)
        self.run.steps = []
        self.run.pid = 100
        self.run.target = types.SimpleNamespace(pid=200, poll=lambda: 0)
        self.run.taddr = "owned-target"
        self.run.mon, self.run.ws = 1, 1
        self.run.tlog = ["focus-out"]
        self.run.tdrain = lambda: self.run.tlog
        self.run._target_ready = True
        self.run.layer = lambda: {"pid": 100, "at": [0, 0], "size": [100, 100]}
        self.run.drawn = lambda *a: True
        self.run.wait_layer = lambda *a, **kw: True
        self.run.shot = lambda *a: "synthetic.png"
        self.active = "owned-target"

        def hypr(command, *args, **kwargs):
            if command == "activewindow":
                return {"address": self.active}
            if command == "clients":
                return [{"pid": 200, "address": "owned-target", "mapped": True}]
            raise AssertionError("unexpected compositor read")

        self.stack.enter_context(patch.object(hermes, "hypr", side_effect=hypr))
        self.stack.enter_context(patch.object(acc, "hypr", side_effect=hypr))
        self.stack.enter_context(patch.object(hermes, "hermes_windows", return_value={}))

    def attempt_accept(self):
        with self.assertRaises(RuntimeError):
            self.run.accept()

    def test_failed_setup_main_never_accepts_or_sends_input(self):
        with tempfile.TemporaryDirectory() as temp:
            self.run.setup_target = lambda: False
            self.run.cleanup = lambda: {"launcher_hidden": True, "focus_restored": True}
            with contextlib.ExitStack() as stack:
                # Only bypass entrypoint retirement to exercise the real old
                # main -> setup_target -> accept sequence with a fake device.
                stack.enter_context(patch.object(acc, "refuse_host_input", create=True))
                stack.enter_context(patch.object(acc, "Devices", acc.Devices))
                stack.enter_context(patch.object(hermes, "HermesRun", return_value=self.run))
                stack.enter_context(patch.object(acc, "service_pid", return_value=100))
                stack.enter_context(patch.object(subprocess, "check_output", side_effect=[b"/synthetic/exe\n", b"synthetic-sha exe\n"]))
                stack.enter_context(patch.object(sys, "argv", ["qa", "--shots", temp, "--output", str(Path(temp) / "result.json")]))
                with self.assertRaises(RuntimeError):
                    hermes.main()
            self.assertEqual(self.events, [])
            self.assertFalse(json.loads((Path(temp) / "result.json").read_text())["pass"])

    def test_missing_layer_accept_sends_no_followup_keys(self):
        self.run.wait_layer = lambda *a, **kw: False
        self.run.layer = lambda: None
        self.attempt_accept()
        self.assertEqual(self.events, ["toggle"])

    def test_focus_lost_before_toggle_accept_sends_zero_input(self):
        self.active = "unowned-window"
        self.attempt_accept()
        self.assertEqual(self.events, [])

    def test_target_keeps_focus_accept_sends_no_followup_keys(self):
        self.run.tlog = ["focus-in"]
        self.attempt_accept()
        self.assertEqual(self.events, ["toggle"])

    def test_focus_lost_after_layer_check_sends_no_followup_keys(self):
        def wait(*args, **kwargs):
            self.active = "unowned-window"
            return True
        self.run.wait_layer = wait
        self.attempt_accept()
        self.assertEqual(self.events, ["toggle"])

    def test_missing_layer_despite_successful_wait_sends_no_followup_keys(self):
        self.run.layer = lambda: None
        self.attempt_accept()
        self.assertEqual(self.events, ["toggle"])

    def test_valid_ownership_can_reach_actual_typed_input_seam(self):
        self.run.dev.chord = lambda *args: self.events.append("settings-chord")
        self.run.dev.tap = lambda *args: self.events.append("settings-escape")
        # FakeDevice.type stops the run at the actual typed-input seam.
        self.attempt_accept()
        self.assertEqual(self.events, ["toggle", "settings-chord", "settings-escape", "type"])

    def test_focus_lost_during_selection_gate_sends_no_selection_or_enter(self):
        self.run.dev.chord = lambda *args: self.events.append("settings-chord")
        self.run.dev.tap = lambda *args: self.events.append("settings-escape")
        self.run.dev.type = lambda *args: self.events.append("synthetic-type")
        clock = iter(range(0, 1000, 10))

        def gate(*args):
            self.active = "unowned-window"
            return "TAB"

        self.run.shots = Path("/synthetic")
        with patch.object(hermes, "gate", side_effect=gate), \
                patch.object(hermes, "lab_connections", return_value=["synthetic-connection"]), \
                patch.object(hermes.time, "monotonic", side_effect=lambda: next(clock)):
            self.attempt_accept()
        self.assertEqual(self.events, ["toggle", "settings-chord", "settings-escape", "synthetic-type"])

    def test_cleanup_never_sends_keys_moves_pointer_or_restores_focus(self):
        self.run.p0, self.run.a0 = [1, 2], "unowned-window"
        self.run.toplevels = lambda: []
        with patch.object(acc, "warp", side_effect=lambda *a: self.events.append("warp")), \
                patch.object(acc, "dispatch", side_effect=lambda *a: self.events.append("focus")), \
                patch.object(acc, "cursor", return_value=[1, 2]):
            self.run.cleanup()
        self.assertEqual(self.events, ["device-close"])

    def test_cleanup_handles_uninitialized_setup_and_keeps_closing_resources(self):
        self.run.target = None
        self.run.layer = Mock(side_effect=RuntimeError("compositor unavailable"))
        self.run.cleanup()
        self.assertEqual(self.events, ["device-close"])

    def test_cleanup_process_failure_still_closes_device_without_input(self):
        self.run.target = Mock()
        self.run.target.poll.return_value = None
        self.run.target.terminate.side_effect = OSError("synthetic process error")
        result = self.run.cleanup()
        self.assertEqual(self.events, ["device-close"])
        self.assertFalse(result["resources_closed"])
        self.assertEqual(result["errors"], ["owned-target-close-failed"])

    def test_cleanup_reaps_only_owned_target_after_timeout(self):
        self.run.target = Mock()
        self.run.target.poll.return_value = None
        self.run.target.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 2), 0]
        self.run.cleanup()
        self.run.target.terminate.assert_called_once_with()
        self.run.target.kill.assert_called_once_with()
        self.assertEqual(self.run.target.wait.call_count, 2)
        self.assertEqual(self.events, ["device-close"])

    def test_retired_entrypoints_refuse_before_any_service_or_device_access(self):
        for module in (hermes, acc):
            with self.subTest(module=module.__name__), \
                    patch.object(sys, "argv", ["qa", "--shots", "/synthetic", "--output", "/synthetic/result"]), \
                    patch.object(Path, "mkdir", side_effect=AssertionError("filesystem access forbidden")), \
                    patch.object(acc, "service_pid", side_effect=AssertionError("service access forbidden")):
                with self.assertRaisesRegex(RuntimeError, "retired"):
                    module.main()

    def test_retired_device_constructors_never_import_or_create_uinput(self):
        fake_evdev = Mock()
        with patch.dict(sys.modules, {"evdev": fake_evdev}):
            for cls in (hermes.Keyboard, acc.Devices):
                with self.subTest(cls=cls.__name__):
                    with self.assertRaisesRegex(RuntimeError, "retired"):
                        cls()
            fake_evdev.UInput.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
