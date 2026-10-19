#!/usr/bin/env python3
"""Exercise installation in a temporary home; never call the real systemctl."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
import socket
import shutil
import tempfile
import unittest
import catalogue_migration

INSTALL = Path(__file__).with_name("install.py").resolve()
BUNDLE = INSTALL.parents[2] / "src-tauri/target/release/lib"


class InstallTest(unittest.TestCase):
    def test_toggle_refuses_during_update_and_closes_lock_before_exec(self):
        self.install()
        runtime = self.home / '.local/lib/soft-launcher'
        marker = self.home / 'synthetic-launched'
        (runtime / 'soft-launcher').write_text('#!/usr/bin/env bash\nflock --exclusive --nonblock "$XDG_RUNTIME_DIR/soft-launcher-update.lock" /usr/bin/true || exit 17\ntouch "$HOME/synthetic-launched"\n')
        (runtime / 'soft-launcher').chmod(0o755)
        lock = Path(self.env['XDG_RUNTIME_DIR']) / 'soft-launcher-update.lock'
        with lock.open('a') as guard:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            denied = subprocess.run([str(runtime / 'toggle')], env=self.env, timeout=3)
            self.assertEqual(denied.returncode, 0)
            self.assertFalse(marker.exists())
        allowed = subprocess.run([str(runtime / 'toggle')], env=self.env, timeout=3)
        self.assertEqual(allowed.returncode, 0)
        self.assertTrue(marker.exists())

    def test_staged_backend_environment_is_protected_and_rolled_back_on_failure(self):
        self.install()
        backend = self.home / '.config/local.system1.soft-launcher/backend.env'
        source = Path(self.temp.name) / 'staged-backend.env'
        source.write_text('SOFT_LAUNCHER_CODEX_BRIDGE=/opt/example/bridge.py\n')
        source.chmod(0o600)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        def update(success=True, sha=digest):
            return self.command('update', '--no-start', '--skip-build', '--binary', str(self.binary), '--sha256', self.sha,
                                '--backend-env', str(source), '--backend-env-sha256', sha, success=success)
        update(success=False, sha='0' * 64)
        self.assertFalse(backend.exists())
        source.chmod(0o644)
        update(success=False)
        self.assertFalse(backend.exists())
        source.chmod(0o600)
        verifier = self.commands / 'systemd-analyze'
        verifier.write_text('#!/bin/sh\nexit 1\n'); verifier.chmod(0o755)
        update(success=False)
        self.assertFalse(backend.exists())
        backend.write_text('SOFT_LAUNCHER_CODEX_MODEL=previous\n'); backend.chmod(0o640)
        update(success=False)
        self.assertEqual(backend.read_text(), 'SOFT_LAUNCHER_CODEX_MODEL=previous\n')
        self.assertEqual(backend.stat().st_mode & 0o777, 0o640)
        verifier.unlink()
        update()
        self.assertEqual(backend.read_bytes(), source.read_bytes())
        self.assertEqual(backend.stat().st_mode & 0o777, 0o600)

    def old_catalogue(self):
        source = "# Installed old defaults\n"
        for action in catalogue_migration.OLD.values():
            source += '\n[[action]]\nid=' + json.dumps(action['id']) + '\ndescription=' + json.dumps(action['description']) + '\nexec={' + ','.join(k+'='+json.dumps(v) for k,v in action['exec'].items()) + '}\n'
        return source + '\n# user custom must survive exactly\n[[action]]\nid="my_reply"\ndescription="My custom reply"\nexec={kind="bonsai",arg="clipboard",template="reply"}\n'

    def test_default_migration_and_rollback_preserve_custom_bytes_and_permissions(self):
        self.install()
        catalogue = self.home / ".config/local.system1.soft-launcher/catalogue.toml"
        original = self.old_catalogue()
        catalogue.write_text(original)
        catalogue.chmod(0o600)
        backend = catalogue.parent / "backend.env"
        backend.write_text("SOFT_LAUNCHER_CODEX_MODEL=custom-codex-model\n")
        self.install()
        runtime = self.home / ".local/lib/soft-launcher"
        manifest = json.loads((runtime / "install.json").read_text())
        migration = manifest["catalogueMigration"]
        self.assertEqual(Path(migration["backup"]).read_text(), original)
        self.assertEqual(catalogue.stat().st_mode & 0o777, 0o600)
        self.assertIn('id = "send_to_agent"', catalogue.read_text())
        self.assertIn('# user custom must survive exactly\n[[action]]\nid="my_reply"', catalogue.read_text())
        self.assertEqual(backend.read_text(), "SOFT_LAUNCHER_CODEX_MODEL=custom-codex-model\n")
        self.assertTrue((runtime / "hermes-handoff.py").is_file())
        self.assertEqual(manifest["hermesAdapterSha256"], hashlib.sha256((runtime / "hermes-handoff.py").read_bytes()).hexdigest())
        self.assertEqual(manifest["hermesHandoffProtocol"], "desktop-file-socket-v1")
        unit = (self.home / ".config/systemd/user/soft-launcher.service").read_text()
        self.assertIn("backend.env", unit)
        self.assertIn('KillMode=process\n', unit)
        bridge_unit = (self.home / '.config/systemd/user/soft-if.service').read_text()
        self.assertNotIn('KillMode=process', bridge_unit)
        self.assertNotIn("clef-flash.service", unit)
        self.command("rollback", "--no-start")
        self.assertEqual(catalogue.read_text(), original)

    def test_migrated_catalogue_failure_rolls_back_and_later_custom_edits_block_rollback(self):
        self.install()
        catalogue = self.home / ".config/local.system1.soft-launcher/catalogue.toml"
        original = self.old_catalogue()
        catalogue.write_text(original)
        verifier = self.commands / "systemd-analyze"
        verifier.write_text("#!/bin/sh\nexit 1\n")
        verifier.chmod(0o755)
        self.command("update", "--no-start", "--skip-build", "--binary", str(self.binary), "--sha256", self.sha, success=False)
        self.assertEqual(catalogue.read_text(), original)
        verifier.unlink()
        self.install()
        catalogue.write_text(catalogue.read_text() + "\n# later user edit\n")
        edited = catalogue.read_bytes()
        result = self.command("rollback", "--no-start", success=False)
        self.assertIn("refusing to overwrite custom edits", result.stderr)
        self.assertEqual(catalogue.read_bytes(), edited)

    def test_rollback_refuses_a_changed_paired_hermes_adapter(self):
        self.install()
        self.install()
        runtime = self.home / ".local/lib/soft-launcher"
        manifest = json.loads((runtime / "install.json").read_text())
        original = (runtime / "hermes-handoff.py").read_bytes()
        previous = Path(manifest["previousRuntimeBackup"])
        (previous / "hermes-handoff.py").write_text("inert modified adapter")
        result = self.command("rollback", "--no-start", success=False)
        self.assertIn("Previous Hermes adapter integrity failed", result.stderr)
        self.assertEqual((runtime / "hermes-handoff.py").read_bytes(), original)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="soft-launcher-install-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home with space % test"
        self.home.mkdir()
        self.commands = Path(self.temp.name) / "commands"
        self.commands.mkdir()
        self.calls = Path(self.temp.name) / "systemctl.jsonl"
        fake = self.commands / "systemctl"
        fake.write_text('''#!/usr/bin/python3
import json, os, sys
with open(os.environ['SERVICE_TEST_CALLS'], 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
if '--property=KillMode' in sys.argv:
    print(os.environ.get('SERVICE_TEST_KILL_MODE', 'process'))
sys.exit(3 if 'is-active' in sys.argv else 0)
''')
        fake.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"), XDG_DATA_HOME=str(self.home / ".local/share"), PATH=str(self.commands) + ":/usr/bin:/bin", SOFT_LAUNCHER_NODE="/usr/bin/node", SERVICE_TEST_CALLS=str(self.calls))
        private_runtime = self.home / 'runtime'; private_runtime.mkdir(mode=0o700)
        self.env['XDG_RUNTIME_DIR'] = str(private_runtime)
        self.config = self.home / ".config/hypr/hyprland.lua"
        self.config.parent.mkdir(parents=True)
        self.original = 'hl.on("hyprland.start", function()\n\thl.exec_cmd("dms run")\nend)\n-- keep all user settings\n'
        self.config.write_text(self.original)
        self.config.chmod(0o600)
        self.binary = Path("/usr/bin/true")
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()

    def command(self, *args, success=True):
        result = subprocess.run(["/usr/bin/python3", str(INSTALL), *args], env=self.env, text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("EnvironmentFile= path is not absolute", result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def install(self, *extra):
        return self.command("install", "--no-start", "--skip-build", "--binary", str(self.binary), "--sha256", self.sha, *extra)

    def bundled_fixture(self):
        self.assertTrue((BUNDLE / "gtk-layer-shell.bundle.json").exists(),
                        "Run python3 scripts/services/layer_shell.py before the bundle tests")
        folder = Path(self.temp.name) / "binary fixture"
        folder.mkdir(exist_ok=True)
        shutil.copytree(BUNDLE, folder / "lib", symlinks=True)
        self.binary = folder / "soft-launcher"
        shutil.copyfile("/usr/bin/true", self.binary)
        self.binary.chmod(0o755)
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        return folder

    def test_bundled_library_loads_after_relocation_without_loader_environment(self):
        folder = self.bundled_fixture()
        source = folder / "version.c"
        source.write_text('''#include <stdio.h>
extern unsigned gtk_layer_get_major_version(void);
extern unsigned gtk_layer_get_minor_version(void);
extern unsigned gtk_layer_get_micro_version(void);
int main(void) {
  printf("%u.%u.%u\\n", gtk_layer_get_major_version(), gtk_layer_get_minor_version(), gtk_layer_get_micro_version());
  return 0;
}
''')
        subprocess.run(["cc", str(source), "-o", str(self.binary), "-L" + str(folder / "lib"),
                        "-lgtk-layer-shell", "-Wl,--enable-new-dtags,-rpath,$ORIGIN/lib"], check=True)
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        (folder / "lib").rename(folder / "saved-lib")
        missing = self.command("install", "--no-start", "--skip-build", "--binary", str(self.binary), "--sha256", self.sha, success=False)
        self.assertIn("verified adjacent lib bundle", missing.stderr)
        self.assertFalse((self.home / ".local/lib/soft-launcher").exists())
        (folder / "saved-lib").rename(folder / "lib")
        self.install()
        runtime = self.home / ".local/lib/soft-launcher"
        self.assertTrue((runtime / "lib/libgtk-layer-shell.so").is_symlink())
        self.assertEqual(os.readlink(runtime / "lib/libgtk-layer-shell.so.0"), "libgtk-layer-shell.so.0.10.1")
        self.assertTrue((runtime / "lib/licenses/LGPL-3.0.txt").is_file())
        result = subprocess.run([str(self.home / ".local/bin/soft-launcher")], env=dict(self.env, LD_LIBRARY_PATH="/nonexistent-fixture"),
                                cwd="/", check=True, text=True, capture_output=True)
        self.assertEqual(result.stdout.strip(), "0.10.1")
        self.command("uninstall")
        self.assertFalse(runtime.exists())

    def test_corrupt_bundle_is_rejected_before_replacing_installed_binary_or_library(self):
        folder = self.bundled_fixture()
        self.install()
        runtime = self.home / ".local/lib/soft-launcher"
        original = (runtime / "soft-launcher").read_bytes()
        library = runtime / "lib/libgtk-layer-shell.so.0.10.1"
        original_library = library.read_bytes()
        (folder / "lib/libgtk-layer-shell.so.0.10.1").write_bytes(b"corrupted fixture")
        self.binary.write_bytes(Path("/usr/bin/false").read_bytes())
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        result = self.command("update", "--no-start", "--skip-build", "--binary", str(self.binary), "--sha256", self.sha, success=False)
        self.assertIn("bundle integrity failed", result.stderr)
        self.assertEqual((runtime / "soft-launcher").read_bytes(), original)
        self.assertEqual(library.read_bytes(), original_library)

    def test_update_retains_previous_binary_and_library_together(self):
        self.bundled_fixture()
        self.install()
        old_sha = self.sha
        self.binary.write_bytes(Path("/usr/bin/false").read_bytes())
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.install()
        runtime = self.home / ".local/lib/soft-launcher"
        manifest = json.loads((runtime / "install.json").read_text())
        backup = Path(manifest["previousRuntimeBackup"])
        self.assertEqual(hashlib.sha256((backup / "soft-launcher").read_bytes()).hexdigest(), old_sha)
        self.assertEqual(manifest["binarySha256"], self.sha)
        self.assertEqual((backup / "lib/libgtk-layer-shell.so.0.10.1").read_bytes(), (runtime / "lib/libgtk-layer-shell.so.0.10.1").read_bytes())
        self.command("uninstall")
        self.assertFalse(runtime.exists())
        self.assertTrue(backup.exists())

    def test_rejects_library_symlink_escaping_the_pinned_bundle(self):
        folder = self.bundled_fixture()
        self.install()
        link = folder / "lib/libgtk-layer-shell.so.0"
        link.unlink()
        link.symlink_to("/usr/lib/libgtk-layer-shell.so.0")
        result = self.command("update", "--no-start", "--skip-build", "--binary", str(self.binary), "--sha256", self.sha, success=False)
        self.assertIn("bundle symlink is invalid", result.stderr)
        runtime = self.home / ".local/lib/soft-launcher"
        self.assertEqual(os.readlink(runtime / "lib/libgtk-layer-shell.so.0"), "libgtk-layer-shell.so.0.10.1")

    def test_rollback_restores_the_recorded_binary_and_library_cohort(self):
        self.bundled_fixture()
        self.install()
        old_sha = self.sha
        self.binary.write_bytes(Path("/usr/bin/false").read_bytes())
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.install()
        self.command("rollback", "--no-start")
        runtime = self.home / ".local/lib/soft-launcher"
        manifest = json.loads((runtime / "install.json").read_text())
        self.assertEqual(manifest["binarySha256"], old_sha)
        self.assertEqual(hashlib.sha256((runtime / "soft-launcher").read_bytes()).hexdigest(), old_sha)
        self.assertTrue((runtime / "lib/licenses/MIT.txt").is_file())
        replaced = Path(manifest["previousRuntimeBackup"])
        self.assertEqual(hashlib.sha256((replaced / "soft-launcher").read_bytes()).hexdigest(), self.sha)

    def test_failure_after_publication_restores_original_binary_library_and_manifest(self):
        self.bundled_fixture()
        self.install()
        runtime = self.home / ".local/lib/soft-launcher"
        old_manifest = (runtime / "install.json").read_bytes()
        old_binary_inode = (runtime / "soft-launcher").stat().st_ino
        old_library_inode = (runtime / "lib/libgtk-layer-shell.so.0.10.1").stat().st_ino
        self.binary.write_bytes(Path("/usr/bin/false").read_bytes())
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        verifier = self.commands / "systemd-analyze"
        verifier.write_text("#!/bin/sh\nexit 1\n")
        verifier.chmod(0o755)
        self.command("update", "--no-start", "--skip-build", "--binary", str(self.binary), "--sha256", self.sha, success=False)
        self.assertEqual((runtime / "install.json").read_bytes(), old_manifest)
        self.assertEqual((runtime / "soft-launcher").stat().st_ino, old_binary_inode)
        self.assertEqual((runtime / "lib/libgtk-layer-shell.so.0.10.1").stat().st_ino, old_library_inode)

    def test_install_update_and_uninstall_keep_user_data_and_config(self):
        data = self.home / ".local/share/soft-launcher"
        data.mkdir(parents=True)
        database = data / "soft-if.db"
        database.write_bytes(b"fixture preserved database bytes")
        catalogue = self.home / ".config/local.system1.soft-launcher/catalogue.toml"
        catalogue.parent.mkdir(parents=True)
        catalogue.write_text("# keep user's catalogue\n")
        self.install("--hyprland-hook")
        runtime = self.home / ".local/lib/soft-launcher"
        self.assertEqual((runtime / "soft-launcher").read_bytes(), self.binary.read_bytes())
        self.assertTrue((runtime / "soft-if/dist/src/cli.js").exists())
        subprocess.run(["/usr/bin/node", str(runtime / "soft-if/dist/src/cli.js"), "--help"], env=self.env, check=True, stdout=subprocess.DEVNULL)
        self.assertEqual(json.loads((runtime / "install.json").read_text())["node"], "/usr/bin/node")
        self.assertEqual(len(list(self.config.parent.glob("*.before-soft-launcher-*"))), 1)
        self.assertEqual(self.config.read_text().count("-- soft-launcher startup"), 1)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.install("--hyprland-hook")
        self.assertEqual(self.config.read_text().count("-- soft-launcher startup"), 1)
        self.assertEqual(catalogue.read_text(), "# keep user's catalogue\n")
        self.assertEqual(database.read_bytes(), b"fixture preserved database bytes")
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertFalse(any("start" in call or "stop" in call for call in calls))
        self.command("uninstall")
        self.assertEqual(self.config.read_text(), self.original)
        self.assertEqual(catalogue.read_text(), "# keep user's catalogue\n")
        self.assertEqual(database.read_bytes(), b"fixture preserved database bytes")
        self.assertFalse(runtime.exists())

    def test_rejects_unrelated_unit_without_writing_runtime(self):
        unit = self.home / ".config/systemd/user/soft-if.service"
        unit.parent.mkdir(parents=True)
        original = "[Service]\nExecStart=/usr/bin/sleep infinity\n"
        unit.write_text(original)
        result = self.command("install", "--no-start", success=False)
        self.assertIn("unrelated unit", result.stderr)
        self.assertEqual(unit.read_text(), original)
        self.assertFalse((self.home / ".local/lib/soft-launcher").exists())

    def test_rejects_wrong_production_hash_before_installing(self):
        result = self.command("install", "--no-start", "--skip-build", "--binary", str(self.binary), "--sha256", "0" * 64, success=False)
        self.assertIn("SHA256 does not match", result.stderr)
        self.assertFalse((self.home / ".local/lib/soft-launcher").exists())

    def test_session_start_requires_live_wayland_and_imports_only_graphical_names(self):
        self.install()
        helper = self.home / ".local/lib/soft-launcher/session-start"
        env = dict(self.env, XDG_SESSION_TYPE="wayland", WAYLAND_DISPLAY="missing-wayland", XDG_RUNTIME_DIR=self.temp.name, HYPRLAND_INSTANCE_SIGNATURE="fixture", XDG_CURRENT_DESKTOP="Hyprland", DISPLAY=":0", LD_LIBRARY_PATH="fixture-do-not-import")
        before = self.calls.read_text()
        missing = subprocess.run([str(helper)], env=env, capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(self.calls.read_text(), before)
        fake = self.commands / "hyprctl"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(Path(self.temp.name) / "wayland-fixture"))
            env["WAYLAND_DISPLAY"] = "wayland-fixture"
            subprocess.run([str(helper)], env=env, check=True, capture_output=True)
        calls = [json.loads(line) for line in self.calls.read_text()[len(before):].splitlines()]
        imported = next(call for call in calls if "import-environment" in call)
        self.assertEqual(set(imported[2:]), {"WAYLAND_DISPLAY", "DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "XDG_RUNTIME_DIR"})
        self.assertEqual(calls[-1], ["--user", "start", "soft-launcher.service"])


if __name__ == "__main__":
    unittest.main()
