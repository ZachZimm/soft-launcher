# Native build and runtime

Rust owns collectors, catalogue validation, confirmation and native execution. Decisions go from the webview directly to the selected backend; soft-if's separate Node bridge owns logs and policy. See [NATIVE-CONTRACT.md](../NATIVE-CONTRACT.md).

## Build from source

Build the sibling soft-if library and install this project's dependencies first, following the [root README](../README.md). The headless Rust library/tests do not need the desktop feature. Desktop builds require GTK 3, WebKitGTK 4.1 and the usual Linux C/C++ build tools, pkg-config, OpenSSL and librsvg development files.

On Arch Linux, the native dependency package names include `base-devel`, `webkit2gtk-4.1`, `gtk3`, `openssl`, `librsvg` and `libappindicator-gtk3`. Dependency installation is a separate user-managed step. The pinned GTK layer-shell helper additionally needs the installed Arch keyring, `gpg`, `gpgv`, `bsdtar` and x86_64. It verifies package/signature/file hashes and retains license texts beside the library. It downloads into `target/vendor` and writes only the build tree.

Development uses two terminals:

```sh
pnpm dev
# In the other terminal:
pnpm desktop
```

For a production binary without package creation:

```sh
pnpm build
python3 scripts/services/layer_shell.py --profile release
pnpm exec tauri build --features desktop --no-bundle
```

Keep `src-tauri/target/release/lib/` beside `soft-launcher`. A copied binary without its adjacent verified library bundle is incomplete. No AppImage behavior is established. `pnpm desktop:build` requests packaging as well and is not the documented validation path.

The `desktop` Cargo feature enables Tauri; `custom-protocol` embeds `dist` for production. Tauri's build CLI enables it automatically. A raw Cargo development build loads Vite unless `custom-protocol` is supplied. No beforeDev/beforeBuild hook starts a second Vite process.

## Remote decision server

The checked-in production and development CSP permit only the loopback decision endpoint and persistence bridge. A remote Vite URL or settings change does not expand CSP. Choose a generic endpoint in ignored `.env.local`:

```dotenv
VITE_DECISION_KIND=ollama
VITE_DECISION_URL=http://decision.example.test:11434
VITE_DECISION_MODEL=clef-flash-q8:latest
VITE_DECISION_LABEL=configured server
```

Create an ignored config override that adds only that exact origin to both existing policies. From the repository root:

```sh
python3 - <<'PY'
import json
from pathlib import Path
origin = 'http://decision.example.test:11434'
security = json.loads(Path('src-tauri/tauri.conf.json').read_text())['app']['security']
for key in ('csp', 'devCsp'):
    directives = security[key].split(';')
    security[key] = ';'.join(
        directive + ' ' + origin if directive.strip().startswith('connect-src ') else directive
        for directive in directives
    )
Path('src-tauri/tauri.remote.local.json').write_text(
    json.dumps({'app': {'security': security}}, indent=2) + '\n'
)
PY
```

Replace the example origin with the actual scheme, host and port, without a path or credentials. This preserves IPC, loopback, dev-server websocket and other directives. The server must implement the selected decision API and permit the launcher's Origin. The launcher does not configure or start it.

Run development with the override, after `pnpm dev` and the debug bundle preparation:

```sh
python3 scripts/services/layer_shell.py --profile debug
pnpm exec tauri dev --features desktop --config src-tauri/tauri.remote.local.json
```

Build a production binary with the same override:

```sh
pnpm build
python3 scripts/services/layer_shell.py --profile release
pnpm exec tauri build --features desktop --no-bundle \
  --config src-tauri/tauri.remote.local.json
```

Rebuild after changing `.env.local`. Existing custom settings retain their saved profile, so use settings to select the intended build default if needed. Native `SOFT_LAUNCHER_CLEF_URL` controls the status executor independently. Codex uses native SSH and needs no browser CSP entry. For optional service installation, install this exact prebuilt binary through the [skip-build recipe](../STARTUP.md), preserving the adjacent `lib/` directory.

## Desktop behavior and configuration

`--hidden` or `--background` starts without showing a window; a repeated hidden invocation does nothing. Normal invocation shows the existing instance and `--toggle` toggles it. Escape, outside click and toggle hide the overlay. The panel is 760 px wide and grows from 232 to 600 px. Layer-shell uses the focused monitor. The fallback window placement worker validates its own PID, class and address before any Hyprland dispatch.

`SOFT_LAUNCHER_NO_LAYER_SHELL` selects the normal-window fallback. `SOFT_LAUNCHER_NO_PLACEMENT` disables fallback placement. `SOFT_LAUNCHER_KEEP_ON_BLUR=1` disables focus-loss dismissal. `SOFT_LAUNCHER_DEBUG_PLACEMENT=1` logs own-window geometry and timing.

The native process defaults `__NV_DISABLE_EXPLICIT_SYNC=1` on Wayland only when no explicit renderer override exists. Explicit `GDK_GL`, `__NV_DISABLE_EXPLICIT_SYNC`, `WEBKIT_DISABLE_DMABUF_RENDERER`, `WEBKIT_DISABLE_COMPOSITING_MODE` or an X11-first `GDK_BACKEND` suppresses that default. This affects only the launcher process.

Development reads the repository catalogue. Releases copy the bundled catalogue to the app's per-user config directory on first use. `SOFT_LAUNCHER_CATALOGUE` overrides it. A watcher retains the last valid generation on invalid edits. Confirmation is enforced natively; arbitrary programs are not accepted as catalogue fields.

File indexing stays in memory, respects ignore rules, avoids symlinks and hidden/sensitive entries, and caps work at 50,000 files, depth 20 and a 20-second scan budget. Window and clipboard context cache for 500 ms. Private/unknown foreground windows suppress clipboard context. Full-text clipboard operations read separately after action selection. See the contracts for bounds and confirmation semantics.

Optional host tools are action-specific. Timers last at most 24 hours, are capped at 64 and do not survive process exit. Cross-filesystem moves fail safely. Notes use a fixed local inbox path. Paste uses `wtype` with Shift+Insert after restoring the original known foreground window; the target app must support that binding.

See [VALIDATION.md](../VALIDATION.md) for offline checks. Desktop smoke and acceptance tools are optional and can manipulate windows or send input; they are not part of the offline build sequence.
