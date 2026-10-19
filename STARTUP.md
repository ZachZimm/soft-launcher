# Optional user-service setup

Start with the source build and offline checks in [README.md](README.md). The service installer is an optional Linux/systemd integration. It builds and copies a runtime under `~/.local/lib/soft-launcher`, writes a `~/.local/bin/soft-launcher` wrapper and manages the `soft-if.service` and `soft-launcher.service` user units. Installation enables those units and starts them unless `--no-start` is supplied. Do not use it as a build-only check.

```sh
scripts/service.sh install
scripts/service.sh status
```

`install --hyprland-hook` additionally backs up and adds a startup hook to an existing supported Hyprland Lua configuration. A keybinding can instead run `~/.local/lib/soft-launcher/toggle`, or `soft-launcher --toggle` when the wrapper is on PATH. `--hidden` starts without showing the overlay or requesting inference. Ordinary invocation shows the existing instance; `--toggle` shows or hides it.

The installer refuses unrelated units and an occupied unmanaged bridge port. It stages binary, verified adjacent library bundle, bridge and adapter together and retains a paired runtime backup. Existing SQLite history, catalogue, policy and their permissions survive updates. Unchanged legacy default catalogue entries can migrate to the current Codex/Hermes entries; custom entries remain unchanged and migration keeps a backup.

The launcher unit uses `KillMode=process` so stopping it does not kill applications it opened. Linux bounded command workers receive a parent-death signal; detached applications do not. This covers direct workers, not arbitrary grandchildren. Launched applications can remain accounted to the stopped service's cgroup: this is a compatibility tradeoff, not independent application scopes. See [systemd's kill-mode semantics](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html). Update older installations through the guarded installer before using restart or uninstall, so the safe stop policy is loaded first.

Updates hold a per-user startup lock. The managed hidden instance can start during the transaction; ordinary new launcher invocations refuse until it finishes. The hotkey wrapper also checks the lock. This wrapper check alone is not atomic with execution; the current native binary holds its startup lock through singleton registration. Older binaries launched manually can still conflict, and publication checks must detect that conflict rather than claim success.

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

To install a reviewed native configuration together with a runtime update, supply `--backend-env /absolute/path/to/staged.env --backend-env-sha256 <sha256>` alongside the install/update flags. The staged file must have mode `0600`. The installer verifies its hash before stopping services and restores the previous configuration, or removes a newly created file, if the transaction fails. Keep this file outside Git. These flags do not configure the webview endpoint or its CSP.

The default installer build does not pass a Tauri config override. For remote builds, first create and validate the no-bundle binary with `--config` as documented there, then install that exact binary using:

```sh
sha256sum src-tauri/target/release/soft-launcher
scripts/service.sh install --skip-build \
  --binary "$PWD/src-tauri/target/release/soft-launcher" \
  --sha256 <the-verified-sha256>
```

Keep the verified `lib/` directory adjacent to the supplied binary. `update` accepts the same flags. Running a default rebuild later uses the checked-in loopback CSP again, so repeat the configured build for remote-host updates.
