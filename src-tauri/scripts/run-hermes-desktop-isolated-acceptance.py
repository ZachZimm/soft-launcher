#!/usr/bin/env python3
"""Run hermes-desktop-isolated-acceptance.py only inside a ready hidden agent workspace.

The inner run gets a scrubbed environment (env -i): the workspace's private
Xvfb DISPLAY, private 0700 XDG runtime/config/data/cache/state directories and
Desktop userData under the workspace runtime, a private dbus-run-session bus,
and HERMES_HOME pinned to the real ~/.hermes. The host Wayland socket,
Hyprland signature, session bus, SSH agent and service-manager variables are
never passed. This runner sends no host input, focuses nothing, calls no
service manager and never runs service.sh or session-start.

Stages:
  candidate  HOME is a private directory whose ~/.local/bin/hermes is a
             documented wrapper running a copy of the patched Hermes CLI with
             apps/desktop/release -> the candidate package. The live release
             directory is not touched.
  installed  HOME is the real home: the real ~/.local/bin/hermes, the deployed
             package and the given (installed) launcher and adapter. Nothing on
             the handoff path is substituted.

Host-side checks are read-only: hashes, the Hermes gateway PID and start time,
Hermes checkout HEAD/status/FETCH_HEAD, soft-if counts, absence of a host
Desktop, and that no process carrying this run's marker survives.
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
HERMES_ROOT = HOME / '.hermes/hermes-agent'
LIVE_RELEASE = HERMES_ROOT / 'apps/desktop/release/linux-unpacked'
INSTALLED = HOME / '.local/lib/soft-launcher'
CATALOGUE = HOME / '.config/local.system1.soft-launcher/catalogue.toml'
SOFTIF_DB = HOME / '.local/share/soft-launcher/soft-if.db'
VENV_PYTHON = HERMES_ROOT / 'venv/bin/python'
CLI_COPY_EXCLUDES = ('venv', 'node_modules', 'apps', 'web', 'website', 'ui-tui', 'tests', 'tests-js', '.git', 'docs', 'evals',
                     '__pycache__', 'mcp-research-data', 'datagen-config-examples', 'contributors', 'nix', 'docker')


def sha_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def softif_counts():
    try:
        con = sqlite3.connect(f'file:{SOFTIF_DB}?mode=ro', uri=True, timeout=2)
        try:
            return {t: con.execute(f'select count(*) from {t}').fetchone()[0] for t in ('requests', 'answers', 'outcomes', 'policy_changes')}
        finally:
            con.close()
    except sqlite3.Error as error:
        return {'error': type(error).__name__}


def proc_start(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def exe_of(entry):
    try:
        return os.readlink(entry / 'exe')
    except OSError:
        return None


def git(*args):
    return subprocess.run(['/usr/bin/git', '-C', str(HERMES_ROOT), *args], capture_output=True, text=True, timeout=20).stdout.strip()


def host_snapshot():
    try:
        gw_pid = int(json.loads((HOME / '.hermes/gateway.pid').read_text())['pid'])
    except (OSError, ValueError, KeyError, TypeError):
        gw_pid = None
    procs = [(int(e.name), exe_of(e)) for e in Path('/proc').iterdir() if e.name.isdecimal()]
    return {
        'installedLauncherSha256': sha_file(INSTALLED / 'soft-launcher'),
        'installedAdapterSha256': sha_file(INSTALLED / 'hermes-handoff.py'),
        'catalogueSha256': sha_file(CATALOGUE),
        'hermesConfigSha256': sha_file(HOME / '.hermes/config.yaml'),
        'hermesDesktopEntrySha256': sha_file(HOME / '.local/share/applications/hermes.desktop'),
        'liveDesktopAsarSha256': sha_file(LIVE_RELEASE / 'resources/app.asar'),
        'liveDesktopExeSha256': sha_file(LIVE_RELEASE / 'Hermes'),
        'desktopBuildStampSha256': sha_file(HOME / '.hermes/desktop-build-stamp.json'),
        'hostDesktopUserData': sorted(p.name for p in (HOME / '.config/Hermes').iterdir()) if (HOME / '.config/Hermes').exists() else None,
        'hermesCheckout': {'head': git('rev-parse', 'HEAD'), 'statusSha256': hashlib.sha256(git('status', '--porcelain').encode()).hexdigest(),
                           'fetchHeadMtime': (HERMES_ROOT / '.git/FETCH_HEAD').stat().st_mtime if (HERMES_ROOT / '.git/FETCH_HEAD').exists() else None},
        'gateway': {'pid': gw_pid, 'start': proc_start(gw_pid) if gw_pid else None},
        'hostDesktopProcesses': sorted(p for p, exe in procs if exe and exe.endswith('/linux-unpacked/Hermes')),
        'hostLauncherPids': sorted(p for p, exe in procs if exe == str(INSTALLED / 'soft-launcher')),
        'hostReceipts': sorted(p.name for p in (HOME / '.local/state/soft-launcher/hermes-desktop').iterdir())
        if (HOME / '.local/state/soft-launcher/hermes-desktop').exists() else [],
        'softIfCounts': softif_counts(),
    }


def marked(marker):
    needle = f'SL_HD_QA_RUN={marker}'.encode()
    found = []
    for e in Path('/proc').iterdir():
        if e.name.isdecimal():
            try:
                if needle in (e / 'environ').read_bytes().split(b'\0'):
                    found.append(int(e.name))
            except OSError:
                pass
    return found


def candidate_cli(marker, package_root, profile):
    """A copy of the patched CLI whose apps/desktop/release is the candidate, plus a private wrapper HOME."""
    shim = Path(f'/tmp/slhd-cli-{marker[:8]}')
    excludes = sum((['--exclude', f'/{e}'] for e in CLI_COPY_EXCLUDES), []) + ['--exclude', '__pycache__']
    subprocess.run(['rsync', '-a', *excludes, f'{HERMES_ROOT}/', f'{shim}/'], check=True, timeout=300)
    desktop = shim / 'apps/desktop'
    desktop.mkdir(parents=True)
    shutil.copy2(HERMES_ROOT / 'apps/desktop/package.json', desktop / 'package.json')
    (desktop / 'release').symlink_to(package_root)
    home = profile / 'home'
    wrapper = home / '.local/bin/hermes'
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(f"""#!/bin/sh
# Candidate QA wrapper (not installed): the real patched Hermes CLI, copied to
# {shim} so its apps/desktop/release is the candidate package.
unset PYTHONPATH PYTHONHOME
exec {VENV_PYTHON} {shim}/hermes "$@"
""")
    wrapper.chmod(0o700)
    return shim, home


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('candidate', 'installed'), required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--adapter', type=Path, required=True)
    parser.add_argument('--expect-binary-sha256', required=True)
    parser.add_argument('--expect-adapter-sha256', required=True)
    parser.add_argument('--package', type=Path, help='Candidate electron-builder output directory (contains linux-unpacked/)')
    parser.add_argument('--expect-asar-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--skip-pending-ui', action='store_true', help='Diagnostic only; the result can never count as acceptance')
    args = parser.parse_args()
    status = json.loads(subprocess.check_output([str(CLI), 'workspace', 'status', '--id', args.workspace], timeout=5))
    status = status.get('status', status)
    runtime = Path(status['runtime_dir']).resolve()
    display = status.get('display', '')
    if not status.get('ready') or not status.get('user_acknowledged_hidden_workspace'):
        raise SystemExit('A ready, acknowledged hidden workspace is required')
    if runtime != Path(f'/run/user/{os.getuid()}/agent-workspace-linux') / args.workspace or not display.startswith(':') or display in (':0', ':1'):
        raise SystemExit('Workspace runtime/display could not be verified')
    binary, adapter = args.binary.resolve(strict=True), args.adapter.resolve(strict=True)
    if sha_file(binary) != args.expect_binary_sha256 or sha_file(adapter) != args.expect_adapter_sha256:
        raise SystemExit('Launcher binary/adapter is not the expected cohort')
    if args.stage == 'installed' and (binary != INSTALLED / 'soft-launcher' or adapter != INSTALLED / 'hermes-handoff.py' or args.package):
        raise SystemExit('Installed stage uses only the installed launcher, adapter and live Desktop package')
    package_exe = (args.package.resolve(strict=True) / 'linux-unpacked/Hermes') if args.stage == 'candidate' else LIVE_RELEASE / 'Hermes'
    if sha_file(package_exe.parent / 'resources/app.asar') != args.expect_asar_sha256:
        raise SystemExit('Desktop package is not the expected build')
    before = host_snapshot()
    if before['hostDesktopProcesses']:
        raise SystemExit('A host Hermes Desktop is running; refusing (QA must never touch it)')

    marker = uuid.uuid4().hex
    profile = runtime / f'qa-{marker[:8]}'
    for sub in ('rt', 'config', 'data', 'cache', 'state', 'ud'):
        (profile / sub).mkdir(parents=True, mode=0o700)
    profile.chmod(0o700)
    shim = None
    run_home = HOME
    if args.stage == 'candidate':
        shim, run_home = candidate_cli(marker, args.package.resolve(strict=True), profile)
    inner_out = profile / 'inner.json'
    shots = ROOT / f'src-tauri/visual-qa/hermes-desktop-{args.stage}'
    env = ['env', '-i', f'HOME={run_home}', f"USER={os.environ.get('USER') or getpass.getuser()}", f"LOGNAME={os.environ.get('USER') or getpass.getuser()}",
           'PATH=/usr/local/bin:/usr/bin:/usr/sbin', 'LANG=C.UTF-8', 'SHELL=/bin/bash',
           f'DISPLAY={display}', f"XAUTHORITY={status['xauthority_path']}",
           f'XDG_RUNTIME_DIR={profile / "rt"}', f'XDG_CONFIG_HOME={profile / "config"}', f'XDG_DATA_HOME={profile / "data"}',
           f'XDG_CACHE_HOME={profile / "cache"}', f'XDG_STATE_HOME={profile / "state"}',
           f'HERMES_HOME={HOME / ".hermes"}', f'HERMES_DESKTOP_USER_DATA_DIR={profile / "ud"}', 'HERMES_DESKTOP_DISABLE_GPU=1',
           'LIBSEAT_BACKEND=noop', 'GIO_USE_VFS=local', 'GVFS_DISABLE_FUSE=1', 'NO_AT_BRIDGE=1', 'GTK_A11Y=none',
           'SL_HD_QA=1', f'SL_HD_QA_RUN={marker}']
    env.append('SOFT_LAUNCHER_QA_CLEF_HOST=' + os.environ.get('SOFT_LAUNCHER_QA_CLEF_HOST', '127.0.0.1:11434'))
    env.append(f'SOFT_LAUNCHER_QA_REAL_HOME={HOME}')
    command = [str(CLI), 'workspace', 'run', '--id', args.workspace, '--name', f'hermes-desktop-{args.stage}-acceptance',
               '--cwd', str(ROOT), '--timeout-ms', '1080000', '--kill-on-timeout', '--tail-bytes', '6000', '--',
               *env, 'dbus-run-session', '--', sys.executable, '-I', str(ROOT / 'src-tauri/scripts/hermes-desktop-isolated-acceptance.py'),
               '--stage', args.stage, '--display', display, '--workspace-runtime', str(runtime), '--profile-dir', str(profile),
               '--marker', marker, '--binary', str(binary), '--adapter', str(adapter), '--catalogue', str(CATALOGUE),
               '--desktop-exe', str(package_exe), '--shots', str(shots), '--output', str(inner_out)] + (['--skip-pending-ui'] if args.skip_pending_ui else [])
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=1140)
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
        'workspaceRunSucceeded': bool(reply.get('succeeded')),
        'innerStdoutTail': reply.get('stdout', {}).get('content', '')[-1500:],
        'innerStderrTail': reply.get('stderr', {}).get('content', '')[-1500:],
        'scrubbedEnvironment': [e.split('=', 1)[0] for e in env[2:]],
        'candidateCliCopy': str(shim) if shim else None,
        'hostBefore': before, 'hostAfter': after,
        'installedUnchanged': same('installedLauncherSha256', 'installedAdapterSha256', 'catalogueSha256'),
        'hermesConfigUnchanged': same('hermesConfigSha256'),
        'hostDesktopEntryUnchanged': same('hermesDesktopEntrySha256'),
        'liveDesktopPackageUnchanged': same('liveDesktopAsarSha256', 'liveDesktopExeSha256', 'desktopBuildStampSha256'),
        'hostDesktopUserDataUnchanged': same('hostDesktopUserData'),
        'hermesCheckoutUnchanged': same('hermesCheckout'),
        'gatewayUnchanged': same('gateway') and before['gateway']['pid'] is not None,
        'noHostDesktop': not after['hostDesktopProcesses'],
        'hostLauncherUnchanged': same('hostLauncherPids'),
        'hostReceiptsUnchanged': same('hostReceipts'),
        'softIfCountsUnchanged': same('softIfCounts'),
        'survivingMarkedProcesses': survivors,
    }
    shutil.rmtree(profile, ignore_errors=True)
    if shim:
        shutil.rmtree(shim, ignore_errors=True)
    evidence['runner']['privateProfileRemoved'] = not profile.exists() and not (shim and shim.exists())
    host_keys = ('installedUnchanged', 'hermesConfigUnchanged', 'hostDesktopEntryUnchanged', 'liveDesktopPackageUnchanged',
                 'hostDesktopUserDataUnchanged', 'hermesCheckoutUnchanged', 'gatewayUnchanged', 'noHostDesktop',
                 'hostLauncherUnchanged', 'hostReceiptsUnchanged', 'softIfCountsUnchanged', 'privateProfileRemoved')
    host_ok = all(evidence['runner'][k] for k in host_keys) and not survivors
    evidence['runner']['hostOk'] = host_ok
    evidence['passed'] = bool(evidence.get('passed')) and host_ok and not args.skip_pending_ui
    if args.skip_pending_ui:
        evidence['diagnosticOnly'] = True
    args.output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'passed': evidence['passed'], 'checks': len(evidence.get('checks', [])), 'error': evidence.get('error'),
                      'failure': evidence.get('failure'), 'hostOk': host_ok,
                      'hostFailures': [k for k in host_keys if not evidence['runner'][k]], 'survivors': survivors,
                      'output': str(args.output.resolve())}, indent=2, ensure_ascii=False)[:4000])
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
