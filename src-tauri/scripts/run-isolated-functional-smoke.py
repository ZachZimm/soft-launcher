#!/usr/bin/env python3
"""Run the native functional smoke only through a provisioned hidden workspace."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CLI = Path.home() / '.local/bin/agent-workspace-linux'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--binary', type=Path, default=ROOT / 'src-tauri/target/release/soft-launcher')
    parser.add_argument('--output', type=Path, default=ROOT / 'src-tauri/desktop-functional-isolated-green.json')
    parser.add_argument('--expect-cold', action='store_true')
    args = parser.parse_args()
    state = json.loads(subprocess.check_output([str(CLI), 'workspace', 'status', '--id', args.workspace], timeout=5))
    status = state.get('status', state)
    if not status.get('ready') or not status.get('user_acknowledged_hidden_workspace'):
        raise SystemExit('A ready, acknowledged hidden workspace is required')
    runtime = Path(status['runtime_dir']).resolve()
    expected = Path(f'/run/user/{os.getuid()}/agent-workspace-linux') / args.workspace
    if runtime != expected or not status.get('display', '').startswith(':'):
        raise SystemExit('Workspace runtime/display could not be verified')
    command = [str(CLI), 'workspace', 'run', '--id', args.workspace,
               '--name', 'native-functional-smoke', '--ack-unenforced-policy',
               '--cwd', str(ROOT), '--timeout-ms', '100000', '--kill-on-timeout',
               '--tail-bytes', '12000', '--', 'env', '-u', 'WAYLAND_DISPLAY',
               '-u', 'HYPRLAND_INSTANCE_SIGNATURE', '-u', 'DBUS_SESSION_BUS_ADDRESS',
               f'XDG_RUNTIME_DIR={runtime}', 'GDK_BACKEND=x11',
               'SOFT_LAUNCHER_ISOLATED_QA=1', 'dbus-run-session', '--',
               sys.executable, str(ROOT / 'src-tauri/scripts/desktop-functional-smoke.py'),
               '--isolated-display', '--expect-production', '--binary', str(args.binary.resolve()),
               '--output', str(args.output.resolve())]
    if args.expect_cold:
        command.append('--expect-cold')
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    try:
        reply = json.loads(result.stdout)
    except json.JSONDecodeError:
        print('Workspace runner returned an unreadable result', file=sys.stderr)
        return 1
    if args.output.resolve().exists():
        evidence = json.loads(args.output.resolve().read_text())
        print(json.dumps({'passed': evidence.get('passed'), 'binarySha256': evidence.get('binarySha256'),
                          'origin': evidence.get('origin'), 'display': evidence.get('display'),
                          'checks': len(evidence.get('checks', [])), 'error': evidence.get('error'),
                          'artifact': str(args.output.resolve())}, indent=2))
    else:
        print('No functional evidence artifact was produced', file=sys.stderr)
    if not reply.get('succeeded'):
        print(reply.get('stderr', {}).get('content', '')[-2000:], file=sys.stderr)
    return 0 if reply.get('succeeded') else 1


if __name__ == '__main__':
    raise SystemExit(main())
