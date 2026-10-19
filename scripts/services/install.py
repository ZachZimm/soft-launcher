#!/usr/bin/env python3
"""Install the two user services without changing system-wide configuration."""
import argparse
import fcntl
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import layer_shell
import catalogue_migration

REPO = Path(__file__).resolve().parents[2]
HELPERS = Path(__file__).resolve().parent
HOME = Path.home()
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
DATA = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
RUNTIME = HOME / ".local/lib/soft-launcher"
UNITS = CONFIG / "systemd/user"
BIN = HOME / ".local/bin/soft-launcher"
DATABASE = DATA / "soft-launcher/soft-if.db"
CATALOGUE = CONFIG / "local.system1.soft-launcher/catalogue.toml"
MARKER = "# Managed by soft-launcher/scripts/service.sh"
NAMES = ("soft-if.service", "soft-launcher.service")
UPDATE_LOCK = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'soft-launcher-update.lock'


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def ctl(*args, **kwargs):
    return run("systemctl", "--user", *args, **kwargs)


def active(name):
    return subprocess.run(["systemctl", "--user", "is-active", "--quiet", name]).returncode == 0


def owned_units():
    for name in NAMES:
        unit = UNITS / name
        # Check the manager too, so an unrelated unit in another search path is
        # never shadowed by this installer.
        result = subprocess.run(["systemctl", "--user", "show", name, "--property=FragmentPath", "--value"], capture_output=True, text=True)
        existing = Path(result.stdout.strip()) if result.stdout.strip() else unit
        if existing.exists() and MARKER not in existing.read_text():
            raise RuntimeError(f"Refusing to replace unrelated unit {existing}")
        if unit.exists() and MARKER not in unit.read_text():
            raise RuntimeError(f"Refusing to replace unrelated unit {unit}")


def free_port():
    with socket.socket() as sock:
        sock.settimeout(0.5)
        try:
            sock.connect(("127.0.0.1", 7331))
        except ConnectionRefusedError:
            return
        # Connecting detects listeners without mistaking old TIME_WAIT sockets
        # for a server after its graceful shutdown.
        raise RuntimeError("Port 7331 is already in use outside the managed bridge. Coordinate its shutdown first; this script never kills it.")


def quote(value):
    # systemd command arguments are not shell strings. Escape both unit
    # specifiers and ExecStart environment expansion as well as C-style quotes.
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def unit_path(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def directory(value):
    # WorkingDirectory and EnvironmentFile are single unquoted paths. Their
    # parsers preserve spaces and only expand unit specifiers.
    return str(value).replace("%", "%%")


def launcher_command():
    return f'exec env -u LD_LIBRARY_PATH -u TAURI_WEBVIEW_AUTOMATION -u WEBKIT_INSPECTOR_SERVER {shlex.quote("SOFT_LAUNCHER_CATALOGUE=" + str(CATALOGUE))} {shlex.quote(str(RUNTIME / "soft-launcher"))}'


def toggle_script():
    return f'''#!/usr/bin/env bash
{MARKER}
umask 077
exec 9>"${{XDG_RUNTIME_DIR:-/run/user/$UID}}/soft-launcher-update.lock"
if ! flock --shared --nonblock 9; then exit 0; fi
# Check only; native startup owns the atomic startup lock. Never hold a lock
# for the running application's lifetime, including legacy cohorts.
flock --unlock 9
exec 9>&-
{launcher_command()} --toggle
'''


def exact_text(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def write(path, text, executable=False, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = mode if mode is not None else 0o755 if executable else (path.stat().st_mode & 0o777 if path.exists() else 0o644)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp = Path(handle.name)
    temp.chmod(mode)
    temp.replace(path)


def copy(source, destination, executable=False):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temp = Path(handle.name)
    shutil.copyfile(source, temp)
    temp.chmod(0o755 if executable else 0o644)
    temp.replace(destination)


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def node_path():
    value = os.environ.get("SOFT_LAUNCHER_NODE") or shutil.which("node")
    if not value:
        raise RuntimeError("Node 26 is required")
    node = Path(value).resolve(strict=True)
    run(node, HELPERS / "check-node.mjs")
    return node


def runtime_bundle(binary):
    dynamic = subprocess.check_output(["readelf", "-d", str(binary)], text=True)
    linked = "Shared library: [libgtk-layer-shell.so.0]" in dynamic
    required = linked or "Shared library: [libwebkit2gtk-4.1.so" in dynamic
    path = binary.parent / "lib"
    if linked and "$ORIGIN/lib" not in dynamic:
        raise RuntimeError("Production binary requires gtk-layer-shell but lacks $ORIGIN/lib RUNPATH")
    if path.exists():
        layer_shell.validate_bundle(path)
        return path
    if required:
        raise RuntimeError("Production binary requires its verified adjacent lib bundle")
    return None


def stage_runtime(binary, sibling, expected_sha):
    bundle = runtime_bundle(binary)
    RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".soft-launcher-stage-", dir=RUNTIME.parent))
    try:
        if RUNTIME.exists():
            shutil.copytree(RUNTIME, stage, dirs_exist_ok=True, symlinks=True)
        write(stage / ".managed", MARKER + "\n")
        copy(binary, stage / "soft-launcher", executable=True)
        if digest(stage / "soft-launcher") != expected_sha:
            raise RuntimeError("Production binary changed during staging; freeze the tested build")
        if (stage / "lib").exists():
            shutil.rmtree(stage / "lib")
        if bundle:
            shutil.copytree(bundle, stage / "lib", symlinks=True)
            layer_shell.validate_bundle(stage / "lib")
        if (stage / "soft-if").exists():
            shutil.rmtree(stage / "soft-if")
        shutil.copytree(sibling / "dist", stage / "soft-if/dist")
        write(stage / "soft-if/package.json", '{"type":"module"}\n')
        for helper in ("check-node.mjs", "graphical-ready", "session-start", "hermes-handoff.py"):
            copy(HELPERS / helper, stage / helper, executable=helper != "check-node.mjs")
        return stage
    except BaseException:
        shutil.rmtree(stage)
        raise


def publish_runtime(stage, destination):
    backup = None
    if destination.exists():
        backup = destination.with_name(destination.name + ".backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        destination.rename(backup)
    try:
        stage.rename(destination)
    except BaseException:
        if backup:
            backup.rename(destination)
        raise
    return backup


class RuntimeUpdate:
    """Publish a complete binary/lib cohort, restoring it on a later install failure."""
    def __enter__(self):
        self.touched = False
        self.published = False
        self.backup = None
        self.stage = None
        self.guard = None
        self.prepared = False
        self.services_changed = False
        self.changed_services = set()
        return self

    def acquire_guard(self):
        if self.guard is not None:
            return
        descriptor = os.open(UPDATE_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            raise RuntimeError('Another launcher startup or update is in progress') from None
        self.guard = descriptor

    def require_safe_stop(self):
        result = subprocess.run(['systemctl', '--user', 'show', 'soft-launcher.service', '--property=KillMode', '--value'], capture_output=True, text=True, check=True)
        if result.stdout.strip() != 'process':
            raise RuntimeError('Refusing launcher stop without effective KillMode=process')

    def prepare(self):
        if self.prepared:
            return
        self.acquire_guard()
        self.active_before = {name: active(name) for name in NAMES}
        self.files_before = {path: (exact_text(path), path.stat().st_mode & 0o777) if path.exists() else None
                             for path in (BIN, CATALOGUE, CONFIG / 'local.system1.soft-launcher/backend.env', RUNTIME / 'toggle', *(UNITS / name for name in NAMES))}
        for path, expected in getattr(self, 'expected_files', {}).items():
            original = self.files_before[path]
            actual = {'sha256': hashlib.sha256(original[0].encode()).hexdigest(), 'mode': oct(original[1])} if original else None
            if actual != expected:
                raise RuntimeError('Configuration changed before the publication transaction')
        self.touched = True
        if RUNTIME.exists():
            write(RUNTIME / 'toggle', toggle_script(), executable=True)
        # Load the safe launcher stop policy before stopping an old cohort that
        # may already share its cgroup with user applications. Bridge unchanged.
        write(UNITS / 'soft-launcher.service', launcher_unit())
        ctl('daemon-reload')
        self.require_safe_stop()
        self.prepared = True

    def publish(self, stage):
        self.stage = stage
        self.prepare()
        self.require_safe_stop()
        self.note_service_change(*NAMES)
        for name in reversed(NAMES):
            if active(name):
                ctl("stop", name)
        self.backup = publish_runtime(stage, RUNTIME)
        self.published = True
        original_toggle = self.files_before[RUNTIME / 'toggle']
        if self.backup and original_toggle is not None:
            write(self.backup / 'toggle', original_toggle[0], mode=original_toggle[1])

    def note_service_change(self, *names):
        self.services_changed = True
        self.changed_services.update(names)

    def stage_backend_environment(self, text):
        if text is not None:
            if not self.prepared:
                raise RuntimeError('Backend environment requires a captured update boundary')
            write(CONFIG / 'local.system1.soft-launcher/backend.env', text, mode=0o600)

    def __exit__(self, kind, error, traceback):
        try:
            return self.finish(kind, error, traceback)
        finally:
            if self.guard is not None:
                os.close(self.guard)
                self.guard = None

    def finish(self, kind, error, traceback):
        if kind and self.touched:
            try:
                if self.services_changed:
                    # Stop under the safe policy, then restore old unit bytes.
                    # Never restore control-group and subsequently stop it.
                    self.require_safe_stop()
                    for name in reversed(NAMES):
                        if name in self.changed_services and active(name):
                            ctl("stop", name)
                if self.published:
                    failed = RUNTIME.with_name(RUNTIME.name + ".failed-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
                    RUNTIME.rename(failed)
                    if self.backup:
                        self.backup.rename(RUNTIME)
                for path, original in self.files_before.items():
                    if original is None:
                        path.unlink(missing_ok=True)
                    else:
                        write(path, original[0])
                        path.chmod(original[1])
                ctl("daemon-reload")
                for name in NAMES:
                    if name in self.changed_services and self.active_before[name]:
                        ctl("start", name)
            except BaseException as rollback_error:
                raise RuntimeError(f"Installation failed ({error}); runtime rollback also failed ({rollback_error})") from error
        if self.stage and self.stage.exists():
            shutil.rmtree(self.stage)
        return False


def install_hook():
    path = CONFIG / "hypr/hyprland.lua"
    line = f'\thl.exec_cmd({json.dumps(str(RUNTIME / "session-start"))}) -- soft-launcher startup\n'
    if not path.exists():
        raise RuntimeError(f"Hyprland Lua config missing: {path}; install without --hyprland-hook and add the documented hook manually")
    text = path.read_text()
    if line in text:
        return
    anchor = 'hl.on("hyprland.start", function()\n'
    if text.count(anchor) != 1 or "-- soft-launcher startup" in text:
        raise RuntimeError("Hyprland startup callback differs; add the documented session-start line manually")
    backup = path.with_name(path.name + ".before-soft-launcher-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    shutil.copy2(path, backup)
    write(path, text.replace(anchor, anchor + line, 1))
    print(f"Hyprland backup: {backup}")


def install(args):
    with RuntimeUpdate() as update:
        _install(args, update)


def load_backend_environment(source, expected_sha):
    if not source:
        return None
    source = Path(source)
    if source.stat().st_mode & 0o777 != 0o600:
        raise RuntimeError('Staged backend environment must have mode 0600')
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha:
        raise RuntimeError('Staged backend environment SHA256 mismatch')
    if len(data) > 16384 or b'\0' in data:
        raise RuntimeError('Invalid staged backend environment')
    return data.decode('utf-8')


def launcher_unit():
    return f'''{MARKER}
[Unit]
Description=soft-launcher hidden Wayland keyboard launcher
Wants=soft-if.service
After=soft-if.service graphical-session-pre.target
PartOf=graphical-session.target
StartLimitIntervalSec=120
StartLimitBurst=3

[Service]
Type=exec
WorkingDirectory={directory(HOME)}
Environment={unit_path("SOFT_LAUNCHER_CATALOGUE=" + str(CATALOGUE))}
Environment={unit_path("SOFT_LAUNCHER_HERMES_ADAPTER=" + str(RUNTIME / "hermes-handoff.py"))}
Environment={unit_path("SOFT_LAUNCHER_CODEX_MODEL=gpt-6.1-sol")}
Environment={unit_path("SOFT_LAUNCHER_CLEF_URL=http://127.0.0.1:11434")}
EnvironmentFile=-{directory(CONFIG / "local.system1.soft-launcher/backend.env")}
UnsetEnvironment=LD_LIBRARY_PATH TAURI_WEBVIEW_AUTOMATION WEBKIT_INSPECTOR_SERVER
ExecCondition={quote(RUNTIME / "graphical-ready")}
ExecStart={quote(RUNTIME / "soft-launcher")} --hidden
KillMode=process
Restart=on-failure
RestartSec=10
TimeoutStopSec=10
UMask=0077

[Install]
WantedBy=graphical-session.target
'''


def _install(args, update):
    owned_units()
    if RUNTIME.exists() and not (RUNTIME / ".managed").exists():
        raise RuntimeError(f"Refusing to replace unmanaged directory {RUNTIME}")
    if BIN.exists() and MARKER not in BIN.read_text():
        raise RuntimeError(f"Refusing to replace unmanaged command {BIN}")
    if not args.no_start and not active("soft-if.service"):
        free_port()
    node = node_path()
    sibling = REPO.parent / "soft-if"
    if args.skip_build:
        if not args.binary or not args.sha256:
            raise RuntimeError("--skip-build requires --binary and --sha256 from the verified production build")
        binary = Path(args.binary).resolve(strict=True)
        if digest(binary) != args.sha256.lower():
            raise RuntimeError("Production binary SHA256 does not match the approved build")
    else:
        # Build with the selected Node even if an interactive PATH selects another.
        env = dict(os.environ, PATH=str(node.parent) + os.pathsep + os.environ.get("PATH", ""))
        pkg_config = layer_shell.prepare("release")
        env["PKG_CONFIG_PATH"] = str(pkg_config) + (os.pathsep + env["PKG_CONFIG_PATH"] if env.get("PKG_CONFIG_PATH") else "")
        run("pnpm", "--dir", sibling, "build", env=env)
        run("pnpm", "sync:soft-if", cwd=REPO, env=env)
        run("pnpm", "build", cwd=REPO, env=env)
        run("pnpm", "tauri", "build", "--features", "desktop", "--no-bundle", cwd=REPO, env=env)
        binary = REPO / "src-tauri/target/release/soft-launcher"
    cli = sibling / "dist/src/cli.js"
    if not cli.exists():
        raise RuntimeError("Build the sibling soft-if before installing")
    if args.hyprland_hook and not (CONFIG / "hypr/hyprland.lua").exists():
        raise RuntimeError("Hyprland Lua config missing")
    expected_sha = digest(binary)
    backend_text = load_backend_environment(getattr(args, 'backend_env', None), getattr(args, 'backend_env_sha256', None))
    # Validate and stage the full binary/library cohort before stopping services.
    migration = None
    if CATALOGUE.exists():
        original = exact_text(CATALOGUE)
        migrated, changed = catalogue_migration.migrate(original, (REPO / "catalogue.toml").read_text())
        if changed:
            migration = {"original": original, "migrated": migrated, "changed": changed}
    update.publish(stage_runtime(binary, sibling, expected_sha))
    update.stage_backend_environment(backend_text)
    CATALOGUE.parent.mkdir(parents=True, exist_ok=True)
    if not CATALOGUE.exists():
        copy(REPO / "catalogue.toml", CATALOGUE)
    if migration:
        if exact_text(CATALOGUE) != migration["original"]:
            raise RuntimeError("Catalogue changed during migration; reload and retry")
        backup = CATALOGUE.with_name(CATALOGUE.name + ".before-hermes-codex-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        shutil.copy2(CATALOGUE, backup)
        write(CATALOGUE, migration["migrated"])
        migration = {"backup": str(backup), "originalSha256": catalogue_migration.sha(migration["original"]),
                     "migratedSha256": catalogue_migration.sha(migration["migrated"]), "actions": migration["changed"]}
        print(f"Default catalogue entries migrated; backup: {backup}")
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    # Existing SQLite files, policy, catalogue, and their permissions survive updates.
    DATABASE.parent.chmod(0o700)
    launch = launcher_command()
    write(BIN, f'#!/usr/bin/env bash\n{MARKER}\n{launch} "$@"\n', executable=True)
    write(RUNTIME / "toggle", toggle_script(), executable=True)
    policy = REPO / "policy.json"
    bridge = f'''{MARKER}
[Unit]
Description=soft-if launcher policy and SQLite bridge
StartLimitIntervalSec=120
StartLimitBurst=3

[Service]
Type=exec
WorkingDirectory={directory(RUNTIME / "soft-if")}
UnsetEnvironment=LD_LIBRARY_PATH
ExecCondition={quote(node)} {quote(RUNTIME / "check-node.mjs")}
ExecStart={quote(node)} {quote(RUNTIME / "soft-if/dist/src/cli.js")} debug --db {quote(DATABASE)} --policy {quote(policy)} --port 7331
Restart=on-failure
RestartSec=10
TimeoutStopSec=15
UMask=0077

[Install]
WantedBy=default.target
'''
    launcher = launcher_unit()
    UNITS.mkdir(parents=True, exist_ok=True)
    write(UNITS / NAMES[0], bridge)
    write(UNITS / NAMES[1], launcher)
    run("systemd-analyze", "--user", "verify", *(UNITS / n for n in NAMES))
    write(RUNTIME / "install.json", json.dumps({"repository": str(REPO), "node": str(node), "binarySha256": expected_sha, "database": str(DATABASE), "policy": str(policy), "catalogue": str(CATALOGUE), "installedAt": datetime.now(timezone.utc).isoformat(),
          "runtimeLibraries": layer_shell.validate_bundle(RUNTIME / "lib") if (RUNTIME / "lib").exists() else None,
          "previousRuntimeBackup": str(update.backup) if update.backup else None, "catalogueMigration": migration,
          "hermesAdapterSha256": digest(RUNTIME / "hermes-handoff.py"), "hermesHandoffProtocol": "desktop-file-socket-v1"}, indent=2) + "\n")
    if args.hyprland_hook:
        install_hook()
    ctl("daemon-reload")
    ctl("enable", *NAMES)
    if not args.no_start:
        start()
    print(f"Installed {RUNTIME / 'soft-launcher'} SHA256 {expected_sha}")
    print(f"Toggle wrapper: {RUNTIME / 'toggle'}")
    if update.backup:
        print(f"Previous binary and libraries retained together: {update.backup}")


def start():
    owned_units()
    if not active("soft-if.service"):
        free_port()
    ctl("reset-failed", *NAMES)
    ctl("start", "soft-if.service")
    ctl("is-active", "--quiet", "soft-if.service")
    # Reach HTTP readiness before starting the webview; no model request is sent.
    error = None
    for _ in range(50):
        try:
            with urllib.request.urlopen("http://127.0.0.1:7331/health", timeout=1) as response:
                if response.status == 200:
                    break
        except (OSError, ValueError) as caught:
            error = caught
            time.sleep(0.1)
    else:
        raise RuntimeError(f"soft-if HTTP readiness failed: {error}")
    run(RUNTIME / "session-start")
    ctl("is-active", "--quiet", "soft-launcher.service")


def rollback(args):
    owned_units()
    current = json.loads((RUNTIME / "install.json").read_text())
    previous = current.get("previousRuntimeBackup")
    if not previous:
        raise RuntimeError("No previous managed runtime is recorded")
    previous = Path(previous).resolve(strict=True)
    if previous.parent != RUNTIME.parent or not previous.name.startswith(RUNTIME.name + ".backup-") or not (previous / ".managed").exists():
        raise RuntimeError("Refusing an unrelated runtime backup")
    manifest = json.loads((previous / "install.json").read_text())
    if digest(previous / "soft-launcher") != manifest["binarySha256"]:
        raise RuntimeError("Previous runtime binary integrity failed")
    if manifest.get("hermesAdapterSha256") and digest(previous / "hermes-handoff.py") != manifest["hermesAdapterSha256"]:
        raise RuntimeError("Previous Hermes adapter integrity failed")
    if (previous / "lib").exists():
        layer_shell.validate_bundle(previous / "lib")
    migration = current.get("catalogueMigration")
    catalogue_restore = None
    if migration:
        backup = Path(migration["backup"])
        if backup.parent != CATALOGUE.parent or not backup.name.startswith(CATALOGUE.name + ".before-hermes-codex-"):
            raise RuntimeError("Invalid catalogue rollback backup")
        catalogue_restore = exact_text(backup)
        if catalogue_migration.sha(catalogue_restore) != migration["originalSha256"] or digest(CATALOGUE) != migration["migratedSha256"]:
            raise RuntimeError("Catalogue changed after migration; refusing to overwrite custom edits during rollback")
    stage = Path(tempfile.mkdtemp(prefix=".soft-launcher-stage-", dir=RUNTIME.parent))
    try:
        shutil.copytree(previous, stage, symlinks=True, dirs_exist_ok=True)
    except BaseException:
        shutil.rmtree(stage)
        raise
    with RuntimeUpdate() as update:
        update.publish(stage)
        if catalogue_restore is not None:
            write(CATALOGUE, catalogue_restore)
        manifest["previousRuntimeBackup"] = str(update.backup)
        manifest["rolledBackAt"] = datetime.now(timezone.utc).isoformat()
        write(RUNTIME / "install.json", json.dumps(manifest, indent=2) + "\n")
        if not args.no_start:
            start()
    print(f"Restored binary and libraries together: SHA256 {manifest['binarySha256']}")


def uninstall():
    owned_units()
    if not (RUNTIME / ".managed").exists():
        raise RuntimeError("Managed installation missing; refusing removal")
    ctl("disable", "--now", *NAMES)
    for name in NAMES:
        (UNITS / name).unlink(missing_ok=True)
    path = CONFIG / "hypr/hyprland.lua"
    if path.exists():
        text = path.read_text()
        lines = text.splitlines(keepends=True)
        updated = "".join(line for line in lines if not (str(RUNTIME / "session-start") in line and "-- soft-launcher startup" in line))
        if updated != text:
            backup = path.with_name(path.name + ".before-soft-launcher-uninstall-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
            shutil.copy2(path, backup)
            write(path, updated)
    if BIN.exists() and MARKER in BIN.read_text():
        BIN.unlink()
    shutil.rmtree(RUNTIME)
    ctl("daemon-reload")
    print("Removed managed services and runtime. Kept SQLite history, catalogue, policy, and Hyprland backups. Remove the optional soft-launcher hotkey override separately.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "update", "start", "restart", "status", "rollback", "uninstall"))
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--binary")
    parser.add_argument("--sha256")
    parser.add_argument("--backend-env", help="reviewed native environment file, installed transactionally")
    parser.add_argument("--backend-env-sha256", help="exact SHA256 of the mode-0600 staged environment")
    parser.add_argument("--no-start", action="store_true")
    parser.add_argument("--hyprland-hook", action="store_true", help="back up and add only the Hyprland Lua startup line")
    args = parser.parse_args()
    if args.command in ("install", "update"):
        install(args)
    elif args.command == "start":
        start()
    elif args.command == "restart":
        owned_units()
        ctl("stop", *reversed(NAMES))
        start()
    elif args.command == "status":
        ctl("status", *NAMES, "--no-pager", "-n", "5")
    elif args.command == "rollback":
        rollback(args)
    else:
        uninstall()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
