#!/usr/bin/env python3
"""Deploy or roll back the Hermes Desktop launcher-handoff package, transactionally.

deploy:
  1. Refuse if any Hermes Desktop is running.
  2. Verify the candidate's full tree (MANIFEST package.treeSha256, same
     algorithm as tree_sha256 below) and key files, the patched source hashes,
     and that the live release and build stamp still match liveReleaseBefore.
  3. Reflink-back up the live tree and stamp; verify the backup tree.
  4. Stage the candidate beside the live tree; verify the staged tree.
  5. Recheck that no Desktop is running, then swap by two renames.
  6. Write the official build stamp for the patched source (Hermes' own
     _write_desktop_build_stamp, so a plain `hermes desktop` reuses this package)
     and verify the live tree and the stamp.
  7. Only then delete the previous tree. Any failure after the first rename
     restores the previous tree and the original stamp automatically, and the
     deployment record says so.

rollback: the same staged swap in reverse from a backup; --source also
restores the original source files and removes the added ones.

Never starts, stops or signals Hermes Desktop or any other process.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
HOME = Path.home()


class Paths:
    def __init__(self, hermes_root, stamp, backups, records, venv_python=None):
        self.hermes_root = Path(hermes_root)
        self.release = self.hermes_root / 'apps/desktop/release'
        self.live = self.release / 'linux-unpacked'
        self.stamp = Path(stamp)
        self.backups = Path(backups)
        self.records = Path(records)
        self.venv_python = Path(venv_python) if venv_python else self.hermes_root / 'venv/bin/python'


DEFAULT_PATHS = Paths(HOME / '.hermes/hermes-agent', HOME / '.hermes/desktop-build-stamp.json',
                      HOME / '.local/share/soft-launcher-external/hermes-desktop', HERE)


class Refused(Exception):
    """Nothing was changed."""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_sha256(root):
    """Sorted regular files (symlinks skipped): relative path, NUL, raw SHA-256."""
    root = Path(root)
    digest = hashlib.sha256()
    for p in sorted(root.rglob('*')):
        if p.is_file() and not p.is_symlink():
            digest.update(str(p.relative_to(root)).encode() + b'\0' + bytes.fromhex(sha(p)))
    return digest.hexdigest()


def desktop_running():
    found = []
    for entry in Path('/proc').iterdir():
        if entry.name.isdecimal():
            try:
                if os.readlink(entry / 'exe').endswith('/linux-unpacked/Hermes'):
                    found.append(int(entry.name))
            except OSError:
                pass
    return found


def hermes_write_stamp(paths):
    """Write the stamp with Hermes' own helper, then confirm Hermes considers it current."""
    code = ('import sys; from pathlib import Path; import hermes_cli.main_desktop as d; root = Path(sys.argv[1]); '
            'd._write_desktop_build_stamp(root, source_mode=False); '
            "sys.exit(3 if d._desktop_build_needed(root / 'apps' / 'desktop', root, source_mode=False) else 0)")
    subprocess.run([str(paths.venv_python), '-c', code, str(paths.hermes_root)], cwd=paths.hermes_root, check=True, timeout=180,
                   env={**os.environ, 'HERMES_HOME': str(paths.stamp.parent)})


def copy_tree(src, dst):
    subprocess.run(['cp', '-a', '--reflink=auto', str(src), str(dst)], check=True, timeout=900)


def _stamp_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def _record(paths, kind, record):
    paths.records.mkdir(parents=True, exist_ok=True)
    out = paths.records / f"{kind}-{record['at']}.json"
    out.write_text(json.dumps(record, indent=2) + '\n')
    return out


def _restore_stamp(paths, stamp_backup, had_stamp):
    if had_stamp:
        shutil.copy2(stamp_backup, paths.stamp)
    else:
        paths.stamp.unlink(missing_ok=True)


def swap_in(paths, staged, expected_tree, *, running, write_stamp, rename, stamp_backup, had_stamp, record):
    """Two-rename swap of `staged` into the live slot; keep the old tree until everything verifies."""
    old = paths.release / f'.linux-unpacked.previous-{record["at"]}'
    if running():
        shutil.rmtree(staged, ignore_errors=True)
        raise Refused('Hermes Desktop started before the swap; live release unchanged')
    rename(paths.live, old)
    swapped = False
    try:
        rename(staged, paths.live)
        swapped = True
        write_stamp(paths)
        live_tree = tree_sha256(paths.live)
        record['liveTreeAfter'] = live_tree
        if live_tree != expected_tree:
            raise RuntimeError('live tree does not match the expected tree after the swap')
    except BaseException as error:
        record['failure'] = f'{type(error).__name__}: {error}'[:300]
        if swapped:
            failed = paths.release / f'.linux-unpacked.failed-{record["at"]}'
            os.rename(paths.live, failed)
            shutil.rmtree(failed, ignore_errors=True)
        else:
            shutil.rmtree(staged, ignore_errors=True)
        os.rename(old, paths.live)
        _restore_stamp(paths, stamp_backup, had_stamp)
        record['rolledBack'] = True
        record['liveTreeRestored'] = tree_sha256(paths.live)
        raise
    shutil.rmtree(old)


def precheck(manifest, paths, running):
    """Read-only checks shared by `check` and `deploy`; raises Refused."""
    if running():
        raise Refused(f'Hermes Desktop is running ({running()})')
    candidate = Path(manifest['package']['candidateDir']) / 'linux-unpacked'
    if tree_sha256(candidate) != manifest['package']['treeSha256']:
        raise Refused('candidate tree does not match MANIFEST package.treeSha256')
    for rel, want in manifest['package']['files'].items():
        if sha(candidate / rel) != want:
            raise Refused(f'candidate {rel} does not match the manifest')
    for rel, want in manifest['source']['after'].items():
        if sha(paths.hermes_root / rel) != want:
            raise Refused(f'source {rel} does not match the frozen patched source')
    before = manifest['liveReleaseBefore']
    if sha(paths.live / 'Hermes') != before['Hermes'] or sha(paths.live / 'resources/app.asar') != before['resources/app.asar']:
        raise Refused('live release changed since the manifest was frozen')
    if (sha(paths.stamp) if paths.stamp.exists() else None) != before.get('buildStampSha256'):
        raise Refused('build stamp changed since the manifest was frozen')
    return candidate


def check(manifest, paths=DEFAULT_PATHS, *, running=desktop_running):
    try:
        precheck(manifest, paths, running)
    except Refused as error:
        print(json.dumps({'ready': False, 'reason': str(error)}))
        return 2
    print(json.dumps({'ready': True, 'candidateTree': manifest['package']['treeSha256'], 'liveRelease': str(paths.live)}))
    return 0


def deploy(manifest, paths=DEFAULT_PATHS, *, running=desktop_running, write_stamp=hermes_write_stamp, rename=os.rename):
    record = {'at': _stamp_now(), 'action': 'deploy', 'liveRelease': str(paths.live)}
    try:
        candidate = precheck(manifest, paths, running)
        expected_tree = manifest['package']['treeSha256']
        had_stamp = paths.stamp.exists()
    except Refused as error:
        record.update(outcome='refused', reason=str(error))
        print(json.dumps(record, indent=2))
        return 2

    backup = paths.backups / f'release-before-{record["at"]}'
    stamp_backup = backup.with_name(backup.name + '.desktop-build-stamp.json')
    staged = paths.release / f'.linux-unpacked.staged-{record["at"]}'
    record.update(backup=str(backup), stampBackup=str(stamp_backup) if had_stamp else None)
    try:
        backup.mkdir(parents=True)
        live_tree_before = tree_sha256(paths.live)
        copy_tree(paths.live, backup / 'linux-unpacked')
        if had_stamp:
            shutil.copy2(paths.stamp, stamp_backup)
        if tree_sha256(backup / 'linux-unpacked') != live_tree_before:
            raise Refused('backup tree does not match the live tree')
        record['liveTreeBefore'] = live_tree_before
        copy_tree(candidate, staged)
        if tree_sha256(staged) != expected_tree:
            shutil.rmtree(staged, ignore_errors=True)
            raise Refused('staged tree does not match the manifest')
        swap_in(paths, staged, expected_tree, running=running, write_stamp=write_stamp, rename=rename,
                stamp_backup=stamp_backup, had_stamp=had_stamp, record=record)
    except Refused as error:
        record.update(outcome='refused', reason=str(error))
        out = _record(paths, 'deployment', record)
        print(json.dumps({**record, 'record': str(out)}, indent=2))
        return 2
    except BaseException as error:  # noqa: BLE001 - recorded, previous tree restored by swap_in
        record.update(outcome='failed', reason=f'{type(error).__name__}: {error}'[:300])
        out = _record(paths, 'deployment', record)
        print(json.dumps({**record, 'record': str(out)}, indent=2))
        return 1
    record.update(outcome='deployed', stampSha256=sha(paths.stamp), desktopRunningAfter=running())
    out = _record(paths, 'deployment', record)
    print(json.dumps({**record, 'record': str(out)}, indent=2))
    return 0


def rollback(manifest, backup, source, paths=DEFAULT_PATHS, *, running=desktop_running, rename=os.rename):
    record = {'at': _stamp_now(), 'action': 'rollback', 'backup': str(backup)}
    backup = Path(backup)
    stamp_backup = backup.with_name(backup.name + '.desktop-build-stamp.json')
    if running():
        record.update(outcome='refused', reason='Hermes Desktop is running')
        print(json.dumps(record, indent=2))
        return 2
    current_stamp = paths.release / f'.stamp-before-rollback-{record["at"]}.json'
    had_stamp = paths.stamp.exists()
    if had_stamp:
        shutil.copy2(paths.stamp, current_stamp)
    staged = paths.release / f'.linux-unpacked.staged-{record["at"]}'
    try:
        expected = tree_sha256(backup / 'linux-unpacked')
        copy_tree(backup / 'linux-unpacked', staged)
        if tree_sha256(staged) != expected:
            shutil.rmtree(staged, ignore_errors=True)
            raise Refused('staged backup tree does not match the backup')

        def restore_stamp(_paths):
            if stamp_backup.exists():
                shutil.copy2(stamp_backup, paths.stamp)

        swap_in(paths, staged, expected, running=running, write_stamp=restore_stamp, rename=rename,
                stamp_backup=current_stamp, had_stamp=had_stamp, record=record)
    except Refused as error:
        record.update(outcome='refused', reason=str(error))
        print(json.dumps(record, indent=2))
        return 2
    except BaseException as error:  # noqa: BLE001
        record.update(outcome='failed', reason=f'{type(error).__name__}: {error}'[:300])
        _record(paths, 'rollback', record)
        print(json.dumps(record, indent=2))
        return 1
    finally:
        current_stamp.unlink(missing_ok=True)
    if source:
        for rel in manifest['source']['before']:
            shutil.copy2(HERE / 'base' / rel, paths.hermes_root / rel)
        for rel in manifest['source']['added']:
            (paths.hermes_root / rel).unlink(missing_ok=True)
    record.update(outcome='rolled back', sourceRestored=source)
    _record(paths, 'rollback', record)
    print(json.dumps(record, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('check', help='Read-only: verify candidate, patched source, live release and stamp')
    sub.add_parser('deploy')
    rb = sub.add_parser('rollback')
    rb.add_argument('--backup', type=Path, required=True, help='release-before-<stamp> directory printed by deploy')
    rb.add_argument('--source', action='store_true', help='Also restore the original Hermes source files')
    args = parser.parse_args()
    manifest_path = Path(os.environ.get('SOFT_LAUNCHER_HERMES_DEPLOY_MANIFEST', HERE / 'MANIFEST.local.json'))
    manifest = json.loads(manifest_path.read_text())
    if args.cmd == 'check':
        return check(manifest)
    return deploy(manifest) if args.cmd == 'deploy' else rollback(manifest, args.backup, args.source)


if __name__ == '__main__':
    sys.exit(main())
