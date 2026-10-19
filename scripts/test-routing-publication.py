#!/usr/bin/env python3
"""Publication gate tests. All files are temporary; no services or GUI are used."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('routing_publication', Path(__file__).with_name('routing-publication.py'))
publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication)
EXPECTED = 'a' * 64


class PublicationGates(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='routing-publication-test-')
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.receipt = self.root / 'qa.json'
        self.good = {'scope': 'candidate', 'passed': True, 'binarySha256': EXPECTED}

    def write(self, value):
        self.receipt.write_text(json.dumps(value))
        return self.receipt

    def test_valid_candidate_receipt(self):
        self.assertEqual(publication.validate_qa_evidence(self.write(self.good), EXPECTED), self.good)

    def test_missing_unreadable_and_malformed_receipt(self):
        with self.assertRaises(publication.PublicationVerificationError):
            publication.validate_qa_evidence(self.receipt, EXPECTED)
        for raw in ('{', '\ufffd', 'null', '[]'):
            self.receipt.write_text(raw)
            with self.subTest(raw=raw), self.assertRaises(publication.PublicationVerificationError):
                publication.validate_qa_evidence(self.receipt, EXPECTED)

    def test_missing_failed_wrong_scope_and_mismatched_candidate(self):
        for key in ('scope', 'passed', 'binarySha256'):
            value = dict(self.good)
            del value[key]
            with self.subTest(missing=key), self.assertRaises(publication.PublicationVerificationError):
                publication.validate_qa_evidence(self.write(value), EXPECTED)
        for changes in ({'passed': False}, {'passed': 1}, {'scope': 'installed'}, {'binarySha256': 'b' * 64}):
            with self.subTest(changes=changes), self.assertRaises(publication.PublicationVerificationError):
                publication.validate_qa_evidence(self.write(self.good | changes), EXPECTED)

    def test_source_and_candidate_mismatch_fail_before_bundle_validation(self):
        source = self.root / 'source.ts'
        source.write_text('changed')
        binary = self.root / 'soft-launcher'
        binary.write_text('candidate')
        frozen = {'sourceFiles': {'source.ts': EXPECTED}, 'installedSoftIfFiles': {}, 'productionCandidate': {'cohort': str(self.root), 'binarySha256': EXPECTED}}
        with patch.object(publication, 'ROOT', self.root), patch.object(publication.layer_shell, 'validate_bundle') as bundle:
            with self.assertRaises(publication.PublicationVerificationError):
                publication.verify_frozen(frozen)
            frozen['sourceFiles'] = {}
            with self.assertRaises(publication.PublicationVerificationError):
                publication.verify_frozen(frozen)
            bundle.assert_not_called()

    def setup_main(self):
        (self.root / 'src-tauri').mkdir()
        frozen = {'productionCandidate': {'binarySha256': EXPECTED}}
        (self.root / 'src-tauri/routing-frozen-build.json').write_text(json.dumps(frozen))
        runtime = self.root / 'runtime'
        runtime.mkdir()
        (runtime / 'install.json').write_text('{}')
        return runtime

    def test_bad_qa_cannot_enter_publication_transaction(self):
        runtime = self.setup_main()
        self.write(self.good | {'passed': False})
        with patch.object(publication, 'ROOT', self.root), patch.object(publication, 'RUNTIME', runtime), patch.object(sys, 'argv', ['publish', '--qa-evidence', str(self.receipt), '--qa-accepted']), patch.object(publication.install, 'RuntimeUpdate') as boundary, patch.object(publication, 'capture') as capture:
            with self.assertRaises(publication.PublicationVerificationError):
                publication.main()
            boundary.assert_not_called()
            capture.assert_not_called()

    def test_explicit_freeze_selects_new_cohort_without_touching_history(self):
        runtime = self.setup_main()
        historical = self.root / 'src-tauri/routing-frozen-build.json'
        historical.write_text('historical evidence, never loaded or overwritten')
        frozen = self.root / 'url-frozen-build.json'
        frozen.write_text(json.dumps({'productionCandidate': {'binarySha256': 'b' * 64}}))
        output = self.root / 'url-publication.json'
        self.write(self.good)
        with patch.object(publication, 'ROOT', self.root), patch.object(publication, 'RUNTIME', runtime), patch.object(sys, 'argv', ['publish', '--freeze', str(frozen), '--output', str(output), '--qa-evidence', str(self.receipt), '--qa-accepted']), patch.object(publication.install, 'RuntimeUpdate') as boundary:
            with self.assertRaisesRegex(publication.PublicationVerificationError, 'candidate SHA256 mismatch'):
                publication.main()
            boundary.assert_not_called()
        self.assertEqual(historical.read_text(), 'historical evidence, never loaded or overwritten')
        self.assertFalse(output.exists())

    def test_existing_publication_receipt_blocks_before_any_mutation(self):
        output = self.root / 'previous-publication.json'
        output.write_text('preserve me')
        with patch.object(sys, 'argv', ['publish', '--output', str(output), '--qa-evidence', str(self.receipt), '--qa-accepted']), patch.object(publication.install, 'RuntimeUpdate') as boundary:
            with self.assertRaisesRegex(publication.PublicationVerificationError, 'historical publication receipt'):
                publication.main()
            boundary.assert_not_called()
        self.assertEqual(output.read_text(), 'preserve me')

    def test_postcheck_failure_exits_through_transaction_rollback_boundary(self):
        runtime = self.setup_main()
        self.write(self.good)
        before = {'mappedLauncherWindows': {'layers': 0, 'toplevels': 0}}
        after = {'binarySha256': 'wrong', 'services': {'soft-launcher.service': {'MainPID': '123'}}}
        exited = []

        class Boundary:
            def __enter__(self):
                return self

            def __exit__(self, kind, error, traceback):
                exited.append(kind)
                return False

        def fake_install(args, update):
            (runtime / 'install.json').write_text(json.dumps({'binarySha256': EXPECTED}))

        with patch.object(publication, 'ROOT', self.root), patch.object(publication, 'RUNTIME', runtime), patch.object(sys, 'argv', ['publish', '--qa-evidence', str(self.receipt), '--qa-accepted']), patch.object(publication, 'verify_frozen', return_value=self.root / 'candidate'), patch.object(publication, 'wait_for_launcher', return_value={'pid': '123'}), patch.object(publication, 'capture', side_effect=[before, after]), patch.object(publication.install, 'RuntimeUpdate', Boundary), patch.object(publication.install, '_install', side_effect=fake_install):
            with self.assertRaises(publication.PublicationVerificationError):
                publication.main()
        self.assertEqual(exited, [publication.PublicationVerificationError])
        self.assertFalse((self.root / 'src-tauri/routing-publication.json').exists())

    def test_readiness_waits_for_same_process_to_map_verified_library(self):
        values = {'ActiveState': 'active', 'SubState': 'running', 'MainPID': '123'}
        clock = iter([0, 0, 0.05])
        library = str(publication.RUNTIME / 'lib/libgtk-layer-shell.so.0.10.1')
        with patch.object(publication, 'service', return_value=values), patch.object(publication, 'sha', return_value=EXPECTED), patch.object(Path, 'read_text', side_effect=['starting', library]), patch.object(publication.time, 'monotonic', side_effect=lambda: next(clock)), patch.object(publication.time, 'sleep') as sleep:
            ready = publication.wait_for_launcher(EXPECTED)
        self.assertEqual(ready['pid'], '123')
        self.assertEqual(ready['polls'], 2)
        sleep.assert_called_once()

    def test_readiness_rejects_restart_and_wrong_executable(self):
        values = {'ActiveState': 'active', 'SubState': 'running', 'MainPID': '123'}
        with patch.object(publication, 'service', side_effect=[values, values | {'MainPID': '124'}]), patch.object(publication, 'sha', return_value=EXPECTED), patch.object(Path, 'read_text', return_value='starting'), patch.object(publication.time, 'sleep'):
            with self.assertRaisesRegex(publication.PublicationVerificationError, 'process changed'):
                publication.wait_for_launcher(EXPECTED)
        with patch.object(publication, 'service', return_value=values), patch.object(publication, 'sha', return_value='wrong'), patch.object(Path, 'read_text', return_value='starting'):
            with self.assertRaisesRegex(publication.PublicationVerificationError, 'does not match'):
                publication.wait_for_launcher(EXPECTED)

    def test_readiness_timeout_exits_through_transaction_rollback_boundary(self):
        runtime = self.setup_main()
        self.write(self.good)
        before = {'mappedLauncherWindows': {'layers': 0, 'toplevels': 0}}
        exited = []

        class Boundary:
            def __enter__(self):
                return self

            def __exit__(self, kind, error, traceback):
                exited.append(kind)
                return False

        def fake_install(args, update):
            (runtime / 'install.json').write_text(json.dumps({'binarySha256': EXPECTED}))

        original_wait = publication.wait_for_launcher
        def bounded_wait(digest):
            return original_wait(digest, timeout_seconds=0, process_root=self.root / 'proc')

        values = {'ActiveState': 'active', 'SubState': 'running', 'MainPID': '123'}
        with patch.object(publication, 'ROOT', self.root), patch.object(publication, 'RUNTIME', runtime), patch.object(sys, 'argv', ['publish', '--qa-evidence', str(self.receipt), '--qa-accepted']), patch.object(publication, 'verify_frozen', return_value=self.root / 'candidate'), patch.object(publication, 'capture', return_value=before), patch.object(publication.install, 'RuntimeUpdate', Boundary), patch.object(publication.install, '_install', side_effect=fake_install), patch.object(publication, 'wait_for_launcher', side_effect=bounded_wait), patch.object(publication, 'service', return_value=values), patch.object(publication, 'sha', return_value=EXPECTED):
            with self.assertRaisesRegex(publication.PublicationVerificationError, 'readiness timed out'):
                publication.main()
        self.assertEqual(exited, [publication.PublicationVerificationError])

    def test_gate_checks_survive_optimized_python(self):
        self.write(self.good | {'passed': False})
        code = '''import importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location('publication',sys.argv[1]);p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
for check in [lambda: p.require(False,'fail closed'), lambda: p.validate_qa_evidence(pathlib.Path(sys.argv[2]),sys.argv[3])]:
 try: check()
 except p.PublicationVerificationError: continue
 raise SystemExit('gate was removed by optimization')
'''
        result = subprocess.run([sys.executable, '-O', '-B', '-c', code, str(Path(publication.__file__)), str(self.receipt), EXPECTED], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == '__main__':
    unittest.main()
