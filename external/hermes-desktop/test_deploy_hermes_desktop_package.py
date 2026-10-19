#!/usr/bin/env python3
"""Fixture tests for the transactional Desktop package swap. Temporary trees only; no real paths."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('deploy', HERE / 'deploy-hermes-desktop-package.py')
deploy_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_mod)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class Fixture:
    def __init__(self, tmp):
        tmp = Path(tmp)
        self.paths = deploy_mod.Paths(tmp / 'hermes-agent', tmp / 'hermes-home/desktop-build-stamp.json', tmp / 'backups', tmp / 'records')
        live = self.paths.live
        write(live / 'Hermes', 'old exe')
        write(live / 'resources/app.asar', 'old asar')
        write(live / 'locales/en.pak', 'old locale')
        write(self.paths.stamp, '{"contentHash": "old"}')
        self.candidate_dir = tmp / 'candidate'
        cand = self.candidate_dir / 'linux-unpacked'
        write(cand / 'Hermes', 'new exe')
        write(cand / 'resources/app.asar', 'new asar')
        write(cand / 'locales/en.pak', 'new locale')
        write(self.paths.hermes_root / 'apps/desktop/electron/main.ts', 'patched source')
        self.old_tree = deploy_mod.tree_sha256(live)
        self.manifest = {
            'package': {'candidateDir': str(self.candidate_dir), 'treeSha256': deploy_mod.tree_sha256(cand),
                        'files': {'Hermes': deploy_mod.sha(cand / 'Hermes'), 'resources/app.asar': deploy_mod.sha(cand / 'resources/app.asar')}},
            'source': {'after': {'apps/desktop/electron/main.ts': deploy_mod.sha(self.paths.hermes_root / 'apps/desktop/electron/main.ts')},
                       'before': {}, 'added': []},
            'liveReleaseBefore': {'Hermes': deploy_mod.sha(live / 'Hermes'), 'resources/app.asar': deploy_mod.sha(live / 'resources/app.asar'),
                                  'buildStampSha256': deploy_mod.sha(self.paths.stamp)},
        }

    def stamp_writer(self, paths):
        write(paths.stamp, '{"contentHash": "new"}')

    def records(self):
        return [json.loads(p.read_text()) for p in sorted(self.paths.records.glob('deployment-*.json'))]

    def leftovers(self):
        return sorted(p.name for p in self.paths.release.iterdir() if p.name != 'linux-unpacked')


class DeployTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='slhd-deploy-test-')
        self.f = Fixture(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_deploy(self, **kw):
        kw.setdefault('running', lambda: [])
        kw.setdefault('write_stamp', self.f.stamp_writer)
        return deploy_mod.deploy(self.f.manifest, self.f.paths, **kw)

    def assert_old_live_and_stamp(self):
        self.assertEqual(deploy_mod.tree_sha256(self.f.paths.live), self.f.old_tree)
        self.assertEqual(self.f.paths.stamp.read_text(), '{"contentHash": "old"}')
        self.assertEqual(self.f.leftovers(), [])

    def test_success_swaps_in_the_verified_tree_and_keeps_a_backup(self):
        self.assertEqual(self.run_deploy(), 0)
        self.assertEqual(deploy_mod.tree_sha256(self.f.paths.live), self.f.manifest['package']['treeSha256'])
        self.assertEqual(self.f.paths.stamp.read_text(), '{"contentHash": "new"}')
        self.assertEqual(self.f.leftovers(), [])
        rec = self.f.records()[-1]
        self.assertEqual(rec['outcome'], 'deployed')
        backup = Path(rec['backup'])
        self.assertEqual(deploy_mod.tree_sha256(backup / 'linux-unpacked'), self.f.old_tree)
        self.assertEqual(Path(rec['stampBackup']).read_text(), '{"contentHash": "old"}')
        # And the backup rolls back cleanly.
        self.assertEqual(deploy_mod.rollback(self.f.manifest, backup, False, self.f.paths, running=lambda: []), 0)
        self.assert_old_live_and_stamp()

    def test_second_rename_failure_restores_the_previous_tree(self):
        calls = []

        def flaky_rename(src, dst):
            calls.append((src, dst))
            if len(calls) == 2:
                raise OSError('injected second-rename failure')
            os.rename(src, dst)

        self.assertEqual(self.run_deploy(rename=flaky_rename), 1)
        self.assert_old_live_and_stamp()
        rec = self.f.records()[-1]
        self.assertEqual((rec['outcome'], rec['rolledBack']), ('failed', True))
        self.assertEqual(rec['liveTreeRestored'], self.f.old_tree)

    def test_stamp_failure_restores_tree_and_original_stamp(self):
        def broken_stamp(paths):
            write(paths.stamp, '{"contentHash": "half-written"}')
            raise RuntimeError('injected stamp failure')

        self.assertEqual(self.run_deploy(write_stamp=broken_stamp), 1)
        self.assert_old_live_and_stamp()
        self.assertTrue(self.f.records()[-1]['rolledBack'])

    def test_post_swap_verification_failure_restores(self):
        def stamp_then_corrupt(paths):
            self.f.stamp_writer(paths)
            write(paths.live / 'resources/app.asar', 'corrupted after swap')

        self.assertEqual(self.run_deploy(write_stamp=stamp_then_corrupt), 1)
        self.assert_old_live_and_stamp()
        self.assertIn('live tree does not match', self.f.records()[-1]['failure'])

    def test_desktop_starting_before_swap_aborts_without_change(self):
        state = {'n': 0}

        def starts_late():
            state['n'] += 1
            return [4242] if state['n'] > 1 else []

        self.assertEqual(self.run_deploy(running=starts_late), 2)
        self.assert_old_live_and_stamp()

    def test_refusals_change_nothing(self):
        cases = {
            'desktop running': lambda: self.run_deploy(running=lambda: [1234]),
            'changed candidate file': lambda: (write(self.f.candidate_dir / 'linux-unpacked/locales/en.pak', 'tampered'), self.run_deploy())[1],
        }
        for label, run in cases.items():
            with self.subTest(label):
                self.assertEqual(run(), 2)
                self.assert_old_live_and_stamp()
                self.assertFalse(self.f.paths.backups.exists() and any(self.f.paths.backups.iterdir()))

    def test_changed_live_release_or_stamp_is_refused(self):
        write(self.f.paths.live / 'resources/app.asar', 'someone else updated Desktop')
        self.assertEqual(self.run_deploy(), 2)
        write(self.f.paths.live / 'resources/app.asar', 'old asar')
        write(self.f.paths.stamp, '{"contentHash": "rebuilt meanwhile"}')
        self.assertEqual(self.run_deploy(), 2)
        self.assertEqual(self.f.paths.stamp.read_text(), '{"contentHash": "rebuilt meanwhile"}')
        self.assertFalse(self.f.paths.backups.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
