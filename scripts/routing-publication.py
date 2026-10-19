#!/usr/bin/env python3
"""Publish a frozen, QA-accepted cohort using the existing installer.

No GUI input or inference. This script requires an explicit QA evidence path.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
RUNTIME = HOME / '.local/lib/soft-launcher'
NATIVE_SETTINGS = ('SOFT_LAUNCHER_CODEX_SSH_HOST', 'SOFT_LAUNCHER_CODEX_BRIDGE', 'SOFT_LAUNCHER_CODEX_MODEL', 'SOFT_LAUNCHER_CLEF_URL')
sys.path.insert(0, str(ROOT / 'scripts/services'))
import install
import layer_shell


class PublicationVerificationError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise PublicationVerificationError(message)


def validate_qa_evidence(path, candidate_sha256):
    path = Path(path)
    require(path.is_file(), 'QA evidence is required')
    try:
        evidence = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PublicationVerificationError('QA evidence must be readable JSON') from error
    require(isinstance(evidence, dict), 'QA evidence must be an object')
    require(evidence.get('scope') == 'candidate', 'QA evidence must describe candidate acceptance')
    require(evidence.get('passed') is True, 'QA evidence did not pass')
    require(evidence.get('binarySha256') == candidate_sha256, 'QA evidence candidate SHA256 mismatch')
    return evidence


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def stamp(path):
    path = Path(path)
    return {'sha256': sha(path), 'mode': oct(path.stat().st_mode & 0o777)} if path.exists() else None


def native_settings_sha(pid, defaults=None, process_root=Path('/proc')):
    environment = dict(item.decode().split('=', 1) for item in (process_root / str(pid) / 'environ').read_bytes().split(b'\0') if b'=' in item)
    settings = {key: environment.get(key, (defaults or {}).get(key)) for key in NATIVE_SETTINGS}
    require(all(isinstance(value, str) and value for value in settings.values()), 'Effective native configuration is incomplete')
    return hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def launcher_processes(process_root=Path('/proc')):
    result = {}
    for process in process_root.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            executable = (process / 'exe').resolve(strict=True)
            if executable.name != 'soft-launcher':
                continue
            fields = (process / 'stat').read_text().rsplit(')', 1)[1].split()
            result[int(process.name)] = {'pid': int(process.name), 'startTicks': int(fields[19]),
                'exePath': str(executable), 'exeSha256': sha(process / 'exe'),
                'argvSha256': hashlib.sha256((process / 'cmdline').read_bytes()).hexdigest()}
        except (OSError, ValueError, IndexError):
            continue
    return result


def verify_singleton(pid, processes=None):
    require(set(launcher_processes() if processes is None else processes) == {int(pid)}, 'Another launcher instance conflicts with the managed service')


def retire_singleton(expected, quiet, timeout_seconds=2):
    require(isinstance(expected, dict) and set(expected) == {'pid', 'startTicks', 'exePath', 'exeSha256', 'argvSha256'}, 'Invalid singleton replacement identity')
    require(type(expected['pid']) is int and expected['pid'] > 0 and type(expected['startTicks']) is int and expected['startTicks'] > 0, 'Invalid singleton replacement process')
    require(launcher_processes() == {expected['pid']: expected}, 'Authorized singleton identity changed')
    descriptor = os.pidfd_open(expected['pid'])
    try:
        # Observe a quiet hidden interval; never infer permission from the exe name.
        for _ in range(10):
            require(quiet(), 'Launcher is visible or user interaction is underway')
            time.sleep(0.05)
        require(launcher_processes() == {expected['pid']: expected} and quiet(), 'Authorized singleton identity or visibility changed')
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        poll = select.poll(); poll.register(descriptor, select.POLLIN)
        require(bool(poll.poll(round(timeout_seconds * 1000))), 'Authorized singleton did not exit after SIGTERM')
        require(not launcher_processes(), 'A new launcher instance appeared during replacement')
    finally:
        os.close(descriptor)


def recover_inactive_baseline(update, before, manifest, migration, replacement):
    require(before['services']['soft-launcher.service']['MainPID'] == '0', 'Singleton recovery requires the reviewed inactive service baseline')
    update.prepare()
    require(not update.active_before['soft-launcher.service'], 'Service activity changed before recovery')
    def quiet():
        current = capture(manifest)
        return current['mappedLauncherWindows'] == {'layers': 0, 'toplevels': 0} and current['databaseCounts'] == before['databaseCounts']
    retire_singleton(replacement, quiet)
    text = install.load_backend_environment(migration['backendEnvSource'], migration['after']['backendOverride']['sha256'])
    update.stage_backend_environment(text)
    update.require_safe_stop()
    update.note_service_change('soft-launcher.service')
    install.ctl('start', 'soft-launcher.service')
    baseline = wait_for_launcher(manifest['binarySha256'])
    verify_singleton(baseline['pid'])
    require(native_settings_sha(baseline['pid']) == migration['effectiveNativeSha256'], 'Recovered live baseline native configuration changed')
    require(quiet(), 'User interaction began during baseline recovery')
    return baseline


def validate_configuration_migration(plan, before):
    require(isinstance(plan, dict) and set(plan) == {'before', 'after', 'backendEnvSource', 'baselineBinarySha256', 'baselineNativeDefaults', 'effectiveNativeSha256'}, 'Invalid configuration migration plan')
    keys = {'launcherUnit', 'backendOverride'}
    for phase in ('before', 'after'):
        require(isinstance(plan[phase], dict) and set(plan[phase]) == keys, 'Configuration migration may change only launcherUnit and backendOverride')
        for value in plan[phase].values():
            require(value is None or isinstance(value, dict) and set(value) == {'sha256', 'mode'}
                    and isinstance(value['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', value['sha256'])
                    and value['mode'] in {'0o600', '0o640', '0o644'}, 'Invalid configuration file stamp')
    require(plan['before'] == {key: before['protectedFiles'][key] for key in keys}, 'Configuration migration baseline stamp mismatch')
    require(plan['baselineBinarySha256'] == before['binarySha256'], 'Configuration migration baseline binary mismatch')
    require(isinstance(plan['effectiveNativeSha256'], str) and re.fullmatch(r'[0-9a-f]{64}', plan['effectiveNativeSha256']), 'Invalid effective native configuration hash')
    require(isinstance(plan['baselineNativeDefaults'], dict) and set(plan['baselineNativeDefaults']).issubset(NATIVE_SETTINGS)
            and all(isinstance(value, str) and value for value in plan['baselineNativeDefaults'].values()), 'Invalid baseline native defaults')
    source = Path(plan['backendEnvSource'])
    require(source.is_absolute() and source.is_file() and source.stat().st_mode & 0o777 == 0o600, 'Staged backend environment must be an absolute mode-0600 file')
    require(plan['after']['backendOverride'] == stamp(source) and plan['after']['launcherUnit'] is not None, 'Configuration migration expected stamp mismatch')


def validate_hotkey_migration(plan, before):
    require(isinstance(plan, dict) and set(plan) == {'before', 'after'}, 'Invalid hotkey guard migration')
    require(plan['before'] == before['protectedFiles']['toggleWrapper'], 'Hotkey guard baseline stamp mismatch')
    expected = {'sha256': hashlib.sha256(install.toggle_script().encode()).hexdigest(), 'mode': '0o755'}
    require(plan['after'] == expected, 'Hotkey guard expected stamp mismatch')


def verify_protected_files(before, after, plan=None, hotkey=None):
    expected = dict(before['protectedFiles'])
    if plan is not None:
        expected.update(plan['after'])
    if hotkey is not None:
        expected['toggleWrapper'] = hotkey['after']
    require(after['protectedFiles'] == expected, 'Protected file/mode changed outside the exact configuration plan')


def service(name):
    raw = subprocess.check_output(['systemctl', '--user', 'show', name, '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID', '-p', 'UnitFileState'], text=True)
    return dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)


def sql(path):
    return sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)


def settings_digest():
    path = HOME / '.local/share/local.system1.soft-launcher/localstorage/tauri_localhost_0.localstorage'
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with sql(path) as connection:
        for row in connection.execute('SELECT key,value FROM ItemTable ORDER BY key'):
            for value in row:
                data = value.encode() if isinstance(value, str) else bytes(value)
                digest.update(len(data).to_bytes(8, 'big'))
                digest.update(data)
    return {'logicalSha256': digest.hexdigest(), 'mode': oct(path.stat().st_mode & 0o777)}


def mapped_launcher_windows():
    # Read-only compositor inventory. Never retain other apps' titles/identities.
    layers = json.loads(subprocess.check_output(['hyprctl', 'layers', '-j']))
    clients = json.loads(subprocess.check_output(['hyprctl', 'clients', '-j']))
    layer_count = sum(1 for monitor in layers.values() for collection in monitor.get('levels', {}).values() for layer in collection if 'soft-launcher' in layer.get('namespace', ''))
    client_count = sum(1 for client in clients if 'soft-launcher' in client.get('class', '') or 'soft-launcher' in client.get('initialClass', ''))
    return {'layers': layer_count, 'toplevels': client_count}


def capture(manifest):
    paths = {
        'catalogue': Path(manifest['catalogue']), 'policy': Path(manifest['policy']),
        'backendOverride': HOME / '.config/local.system1.soft-launcher/backend.env',
        'hermesConfig': HOME / '.hermes/config.yaml',
        'desktopEntry': HOME / '.local/share/applications/hermes.desktop',
        'launcherUnit': HOME / '.config/systemd/user/soft-launcher.service',
        'bridgeUnit': HOME / '.config/systemd/user/soft-if.service',
        'wrapper': HOME / '.local/bin/soft-launcher',
        'toggleWrapper': RUNTIME / 'toggle',
    }
    with sql(manifest['database']) as connection:
        counts = {table: connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in ('requests', 'answers', 'outcomes', 'policy_changes', 'branches')}
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
    return {'capturedAt': datetime.now(timezone.utc).isoformat(), 'binarySha256': sha(RUNTIME / 'soft-launcher'), 'adapterSha256': sha(RUNTIME / 'hermes-handoff.py'), 'protectedFiles': {key: stamp(path) for key, path in paths.items()}, 'settings': settings_digest(), 'databaseCounts': counts, 'databaseIntegrity': integrity, 'databaseIdentity': Path(manifest['database']).stat().st_ino, 'services': {name: service(name) for name in install.NAMES}, 'mappedLauncherWindows': mapped_launcher_windows()}


def verify_frozen(frozen):
    for path, digest in frozen['sourceFiles'].items():
        require(sha(ROOT / path) == digest, f'Source changed after QA: {path}')
    for path, digest in frozen['installedSoftIfFiles'].items():
        require(sha(ROOT / 'node_modules/soft-if/dist' / path) == digest, f'Frontend soft-if changed: {path}')
    candidate = frozen['productionCandidate']
    binary = Path(candidate['cohort']) / 'soft-launcher'
    require(sha(binary) == candidate['binarySha256'], "Publication verification failed: sha(binary) == candidate['binarySha256']")
    layer_shell.validate_bundle(binary.parent / 'lib')
    return binary


def wait_for_launcher(candidate_sha256, timeout_seconds=5, poll_seconds=0.05, process_root=Path('/proc')):
    """Wait only for one active service process to finish mapping its bundle."""
    started = time.monotonic()
    expected_pid = None
    polls = 0
    while True:
        polls += 1
        values = service('soft-launcher.service')
        pid = values.get('MainPID', '0')
        active = values.get('ActiveState') == 'active' and values.get('SubState') == 'running' and pid.isdigit() and int(pid) > 0
        if expected_pid is not None:
            require(active and pid == expected_pid, 'Launcher process changed during readiness verification')
        if active:
            expected_pid = pid
            process = process_root / pid
            try:
                executable = sha(process / 'exe')
                maps = (process / 'maps').read_text()
            except OSError:
                pass  # The same process may still be starting; keep the bound.
            else:
                require(executable == candidate_sha256, 'Running launcher does not match the frozen candidate')
                if str(RUNTIME / 'lib/libgtk-layer-shell.so.0.10.1') in maps:
                    return {'pid': pid, 'elapsedMs': round((time.monotonic() - started) * 1000), 'polls': polls, 'deadlineMs': round(timeout_seconds * 1000)}
        remaining = timeout_seconds - (time.monotonic() - started)
        require(remaining > 0, 'Launcher readiness timed out before the verified library was mapped')
        time.sleep(min(poll_seconds, remaining))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qa-evidence', required=True, type=Path)
    parser.add_argument('--qa-accepted', required=True, action='store_true', help='Only after explicit Opus QA pass for this frozen candidate')
    parser.add_argument('--freeze', type=Path, default=ROOT / 'src-tauri/routing-frozen-build.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'src-tauri/routing-publication.json')
    args = parser.parse_args()
    require(not args.output.exists(), 'Refusing to overwrite a historical publication receipt')
    freeze = json.loads(args.freeze.read_text())
    migration = freeze.get('configurationMigration')
    replacement = freeze.get('singletonReplacement')
    hotkey = freeze.get('hotkeyGuardMigration')
    require(replacement is None or migration is not None, 'Singleton replacement requires the reviewed configuration migration')
    require(hotkey is None or migration is not None, 'Hotkey migration requires the reviewed configuration migration')
    if migration is not None:
        require(args.freeze.stat().st_mode & 0o777 == 0o600, 'Configuration migration evidence must have mode 0600')
    validate_qa_evidence(args.qa_evidence, freeze['productionCandidate']['binarySha256'])
    binary = verify_frozen(freeze)
    old_manifest = json.loads((RUNTIME / 'install.json').read_text())
    before = capture(old_manifest)
    require(before['mappedLauncherWindows'] == {'layers': 0, 'toplevels': 0}, 'Refusing publication while the launcher is visible')
    if hotkey is not None:
        validate_hotkey_migration(hotkey, before)
    if migration is not None:
        validate_configuration_migration(migration, before)
        pid = before['services']['soft-launcher.service']['MainPID']
        if replacement is None:
            require(native_settings_sha(pid, migration['baselineNativeDefaults']) == migration['effectiveNativeSha256'], 'Baseline effective native configuration changed')
    result = {'startedAt': datetime.now(timezone.utc).isoformat(), 'freeze': str(args.freeze), 'freezeSha256': sha(args.freeze), 'qaEvidence': str(args.qa_evidence), 'qaEvidenceSha256': sha(args.qa_evidence), 'guiInput': False, 'inferenceRequested': False, 'before': before}
    installer_args = SimpleNamespace(skip_build=True, binary=str(binary), sha256=freeze['productionCandidate']['binarySha256'], no_start=False, hyprland_hook=False)
    if migration is not None:
        installer_args.backend_env = migration['backendEnvSource']
        installer_args.backend_env_sha256 = migration['after']['backendOverride']['sha256']
    with install.RuntimeUpdate() as update:
        if migration is not None:
            update.expected_files = {
                install.UNITS / 'soft-launcher.service': migration['before']['launcherUnit'],
                install.CONFIG / 'local.system1.soft-launcher/backend.env': migration['before']['backendOverride'],
            }
        if hotkey is not None:
            update.expected_files[install.RUNTIME / 'toggle'] = hotkey['before']
        if replacement is not None:
            result['baselineRecovery'] = recover_inactive_baseline(update, before, old_manifest, migration, replacement)
        install._install(installer_args, update)
        current = json.loads((RUNTIME / 'install.json').read_text())
        readiness = wait_for_launcher(current['binarySha256'])
        verify_singleton(readiness['pid'])
        after = capture(current)
        require(after['services']['soft-launcher.service']['MainPID'] == readiness['pid'], 'Launcher process changed after readiness verification')
        require(after['binarySha256'] == current['binarySha256'] == freeze['productionCandidate']['binarySha256'], "Publication verification failed: after['binarySha256'] == current['binarySha256'] == freeze['productionCandidate']['binarySha256']")
        require(after['adapterSha256'] == current['hermesAdapterSha256'] == before['adapterSha256'], "Publication verification failed: after['adapterSha256'] == current['hermesAdapterSha256'] == before['adapterSha256']")
        require(current['hermesHandoffProtocol'] == 'desktop-file-socket-v1', "Publication verification failed: current['hermesHandoffProtocol'] == 'desktop-file-socket-v1'")
        verify_protected_files(before, after, migration, hotkey)
        if migration is not None:
            require(native_settings_sha(readiness['pid']) == migration['effectiveNativeSha256'], 'Effective native configuration changed after publication')
        require(after['settings'] == before['settings'], 'UI/backend localStorage changed')
        require(after['databaseCounts'] == before['databaseCounts'], 'Unexpected database writes during hidden publication')
        require(after['databaseIdentity'] == before['databaseIdentity'] and after['databaseIntegrity'] == 'ok', "Publication verification failed: after['databaseIdentity'] == before['databaseIdentity'] and after['databaseIntegrity'] == 'ok'")
        require(after['mappedLauncherWindows'] == {'layers': 0, 'toplevels': 0}, "Publication verification failed: after['mappedLauncherWindows'] == {'layers': 0, 'toplevels': 0}")
        for name, values in after['services'].items():
            require(values['ActiveState'] == 'active' and values['SubState'] == 'running' and (values['UnitFileState'] == 'enabled'), name)
        process = Path('/proc') / after['services']['soft-launcher.service']['MainPID']
        require(sha(process / 'exe') == current['binarySha256'], "Publication verification failed: sha(process / 'exe') == current['binarySha256']")
        require((process / 'cmdline').read_bytes().split(b'\x00')[1:-1] == [b'--hidden'], "Publication verification failed: (process / 'cmdline').read_bytes().split(b'\\x00')[1:-1] == [b'--hidden']")
        maps = (process / 'maps').read_text()
        require(str(RUNTIME / 'lib/libgtk-layer-shell.so.0.10.1') in maps, "Publication verification failed: str(RUNTIME / 'lib/libgtk-layer-shell.so.0.10.1') in maps")
        require('TAURI_WEBVIEW_AUTOMATION=' not in (process / 'environ').read_bytes().decode(errors='replace'), "Publication verification failed: 'TAURI_WEBVIEW_AUTOMATION=' not in (process / 'environ').read_bytes().decode(errors='replace')")
        require('WEBKIT_INSPECTOR_SERVER=' not in (process / 'environ').read_bytes().decode(errors='replace'), "Publication verification failed: 'WEBKIT_INSPECTOR_SERVER=' not in (process / 'environ').read_bytes().decode(errors='replace')")
        layer_shell.validate_bundle(RUNTIME / 'lib')
        with urllib.request.urlopen('http://127.0.0.1:7331/health', timeout=3) as response:
            require(json.load(response)['ok'] is True, "Publication verification failed: json.load(response)['ok'] is True")
        for path, digest in freeze['installedSoftIfFiles'].items():
            require(sha(RUNTIME / 'soft-if/dist' / path) == digest, "Publication verification failed: sha(RUNTIME / 'soft-if/dist' / path) == digest")
        for helper in ('check-node.mjs', 'graphical-ready', 'session-start', 'hermes-handoff.py'):
            require(sha(RUNTIME / helper) == freeze['sourceFiles']['scripts/services/' + helper], "Publication verification failed: sha(RUNTIME / helper) == freeze['sourceFiles']['scripts/services/' + helper]")
        backup = Path(current['previousRuntimeBackup'])
        require(sha(backup / 'soft-launcher') == old_manifest['binarySha256'], "Publication verification failed: sha(backup / 'soft-launcher') == old_manifest['binarySha256']")
        require(sha(backup / 'hermes-handoff.py') == old_manifest['hermesAdapterSha256'], "Publication verification failed: sha(backup / 'hermes-handoff.py') == old_manifest['hermesAdapterSha256']")
        layer_shell.validate_bundle(backup / 'lib')
        verify_frozen(freeze)
        result.update(after=after, startupReadiness=readiness, runningExecutableMatches=True, startupHidden=True, pinnedLibraryMapped=True, helpersAndBridgeVerified=True, pairedRollbackVerified=True, previousRuntimeBackup=str(backup), noCatalogueMigrationNeeded=current['catalogueMigration'] is None, sourceFreezeUnchanged=True, completedAt=datetime.now(timezone.utc).isoformat())
        require(result['noCatalogueMigrationNeeded'], "Publication verification failed: result['noCatalogueMigrationNeeded']")
        verify_singleton(readiness['pid'])
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as receipt:
            receipt.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'binarySha256': result['after']['binarySha256'], 'startupHidden': True, 'pairedRollbackVerified': True, 'protectedFilesAndSettingsPreserved': True, 'databaseCountsUnchanged': True}, indent=2))


if __name__ == '__main__':
    main()
