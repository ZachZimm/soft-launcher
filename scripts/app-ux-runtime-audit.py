#!/usr/bin/env python3
"""Read-only, sanitized installation preservation audit. No GUI input/inference."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
RUNTIME = HOME / '.local/lib/soft-launcher'
spec = importlib.util.spec_from_file_location('runtime_status', ROOT / 'scripts/installed-runtime-status.py')
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def rows(database):
    result = {}
    for table in ('requests', 'outcomes'):
        values = {}
        for row in database.execute(f'SELECT * FROM {table}'):
            key = str(row['id'] if table == 'requests' else row['request_id'])
            values[key] = hashlib.sha256(json.dumps(dict(row), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        result[table] = values
    return result

def capture():
    manifest = json.loads((RUNTIME / 'install.json').read_text())
    db = sqlite3.connect(Path(manifest['database']).as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('BEGIN')
    fingerprints = rows(db)
    quick = db.execute('PRAGMA quick_check').fetchone()[0]
    db.close()
    configs = {
        'hyprlandMain': HOME / '.config/hypr/hyprland.lua',
        'launcherBinding': HOME / '.config/hypr/dms/binds-user.lua',
        'bindingBackup': HOME / '.config/hypr/dms/binds-user.lua.before-soft-launcher-20261017',
        'catalogue': Path(manifest['catalogue']),
        'policy': Path(manifest['policy']),
        'clefUnit': HOME / '.config/systemd/user/clef-flash.service',
    }
    services = {name: status.service(name) for name in ('soft-launcher.service', 'soft-if.service', 'clef-flash.service')}
    pid = int(services['soft-launcher.service']['MainPID'])
    clients = json.loads(subprocess.check_output(['hyprctl', '-j', 'clients'], text=True))
    layers = json.loads(subprocess.check_output(['hyprctl', '-j', 'layers'], text=True))
    def own_layers(value):
        if isinstance(value, dict):
            return int('soft-launcher' in str(value.get('namespace', ''))) + sum(own_layers(v) for v in value.values())
        return sum(own_layers(v) for v in value) if isinstance(value, list) else 0
    mapped = {line.split()[-1] for line in Path(f'/proc/{pid}/maps').read_text().splitlines() if 'libgtk-layer-shell.so.0.10.1' in line}
    env = {item.split(b'=', 1)[0].decode(errors='replace') for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in item}
    blocked = sorted(env & {'LD_PRELOAD', 'LD_LIBRARY_PATH', 'GTK_PATH', 'GTK_MODULES', 'WEBKIT_INSPECTOR_SERVER', 'WEBKIT_REMOTE_INSPECTOR_SERVER', 'TAURI_AUTOMATION', 'TAURI_WEBVIEW_AUTOMATION', 'SOFT_LAUNCHER_KEEP_ON_BLUR', 'SOFT_LAUNCHER_NO_LAYER_SHELL', 'SOFT_LAUNCHER_NO_PLACEMENT'})
    launcher_pids = []
    for process in Path('/proc').iterdir():
        if not process.name.isdigit(): continue
        try:
            if os.readlink(process / 'exe').split('/')[-1].startswith('soft-launcher'): launcher_pids.append(int(process.name))
        except (OSError, PermissionError): pass
    rollbacks = []
    for backup in sorted(RUNTIME.parent.glob('soft-launcher.backup-*')):
        rollbacks.append({'name': backup.name, 'binarySha256': digest(backup / 'soft-launcher'), 'librarySha256': digest(backup / 'lib/libgtk-layer-shell.so.0.10.1')})
    return {
        'capturedAt': datetime.now(timezone.utc).isoformat(), 'desktopInput': False, 'standaloneInference': False,
        'installedSha256': digest(RUNTIME / 'soft-launcher'), 'manifestSha256': manifest['binarySha256'],
        'librarySha256': digest(RUNTIME / 'lib/libgtk-layer-shell.so.0.10.1'), 'bundleManifestSha256': digest(RUNTIME / 'lib/gtk-layer-shell.bundle.json'),
        'mappedLibraryCount': len(mapped), 'mappedFromInstalledBundle': str(RUNTIME / 'lib/libgtk-layer-shell.so.0.10.1') in mapped,
        'services': services, 'launcherProcessIds': sorted(launcher_pids), 'ownClientCount': sum(c.get('pid') == pid for c in clients), 'ownLayerCount': own_layers(layers),
        'blockedEnvironmentKeysPresent': blocked, 'configurationSha256': {name: digest(path) for name, path in configs.items()},
        'databaseQuickCheck': quick, 'databaseCounts': {name: len(values) for name, values in fingerprints.items()}, 'rowFingerprints': fingerprints,
        'runpathPresent': '$ORIGIN/lib' in subprocess.check_output(['readelf', '-d', str(RUNTIME / 'soft-launcher')], text=True), 'rollbackBundles': rollbacks,
        'visualNativeSourceSha256': {name: digest(ROOT / name) for name in ('src-tauri/src/layer_shell.rs', 'src-tauri/src/overlay.rs')},
    }

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--before', type=Path)
    args = parser.parse_args()
    result = capture()
    if args.before:
        baseline = json.loads(args.before.read_text())
        result['preservation'] = {
            'database': {name: {'before': len(values), 'after': len(result['rowFingerprints'][name]), 'allOriginalRowsByteIdentical': all(result['rowFingerprints'][name].get(key) == value for key, value in values.items())} for name, values in baseline['rowFingerprints'].items()},
            'configurationsUnchanged': result['configurationSha256'] == baseline['configurationSha256'],
            'clefPidUnchanged': result['services']['clef-flash.service']['MainPID'] == baseline['services']['clef-flash.service']['MainPID'],
            'visualNativeSourceUnchanged': result['visualNativeSourceSha256'] == baseline['visualNativeSourceSha256'],
            'oldManagedProcessesExited': all(not Path(f'/proc/{pid}').exists() for name in ('soft-launcher.service', 'soft-if.service') for pid in baseline['services'][name]['processIds']),
        }
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('capturedAt', 'installedSha256', 'databaseCounts', 'ownClientCount', 'ownLayerCount')} | ({'preservation': result['preservation']} if 'preservation' in result else {}), indent=2))
