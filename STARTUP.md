# Optional user-service setup

Start with the source build and offline checks in [README.md](README.md). The service installer is an optional Linux/systemd integration. It builds and copies a runtime under `~/.local/lib/soft-launcher`, writes a `~/.local/bin/soft-launcher` wrapper and manages the `soft-if.service` and `soft-launcher.service` user units. Installation enables those units and starts them unless `--no-start` is supplied. Do not use it as a build-only check.

```sh
scripts/service.sh install
scripts/service.sh status
```

`install --hyprland-hook` additionally backs up and adds a startup hook to an existing supported Hyprland Lua configuration. A keybinding can instead run `~/.local/lib/soft-launcher/toggle`, or `soft-launcher --toggle` when the wrapper is on PATH. `--hidden` starts without showing the overlay or requesting inference. Ordinary invocation shows the existing instance; `--toggle` shows or hides it.

The installer refuses unrelated units and an occupied unmanaged bridge port. It stages binary, verified adjacent library bundle, bridge and adapter together and retains a paired runtime backup. Existing SQLite history, catalogue, policy and their permissions survive updates. Unchanged legacy default catalogue entries can migrate to the current Codex/Hermes entries; custom entries remain unchanged and migration keeps a backup.

```sh
scripts/service.sh update
scripts/service.sh rollback
scripts/service.sh uninstall
```

`uninstall` removes the managed units, wrappers and runtime. User data is retained. `rollback` restores the recorded previous runtime and checks its manifest. See `scripts/service.sh --help` for flags. `start` and `restart` affect running services, so invoke them only when intended.

Native settings belong in `~/.config/local.system1.soft-launcher/backend.env`, or the equivalent under `XDG_CONFIG_HOME`. For example:

```sh
SOFT_LAUNCHER_CLEF_URL=http://decision.example.test:11434
SOFT_LAUNCHER_CODEX_SSH_HOST=buildbox
SOFT_LAUNCHER_CODEX_BRIDGE=/opt/soft-launcher/bridge.py
SOFT_LAUNCHER_CODEX_MODEL=gpt-6.1-sol
```

This file is optional, private and preserved across updates. The Clef URL controls the native status action. Webview decisions use settings or the build-time `VITE_DECISION_*` values. A remote webview server also needs the matching CSP override in [the native build guide](src-tauri/README.md#remote-decision-server).

The default installer build does not pass a Tauri config override. For remote builds, first create and validate the no-bundle binary with `--config` as documented there, then install that exact binary using:

```sh
sha256sum src-tauri/target/release/soft-launcher
scripts/service.sh install --skip-build \
  --binary "$PWD/src-tauri/target/release/soft-launcher" \
  --sha256 <the-verified-sha256>
```

Keep the verified `lib/` directory adjacent to the supplied binary. `update` accepts the same flags. Running a default rebuild later uses the checked-in loopback CSP again, so repeat the configured build for remote-host updates.
