#!/usr/bin/env python3
"""Run app-ux-isolated-qa.py only through the provisioned hidden workspace."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CLI = Path.home() / '.local/bin/agent-workspace-linux'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--binary', type=Path, default=ROOT / 'src-tauri/target/release/soft-launcher')
    parser.add_argument('--output', type=Path, default=ROOT / 'src-tauri/app-ux-isolated-qa.json')
    args = parser.parse_args()
    state = json.loads(subprocess.check_output([str(CLI), 'workspace', 'status', '--id', args.workspace], timeout=5))
    status = state.get('status', state)
    if not status.get('ready') or not status.get('user_acknowledged_hidden_workspace'):
        raise SystemExit('A ready, acknowledged hidden workspace is required')
    runtime = Path(status['runtime_dir']).resolve()
    if runtime != Path(f'/run/user/{os.getuid()}/agent-workspace-linux') / args.workspace or not status.get('display', '').startswith(':'):
        raise SystemExit('Workspace runtime/display could not be verified')
    profile = Path(tempfile.mkdtemp(prefix='soft-launcher-app-qa-'))
    command = [str(CLI), 'workspace', 'run', '--id', args.workspace, '--name', 'app-ux-isolated-qa',
               '--ack-unenforced-policy', '--cwd', str(ROOT), '--timeout-ms', '150000', '--kill-on-timeout',
               '--tail-bytes', '12000', '--', 'env', '-u', 'WAYLAND_DISPLAY', '-u', 'HYPRLAND_INSTANCE_SIGNATURE',
               '-u', 'DBUS_SESSION_BUS_ADDRESS', f'XDG_RUNTIME_DIR={runtime}', 'GDK_BACKEND=x11',
               'SOFT_LAUNCHER_ISOLATED_QA=1', 'dbus-run-session', '--', sys.executable,
               str(ROOT / 'src-tauri/scripts/app-ux-isolated-qa.py'), '--isolated-display',
               '--binary', str(args.binary.resolve()), '--output', str(args.output.resolve()), '--profile-dir', str(profile)]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    try:
        reply = json.loads(result.stdout)
    except json.JSONDecodeError:
        print('Workspace runner returned an unreadable result', file=sys.stderr)
        print(result.stdout[-2000:], result.stderr[-2000:], file=sys.stderr)
        return 1
    print(reply.get('stdout', {}).get('content', '')[-3000:])
    if not reply.get('succeeded'):
        print(reply.get('stderr', {}).get('content', '')[-3000:], file=sys.stderr)
    subprocess.run(['rm', '-rf', '--', str(profile)], check=False)
    return 0 if reply.get('succeeded') else 1


if __name__ == '__main__':
    raise SystemExit(main())
