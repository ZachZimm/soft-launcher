#!/usr/bin/env python3
"""Run routing-ui-isolated-qa.py only inside a ready hidden agent workspace.

The inner run gets a scrubbed environment (env -i): the workspace's private
Xvfb DISPLAY, a private HOME and 0700 XDG runtime/config/data/cache/state
directories under the workspace runtime, system-only XDG_DATA_DIRS and a
private dbus-run-session bus. The host Wayland socket, Hyprland signature,
session bus, SSH agent and service-manager variables are never passed. This
runner sends no host input, focuses nothing, calls no service manager and runs
neither service.sh nor session-start. It never starts Hermes or any action.

Stages pin the binary by SHA-256:
  candidate  a frozen build cohort outside ~/.local/lib.
  installed  only ~/.local/lib/soft-launcher/soft-launcher.

Host checks are read-only: installed launcher/adapter/catalogue/policy and
UI-relevant settings hashes, soft-if row counts, Hermes session count
(metadata only), host launcher PIDs, Hermes receipts, and no surviving
process carrying this run's marker.
"""
import argparse
import getpass
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
CLI = Path.home() / '.local/bin/agent-workspace-linux'
HOME = Path.home()
INSTALLED = HOME / '.local/lib/soft-launcher'
CONFIG = HOME / '.config/local.system1.soft-launcher'
CATALOGUE = CONFIG / 'catalogue.toml'
SOFTIF_DB = HOME / '.local/share/soft-launcher/soft-if.db'
HERMES_DB = HOME / '.hermes/state.db'


def sha_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def counts(db, tables):
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=2)
        try:
            return {t: con.execute(f'select count(*) from {t}').fetchone()[0] for t in tables}
        finally:
            con.close()
    except sqlite3.Error as error:
        return {'error': type(error).__name__}


def exe_of(entry):
    try:
        return os.readlink(entry / 'exe')
    except OSError:
        return None


def host_snapshot():
    procs = [(int(e.name), exe_of(e)) for e in Path('/proc').iterdir() if e.name.isdecimal()]
    receipts = HOME / '.local/state/soft-launcher/hermes-desktop'
    return {
        'installedLauncherSha256': sha_file(INSTALLED / 'soft-launcher'),
        'installedAdapterSha256': sha_file(INSTALLED / 'hermes-handoff.py'),
        'catalogueSha256': sha_file(CATALOGUE),
        'configFiles': {p.name: sha_file(p) for p in sorted(CONFIG.iterdir()) if p.is_file()} if CONFIG.is_dir() else None,
        'softIfCounts': counts(SOFTIF_DB, ('requests', 'answers', 'outcomes', 'policy_changes')),
        'hermesSessionCount': counts(HERMES_DB, ('sessions',)),
        'hostLauncherPids': sorted(p for p, exe in procs if exe == str(INSTALLED / 'soft-launcher')),
        'hostDesktopProcesses': sorted(p for p, exe in procs if exe and exe.endswith('/linux-unpacked/Hermes')),
        'hostReceipts': sorted(p.name for p in receipts.iterdir()) if receipts.exists() else [],
    }


def marked(marker):
    needle = f'SL_RT_QA_RUN={marker}'.encode()
    found = []
    for e in Path('/proc').iterdir():
        if e.name.isdecimal():
            try:
                if needle in (e / 'environ').read_bytes().split(b'\0'):
                    found.append(int(e.name))
            except OSError:
                pass
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('candidate', 'installed'), required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--expect-binary-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    status = json.loads(subprocess.check_output([str(CLI), 'workspace', 'status', '--id', args.workspace], timeout=5))
    status = status.get('status', status)
    runtime = Path(status['runtime_dir']).resolve()
    display = status.get('display', '')
    if not status.get('ready') or not status.get('user_acknowledged_hidden_workspace'):
        raise SystemExit('A ready, acknowledged hidden workspace is required')
    if runtime != Path(f'/run/user/{os.getuid()}/agent-workspace-linux') / args.workspace or not display.startswith(':') or display in (':0', ':1'):
        raise SystemExit('Workspace runtime/display could not be verified')
    binary = args.binary.resolve(strict=True)
    if sha_file(binary) != args.expect_binary_sha256:
        raise SystemExit('Launcher binary is not the expected build')
    if (args.stage == 'installed') != (binary == INSTALLED / 'soft-launcher'):
        raise SystemExit('Installed stage uses only the installed launcher; candidate stage never does')
    before = host_snapshot()

    marker = uuid.uuid4().hex
    profile = runtime / f'rtqa-{marker[:8]}'
    for sub in ('rt', 'config', 'data', 'cache', 'state', 'home'):
        (profile / sub).mkdir(parents=True, mode=0o700)
    profile.chmod(0o700)
    inner_out = profile / 'inner.json'
    shots = ROOT / f'src-tauri/visual-qa/routing-ui-{args.stage}'
    user = os.environ.get('USER') or getpass.getuser()
    env = ['env', '-i', f'HOME={profile / "home"}', f'USER={user}', f'LOGNAME={user}',
           'PATH=/usr/local/bin:/usr/bin:/usr/sbin', 'LANG=C.UTF-8', 'SHELL=/bin/bash',
           f'DISPLAY={display}', f"XAUTHORITY={status['xauthority_path']}",
           f'XDG_RUNTIME_DIR={profile / "rt"}', f'XDG_CONFIG_HOME={profile / "config"}', f'XDG_DATA_HOME={profile / "data"}',
           f'XDG_CACHE_HOME={profile / "cache"}', f'XDG_STATE_HOME={profile / "state"}', 'XDG_DATA_DIRS=/usr/local/share:/usr/share',
           'LIBSEAT_BACKEND=noop', 'GIO_USE_VFS=local', 'GVFS_DISABLE_FUSE=1', 'NO_AT_BRIDGE=1', 'GTK_A11Y=none',
           'SL_RT_QA=1', f'SL_RT_QA_RUN={marker}']
    env.append('SOFT_LAUNCHER_QA_CLEF_HOST=' + os.environ.get('SOFT_LAUNCHER_QA_CLEF_HOST', '127.0.0.1:11434'))
    command = [str(CLI), 'workspace', 'run', '--id', args.workspace, '--name', f'routing-ui-{args.stage}-qa',
               '--cwd', str(ROOT), '--timeout-ms', '600000', '--kill-on-timeout', '--tail-bytes', '6000', '--',
               *env, 'dbus-run-session', '--', sys.executable, '-I', str(ROOT / 'src-tauri/scripts/routing-ui-isolated-qa.py'),
               '--stage', args.stage, '--display', display, '--workspace-runtime', str(runtime), '--profile-dir', str(profile),
               '--marker', marker, '--binary', str(binary), '--catalogue', str(CATALOGUE), '--shots', str(shots), '--output', str(inner_out)]
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=660)
    try:
        reply = json.loads(result.stdout)
    except json.JSONDecodeError:
        reply = {'succeeded': False, 'stdout': {'content': result.stdout[-2000:]}, 'stderr': {'content': result.stderr[-2000:]}}
    time.sleep(2)
    survivors = marked(marker)
    evidence = json.loads(inner_out.read_text()) if inner_out.exists() else {'passed': False, 'error': 'inner run wrote no evidence'}
    after = host_snapshot()
    same = lambda *keys: all(before[k] == after[k] for k in keys)  # noqa: E731
    evidence['runner'] = {
        'workspace': args.workspace, 'display': display, 'startedAt': started, 'stage': args.stage,
        'expectedBinarySha256': args.expect_binary_sha256,
        'workspaceRunSucceeded': bool(reply.get('succeeded')),
        'innerStdoutTail': reply.get('stdout', {}).get('content', '')[-1500:],
        'innerStderrTail': reply.get('stderr', {}).get('content', '')[-1500:],
        'scrubbedEnvironment': [e.split('=', 1)[0] for e in env[2:]],
        'hostBefore': before, 'hostAfter': after,
        'installedUnchanged': same('installedLauncherSha256', 'installedAdapterSha256', 'catalogueSha256', 'configFiles'),
        'softIfCountsUnchanged': same('softIfCounts'),
        'hermesSessionsUnchanged': same('hermesSessionCount'),
        'hostLauncherUnchanged': same('hostLauncherPids'),
        'hostReceiptsUnchanged': same('hostReceipts'),
        'survivingMarkedProcesses': survivors,
    }
    shutil.rmtree(profile, ignore_errors=True)
    evidence['runner']['privateProfileRemoved'] = not profile.exists()
    host_keys = ('installedUnchanged', 'softIfCountsUnchanged', 'hermesSessionsUnchanged', 'hostLauncherUnchanged',
                 'hostReceiptsUnchanged', 'privateProfileRemoved')
    host_ok = all(evidence['runner'][k] for k in host_keys) and not survivors
    evidence['runner']['hostOk'] = host_ok
    evidence['passed'] = bool(evidence.get('passed')) and host_ok and evidence.get('binarySha256') == args.expect_binary_sha256
    args.output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'passed': evidence['passed'], 'checks': len(evidence.get('checks', [])), 'error': evidence.get('error'),
                      'failure': evidence.get('failure'), 'hostOk': host_ok,
                      'hostFailures': [k for k in host_keys if not evidence['runner'][k]], 'survivors': survivors,
                      'output': str(args.output.resolve())}, indent=2, ensure_ascii=False)[:5000])
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
