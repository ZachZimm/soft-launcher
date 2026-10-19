#!/usr/bin/env python3
"""Publication gate tests. All files are temporary; no services or GUI are used."""
import importlib.util
import fcntl
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
VERIFY_SINGLETON = publication.verify_singleton


class PublicationGates(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='routing-publication-test-')
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.receipt = self.root / 'qa.json'
        self.good = {'scope': 'candidate', 'passed': True, 'binarySha256': EXPECTED}
        guard_path = patch.object(publication.install, 'UPDATE_LOCK', self.root / 'update.lock')
        guard_path.start(); self.addCleanup(guard_path.stop)
        singleton = patch.object(publication, 'verify_singleton')
        singleton.start(); self.addCleanup(singleton.stop)

    def test_conflicting_hotkey_instance_cannot_pass_singleton_check(self):
        VERIFY_SINGLETON('123', {123: {}})
        for processes in ({}, {124: {}}, {123: {}, 124: {}}):
            with self.subTest(processes=processes), self.assertRaises(publication.PublicationVerificationError):
                VERIFY_SINGLETON('123', processes)

    def test_hotkey_migration_allows_only_exact_toggle_stamp(self):
        before = {'protectedFiles': {'toggleWrapper': {'sha256': EXPECTED, 'mode': '0o755'}, 'wrapper': {'sha256': 'b' * 64, 'mode': '0o755'}}}
        expected = {'sha256': publication.hashlib.sha256(publication.install.toggle_script().encode()).hexdigest(), 'mode': '0o755'}
        plan = {'before': before['protectedFiles']['toggleWrapper'], 'after': expected}
        publication.validate_hotkey_migration(plan, before)
        after = {'protectedFiles': before['protectedFiles'] | {'toggleWrapper': expected}}
        publication.verify_protected_files(before, after, hotkey=plan)
        for changed in (plan | {'before': None}, plan | {'after': expected | {'mode': '0o644'}}, plan | {'wrapper': expected}):
            with self.assertRaises(publication.PublicationVerificationError): publication.validate_hotkey_migration(changed, before)
        with self.assertRaises(publication.PublicationVerificationError):
            publication.verify_protected_files(before, {'protectedFiles': after['protectedFiles'] | {'wrapper': None}}, hotkey=plan)

    def test_retirement_checks_exact_identity_hidden_quiet_and_uses_pidfd_only(self):
        identity = {'pid': 123, 'startTicks': 456, 'exePath': '/example/soft-launcher', 'exeSha256': EXPECTED, 'argvSha256': 'b' * 64}
        with patch.object(publication, 'launcher_processes', side_effect=[{123: identity}, {123: identity}, {}]), patch.object(publication.os, 'pidfd_open', return_value=99) as opened, patch.object(publication.os, 'close') as closed, patch.object(publication.signal, 'pidfd_send_signal') as signal, patch.object(publication.time, 'sleep'), patch.object(publication.select, 'poll') as poll:
            poll.return_value.poll.return_value = [(99, 1)]
            publication.retire_singleton(identity, lambda: True)
            opened.assert_called_once_with(123); signal.assert_called_once_with(99, publication.signal.SIGTERM); closed.assert_called_once_with(99)
        for changed in ({123: identity | {'startTicks': 999}}, {123: identity | {'exeSha256': 'c' * 64}}, {124: identity}):
            with patch.object(publication, 'launcher_processes', return_value=changed), patch.object(publication.os, 'pidfd_open') as opened:
                with self.assertRaises(publication.PublicationVerificationError): publication.retire_singleton(identity, lambda: True)
                opened.assert_not_called()
        with patch.object(publication, 'launcher_processes', return_value={123: identity}), patch.object(publication.os, 'pidfd_open', return_value=99), patch.object(publication.os, 'close'), patch.object(publication.signal, 'pidfd_send_signal') as signal:
            with self.assertRaises(publication.PublicationVerificationError): publication.retire_singleton(identity, lambda: False)
            signal.assert_not_called()
        with patch.object(publication, 'launcher_processes', side_effect=[{123: identity}, {123: identity | {'startTicks': 999}}]), patch.object(publication.os, 'pidfd_open', return_value=99), patch.object(publication.os, 'close') as closed, patch.object(publication.signal, 'pidfd_send_signal') as signal, patch.object(publication.time, 'sleep'):
            with self.assertRaises(publication.PublicationVerificationError): publication.retire_singleton(identity, lambda: True)
            signal.assert_not_called(); closed.assert_called_once_with(99)
        with patch.object(publication, 'launcher_processes', return_value={123: identity}), patch.object(publication.os, 'pidfd_open', return_value=99), patch.object(publication.os, 'close'), patch.object(publication.signal, 'pidfd_send_signal') as signal, patch.object(publication.time, 'sleep'), patch.object(publication.select, 'poll') as poll:
            poll.return_value.poll.return_value = []
            with self.assertRaisesRegex(publication.PublicationVerificationError, 'did not exit'):
                publication.retire_singleton(identity, lambda: True)
            signal.assert_called_once_with(99, publication.signal.SIGTERM)

    def test_inactive_baseline_must_be_recovered_and_live_verified_before_install(self):
        before, plan = self.configuration_fixture()
        before.update(services={'soft-launcher.service': {'MainPID': '0'}}, databaseCounts={'requests': 1}, mappedLauncherWindows={'layers': 0, 'toplevels': 0})
        events = []
        class Boundary:
            active_before = {'soft-launcher.service': False}
            services_changed = False
            def prepare(self): events.append('capture-and-safe-policy')
            def stage_backend_environment(self, text): events.append('stage-private-environment')
            def require_safe_stop(self): events.append('verify-process-policy')
            def note_service_change(self, *names): self.services_changed = True
        def native_hash(pid): events.append('live-baseline-hash'); return plan['effectiveNativeSha256']
        def retired(identity, quiet): self.assertTrue(quiet()); events.append('exact-hidden-singleton-retirement')
        with patch.object(publication, 'capture', return_value=before), patch.object(publication, 'retire_singleton', side_effect=retired), patch.object(publication.install, 'ctl', side_effect=lambda *args: events.append('start-old-baseline')), patch.object(publication, 'wait_for_launcher', side_effect=lambda digest: events.append('same-pid-old-exe-and-library') or {'pid': '456'}), patch.object(publication, 'native_settings_sha', side_effect=native_hash):
            result = publication.recover_inactive_baseline(Boundary(), before, {'binarySha256': EXPECTED}, plan, {})
        self.assertEqual(result, {'pid': '456'})
        self.assertEqual(events, ['capture-and-safe-policy', 'exact-hidden-singleton-retirement', 'stage-private-environment', 'verify-process-policy', 'start-old-baseline', 'same-pid-old-exe-and-library', 'live-baseline-hash'])
        with patch.object(publication, 'capture', return_value=before), patch.object(publication, 'retire_singleton'), patch.object(publication.install, 'ctl'), patch.object(publication, 'wait_for_launcher', return_value={'pid': '456'}), patch.object(publication, 'native_settings_sha', return_value='wrong'):
            with self.assertRaisesRegex(publication.PublicationVerificationError, 'Recovered live baseline'):
                publication.recover_inactive_baseline(Boundary(), before, {'binarySha256': EXPECTED}, plan, {})

    def test_update_guard_refuses_concurrent_startup_and_is_released(self):
        path = publication.install.UPDATE_LOCK
        with path.open('w') as startup:
            fcntl.flock(startup, fcntl.LOCK_SH | fcntl.LOCK_NB)
            with publication.install.RuntimeUpdate() as update:
                with self.assertRaisesRegex(RuntimeError, 'startup or update'):
                    update.acquire_guard()
        with publication.install.RuntimeUpdate() as update:
            update.acquire_guard()
            with publication.install.RuntimeUpdate() as competing:
                with self.assertRaisesRegex(RuntimeError, 'startup or update'):
                    competing.acquire_guard()
        with publication.install.RuntimeUpdate() as update:
            update.acquire_guard()

    def test_rollback_stops_only_under_process_policy_before_restoring_old_unit(self):
        units = self.root / 'units'; units.mkdir()
        unit = units / 'soft-launcher.service'
        original = '# original unit\n[Service]\nKillMode=control-group\n'
        unit.write_text(original)
        state = {'policy': 'control-group', 'retainedAppAlive': True}
        calls = []
        def ctl(*args):
            calls.append((args, state['policy']))
            if args == ('daemon-reload',):
                state['policy'] = 'process' if 'KillMode=process\n' in unit.read_text() else 'control-group'
            if args == ('stop', 'soft-launcher.service') and state['policy'] != 'process':
                state['retainedAppAlive'] = False
        def safe(_self):
            if state['policy'] != 'process': raise RuntimeError('unsafe stop')
        with patch.object(publication.install, 'CONFIG', self.root / 'config'), patch.object(publication.install, 'UNITS', units), patch.object(publication.install, 'BIN', self.root / 'bin'), patch.object(publication.install, 'CATALOGUE', self.root / 'catalogue'), patch.object(publication.install, 'active', side_effect=lambda name: name == 'soft-launcher.service'), patch.object(publication.install, 'ctl', side_effect=ctl), patch.object(publication.install.RuntimeUpdate, 'require_safe_stop', safe):
            with self.assertRaisesRegex(RuntimeError, 'injected failure'):
                with publication.install.RuntimeUpdate() as update:
                    update.prepare()
                    update.note_service_change('soft-launcher.service')
                    raise RuntimeError('injected failure')
        self.assertTrue(state['retainedAppAlive'])
        self.assertEqual(unit.read_text(), original)
        self.assertEqual(state['policy'], 'control-group')
        self.assertEqual([policy for args, policy in calls if args[0] == 'stop'], ['process'])

    def test_rejected_safe_policy_cannot_stop_any_service(self):
        units = self.root / 'units'; units.mkdir()
        unit = units / 'soft-launcher.service'; unit.write_text('original unit')
        with patch.object(publication.install, 'CONFIG', self.root / 'config'), patch.object(publication.install, 'UNITS', units), patch.object(publication.install, 'BIN', self.root / 'bin'), patch.object(publication.install, 'CATALOGUE', self.root / 'catalogue'), patch.object(publication.install, 'active', return_value=True), patch.object(publication.install, 'ctl') as ctl, patch.object(publication.install.RuntimeUpdate, 'require_safe_stop', side_effect=RuntimeError('unsafe stop')):
            with self.assertRaisesRegex(RuntimeError, 'unsafe stop'):
                with publication.install.RuntimeUpdate() as update:
                    update.prepare()
        self.assertFalse(any(call.args[0] == 'stop' for call in ctl.call_args_list))
        self.assertEqual(unit.read_text(), 'original unit')

    def test_each_recovery_failure_stops_before_legacy_unit_restore(self):
        for phase in ('after-policy', 'old-baseline-start', 'candidate-start', 'post-verification'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory(dir=self.root) as directory:
                root = Path(directory); units = root / 'units'; units.mkdir()
                unit = units / 'soft-launcher.service'
                original = '[Service]\nKillMode=control-group\n'; unit.write_text(original)
                runtime = root / 'runtime'; runtime.mkdir(); (runtime / 'binary').write_text('old')
                stage = root / 'stage'; stage.mkdir(); (stage / 'binary').write_text('candidate')
                state = {'policy': 'control-group', 'launcher': False, 'bridge': True, 'appAlive': True}
                calls = []
                def active(name): return state['launcher' if name == 'soft-launcher.service' else 'bridge']
                def ctl(*args):
                    calls.append((args, state['policy']))
                    if args == ('daemon-reload',): state['policy'] = 'process' if 'KillMode=process\n' in unit.read_text() else 'control-group'
                    if args[0] == 'stop':
                        if args[1] == 'soft-launcher.service' and state['policy'] != 'process': state['appAlive'] = False
                        state['launcher' if args[1] == 'soft-launcher.service' else 'bridge'] = False
                    if args[0] == 'start': state['launcher' if args[1] == 'soft-launcher.service' else 'bridge'] = True
                def safe(_self):
                    if state['policy'] != 'process': raise RuntimeError('unsafe policy')
                with patch.object(publication.install, 'CONFIG', root / 'config'), patch.object(publication.install, 'UNITS', units), patch.object(publication.install, 'BIN', root / 'bin'), patch.object(publication.install, 'CATALOGUE', root / 'catalogue'), patch.object(publication.install, 'RUNTIME', runtime), patch.object(publication.install, 'active', side_effect=active), patch.object(publication.install, 'ctl', side_effect=ctl), patch.object(publication.install.RuntimeUpdate, 'require_safe_stop', safe):
                    with self.assertRaisesRegex(RuntimeError, phase):
                        with publication.install.RuntimeUpdate() as update:
                            update.prepare()
                            if phase != 'after-policy':
                                update.note_service_change('soft-launcher.service'); ctl('start', 'soft-launcher.service')
                            if phase in ('candidate-start', 'post-verification'):
                                update.publish(stage); ctl('start', 'soft-launcher.service')
                            raise RuntimeError(phase)
                self.assertTrue(state['appAlive'])
                self.assertEqual(unit.read_text(), original)
                self.assertEqual((runtime / 'binary').read_text(), 'old')
                self.assertFalse(state['launcher'])
                self.assertTrue(state['bridge'])
                self.assertTrue(all(policy == 'process' for args, policy in calls if args[0] == 'stop'))
                if phase in ('after-policy', 'old-baseline-start'):
                    self.assertFalse(any(args == ('stop', 'soft-if.service') for args, _ in calls))

    def write(self, value):
        self.receipt.write_text(json.dumps(value))
        return self.receipt

    def configuration_fixture(self):
        source = self.root / 'backend.env'
        source.write_text('SOFT_LAUNCHER_CODEX_BRIDGE=/opt/example/bridge.py\n')
        source.chmod(0o600)
        before = {'binarySha256': EXPECTED, 'protectedFiles': {
            'launcherUnit': {'sha256': 'b' * 64, 'mode': '0o644'},
            'backendOverride': None,
            'catalogue': {'sha256': 'c' * 64, 'mode': '0o600'},
        }}
        plan = {
            'before': {key: before['protectedFiles'][key] for key in ('launcherUnit', 'backendOverride')},
            'after': {'launcherUnit': {'sha256': 'd' * 64, 'mode': '0o644'}, 'backendOverride': publication.stamp(source)},
            'backendEnvSource': str(source), 'baselineBinarySha256': EXPECTED,
            'baselineNativeDefaults': {'SOFT_LAUNCHER_CODEX_BRIDGE': '/opt/example/bridge.py'},
            'effectiveNativeSha256': 'e' * 64,
        }
        return before, plan

    def test_configuration_plan_allows_only_exact_two_file_stamps(self):
        before, plan = self.configuration_fixture()
        publication.validate_configuration_migration(plan, before)
        after = {'protectedFiles': before['protectedFiles'] | plan['after']}
        publication.verify_protected_files(before, after, plan)
        for key, change in [('launcherUnit', {'sha256': 'f' * 64, 'mode': '0o644'}),
                            ('backendOverride', plan['after']['backendOverride'] | {'mode': '0o644'}),
                            ('catalogue', {'sha256': 'f' * 64, 'mode': '0o600'})]:
            changed = {'protectedFiles': after['protectedFiles'] | {key: change}}
            with self.subTest(key=key), self.assertRaises(publication.PublicationVerificationError):
                publication.verify_protected_files(before, changed, plan)
        with self.assertRaises(publication.PublicationVerificationError):
            publication.verify_protected_files(before, after, None)

    def test_configuration_plan_rejects_wrong_baseline_extra_file_and_unprotected_source(self):
        before, plan = self.configuration_fixture()
        for changed in [plan | {'baselineBinarySha256': 'f' * 64},
                        plan | {'before': plan['before'] | {'backendOverride': {'sha256': 'f' * 64, 'mode': '0o600'}}},
                        plan | {'after': plan['after'] | {'catalogue': None}},
                        plan | {'after': plan['after'] | {'backendOverride': plan['after']['backendOverride'] | {'mode': '0o644'}}},
                        plan | {'effectiveNativeSha256': 'invalid'},
                        plan | {'baselineNativeDefaults': {'UNRELATED_SETTING': 'value'}}]:
            with self.subTest(plan=changed), self.assertRaises(publication.PublicationVerificationError):
                publication.validate_configuration_migration(changed, before)
        source = Path(plan['backendEnvSource'])
        source.write_text('changed bytes')
        with self.assertRaises(publication.PublicationVerificationError):
            publication.validate_configuration_migration(plan, before)
        source.chmod(0o644)
        with self.assertRaises(publication.PublicationVerificationError):
            publication.validate_configuration_migration(plan, before)

    def test_effective_native_hash_includes_missing_compiled_default_and_rejects_drift(self):
        proc = self.root / 'proc' / '123'; proc.mkdir(parents=True)
        values = {'SOFT_LAUNCHER_CODEX_SSH_HOST': 'example.test', 'SOFT_LAUNCHER_CODEX_MODEL': 'example-model',
                  'SOFT_LAUNCHER_CLEF_URL': 'http://decision.example.test:11434'}
        def write_env(settings):
            (proc / 'environ').write_bytes(b'\0'.join(f'{key}={value}'.encode() for key, value in settings.items()) + b'\0')
        write_env(values)
        defaults = {'SOFT_LAUNCHER_CODEX_BRIDGE': '/opt/example/bridge.py'}
        baseline = publication.native_settings_sha('123', defaults, process_root=self.root / 'proc')
        with self.assertRaises(publication.PublicationVerificationError):
            publication.native_settings_sha('123', process_root=self.root / 'proc')
        write_env(values | defaults)
        self.assertEqual(publication.native_settings_sha('123', process_root=self.root / 'proc'), baseline)
        write_env(values | defaults | {'SOFT_LAUNCHER_CODEX_MODEL': 'changed-model'})
        self.assertNotEqual(publication.native_settings_sha('123', process_root=self.root / 'proc'), baseline)

    def test_configuration_change_before_transaction_cannot_stop_services(self):
        backend = self.root / 'config/local.system1.soft-launcher/backend.env'
        backend.parent.mkdir(parents=True)
        backend.write_text('changed after review'); backend.chmod(0o600)
        with patch.object(publication.install, 'CONFIG', self.root / 'config'), patch.object(publication.install, 'UNITS', self.root / 'units'), patch.object(publication.install, 'BIN', self.root / 'bin'), patch.object(publication.install, 'CATALOGUE', self.root / 'catalogue'), patch.object(publication.install, 'active', return_value=False), patch.object(publication.install, 'ctl') as ctl, patch.object(publication.install, 'publish_runtime') as publish:
            with publication.install.RuntimeUpdate() as update:
                update.expected_files = {backend: {'sha256': EXPECTED, 'mode': '0o600'}}
                with self.assertRaisesRegex(RuntimeError, 'Configuration changed'):
                    update.publish(self.root / 'stage')
            ctl.assert_not_called(); publish.assert_not_called()

    def test_changed_live_native_configuration_rolls_back_publication(self):
        runtime = self.setup_main()
        before, plan = self.configuration_fixture()
        before.update(mappedLauncherWindows={'layers': 0, 'toplevels': 0}, services={'soft-launcher.service': {'MainPID': '122'}}, adapterSha256='adapter')
        after = {'binarySha256': EXPECTED, 'adapterSha256': 'adapter', 'protectedFiles': before['protectedFiles'] | plan['after'], 'services': {'soft-launcher.service': {'MainPID': '123'}}}
        frozen = self.root / 'src-tauri/routing-frozen-build.json'
        frozen.write_text(json.dumps({'productionCandidate': {'binarySha256': EXPECTED}, 'configurationMigration': plan})); frozen.chmod(0o600)
        self.write(self.good)
        exited = []
        class Boundary:
            def __enter__(self): return self
            def __exit__(self, kind, error, traceback): exited.append(kind); return False
        def fake_install(args, update):
            self.assertEqual(args.backend_env_sha256, plan['after']['backendOverride']['sha256'])
            self.assertEqual(len(update.expected_files), 2)
            (runtime / 'install.json').write_text(json.dumps({'binarySha256': EXPECTED, 'hermesAdapterSha256': 'adapter', 'hermesHandoffProtocol': 'desktop-file-socket-v1'}))
        with patch.object(publication, 'ROOT', self.root), patch.object(publication, 'RUNTIME', runtime), patch.object(sys, 'argv', ['publish', '--qa-evidence', str(self.receipt), '--qa-accepted']), patch.object(publication, 'verify_frozen', return_value=self.root / 'candidate'), patch.object(publication, 'wait_for_launcher', return_value={'pid': '123'}), patch.object(publication, 'capture', side_effect=[before, after]), patch.object(publication, 'native_settings_sha', side_effect=[plan['effectiveNativeSha256'], 'changed']), patch.object(publication.install, 'RuntimeUpdate', Boundary), patch.object(publication.install, '_install', side_effect=fake_install):
            with self.assertRaisesRegex(publication.PublicationVerificationError, 'Effective native configuration changed'):
                publication.main()
        self.assertEqual(exited, [publication.PublicationVerificationError])
        self.assertFalse((self.root / 'src-tauri/routing-publication.json').exists())

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
