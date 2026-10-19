# soft-launcher

A keyboard launcher for Linux and Hyprland, built with Tauri 2, Svelte 5 and Rust. Installed applications match locally and remain usable without inference. A decision model chooses among the other actions in `catalogue.toml`; the UI shows its distribution, confidence bands and confirmation state.

The launcher supports file search, URLs, calculation, conversion, desktop controls, reviewed catalogue teaching, optional Codex text generation and confirmed Hermes Desktop handoff. Arguments come from validated code and user input. The model does not supply shell commands.

## Requirements and setup

Use Node 26, pnpm 9.15, a Rust toolchain supporting edition 2024, Python 3 and Linux native build dependencies for GTK 3 and WebKitGTK 4.1. The desktop implementation targets Hyprland; other compositors use a normal-window fallback and have limited validation. The pinned layer-shell bundle helper targets Arch Linux x86_64 and needs its installed Arch keyring, GnuPG and archive extraction tools. See [native build details](src-tauri/README.md).

Clone the public library and launcher side by side. Build the library before installing the launcher because `soft-if` is a local file dependency.

```sh
git clone https://github.com/ZachZimm/soft-if.git
git clone https://github.com/ZachZimm/soft-launcher.git
cd soft-if
pnpm install --frozen-lockfile
pnpm build
cd ../soft-launcher
pnpm install --frozen-lockfile
```

After changing or rebuilding the sibling library, run `pnpm sync:soft-if` to refresh pnpm's file-package snapshot.

`pnpm dev` opens the frontend server at `http://127.0.0.1:5173`. In a browser this is a labelled offline demo with simulated actions. `/replay.html` plays a synthetic deterministic fixture. Neither needs a model or native desktop. `?model=live` enables actual decision requests with simulated native execution.

For native development, keep `pnpm dev` running and start `pnpm desktop` in another terminal. The latter prepares a verified adjacent GTK layer-shell bundle and starts Tauri. It can download pinned files but does not install a system package.

## Optional services and integrations

Model actions need a compatible System One decision endpoint. Ordinary Ollama chat alone does not provide `/v1/systemone`. The default is `http://127.0.0.1:11434`, model `clef-flash-q8:latest`; this repository does not install a model or server. Settings support Ollama and llama.cpp compatible backends. Copy [.env.example](.env.example) to `.env.local` for build defaults. A remote URL also requires an exact-origin Tauri CSP override, described in [the native guide](src-tauri/README.md#remote-decision-server). Vite environment variables do not update CSP.

Decision history, calibration and live policy editing use the separate soft-if bridge:

```sh
mkdir -p "$HOME/.local/share/soft-launcher"
pnpm --dir ../soft-if softif debug \
  --db "$HOME/.local/share/soft-launcher/soft-if.db" \
  --policy "$PWD/policy.json" --port 7331
```

Start one bridge on port 7331. Bridge failures remain visible and pending events are retained for retry. [STARTUP.md](STARTUP.md) describes optional user-service installation and rollback. Building and testing do not install or start those services.

Summary, translation and teaching need an explicitly configured Codex SSH helper. Credentials remain on its host. [HERMES-CODEX-CONTRACT.md](HERMES-CODEX-CONTRACT.md) documents the environment settings and bounded API. These actions report a configuration error when the helper is unset.

Send to agent needs the optional patched Hermes Desktop described in [HERMES-DESKTOP-CONTRACT.md](HERMES-DESKTOP-CONTRACT.md). It confirms the exact typed text before sending it once to a new Desktop conversation. Unsupported Desktop builds fail visibly. Unconfirmed delivery asks the user to check Desktop before retrying.

Each native action needs its relevant tool. The catalogue uses `xdg-open`, `wl-clipboard`, `hyprctl`, `wtype`, `hyprshot`, `nmcli`, `loginctl`, `playerctl`, `wpctl`, `foot`, `ssh`, `code`, `rg` and `curl`. Missing tools return execution errors; they are not all required for local app matching or the browser demo.

## Interaction and local data

Type an installed app's name or prefix, choose a card with Tab or the arrow keys, then press Enter. App matches use installed names, desktop IDs, generic names and keywords. A current, valid leading web-search suggestion also runs with one Enter, even at low model confidence; its card shows “Enter search.” This is an explicit choice, not automatic execution or a higher confidence rating. Other actions use Enter on a primed leader or a 150 ms deliberate hold. Guarded actions require a separate second confirmation. Input or selection changes cancel that confirmation. Escape closes the current interaction, then hides the launcher. Ctrl+comma opens settings; Ctrl+R retries readiness.

Low confidence and stale results are different: low confidence means the model is uncertain, while stale results are not usable for the current input. A failed primary decision still blocks execution. File ranking runs when a file action leads or when you deliberately select one; it does not delay an unrelated web-search suggestion.

Bare website addresses use HTTPS. Calculation and conversion show read-only previews. Wrong-action feedback records a misfire but cannot undo external actions. Teaching proposes one catalogue entry and saves it only after explicit review.

The 31-action catalogue reloads valid edits. Development reads `catalogue.toml`; release builds create a per-user configuration copy. `SOFT_LAUNCHER_CATALOGUE` selects another file. Invalid edits retain the previous valid catalogue. Notes append to `~/.local/share/soft-launcher/inbox.md`; note search uses `~/notes`.

Context collection includes a bounded file index, recent files, original window metadata and a short clipboard preview. Primary action decisions omit window titles and clipboard prose; secondary file reranking can use bounded, redacted context. Clipboard text actions read up to 8 KiB after selection. Private-window rules suppress clipboard context. These limits do not make arbitrary typed text safe to disclose. Configured decision servers receive decision state, and the local SQLite database can retain private input and context. Keep databases, logs and exported recordings private.

## Validation and code map

[VALIDATION.md](VALIDATION.md) lists offline checks, optional integration requirements and known limits. No AppImage compatibility is claimed. Use the no-bundle production build in the native guide.

- `src/lib/` contains runtime, arguments, state, persistence bridge and tests. See [CORE-CONTRACT.md](CORE-CONTRACT.md).
- `src-tauri/` contains collectors, catalogue validation, confirmation and executors. See [NATIVE-CONTRACT.md](NATIVE-CONTRACT.md).
- [APP-UX-CONTRACT.md](APP-UX-CONTRACT.md) describes model-independent application matching.
- `scripts/` and `bench/` contain integration checks and synthetic repro tools. Generated run output stays ignored.
- `external/hermes-desktop/` contains an optional upstream patch, pristine touched source files, hashes and its unchanged MIT license.

No project license grant has been selected. Third-party material retains its own license notices; the Hermes license applies to that material.
