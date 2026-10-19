# Validation and known limits

The public-source preparation checks below run without model inference, desktop input or changes to installed services. Generated captures, databases, audit JSON and deployment records stay local and ignored. Historical installation/session reports are not evidence for a fresh clone.

## Offline checks

Build the sibling soft-if library and install launcher dependencies as described in [README.md](README.md). With dependencies already cached, run:

```sh
pnpm check
pnpm test
pnpm build
pnpm exec tsx scripts/integration.ts
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo test --offline --manifest-path src-tauri/Cargo.toml
cargo check --offline --manifest-path src-tauri/Cargo.toml --features desktop
python3 scripts/services/test-backends.py
python3 scripts/services/test-install.py
python3 scripts/test-routing-publication.py
python3 src-tauri/scripts/test-qa-input-safety.py
python3 external/hermes-desktop/test_deploy_hermes_desktop_package.py
```

The current cleanup passed typechecking with no warnings, all 205 frontend/core tests, the frontend build, the local HTTP/SSE/SQLite integration, 57 Rust unit/native tests and desktop compilation. Python checks passed 9 backend-helper, 13 temporary-home installer, 12 publication, 14 fake-input safety and 7 temporary-tree Hermes deployment tests. Installer/publication tests replace process/service boundaries and use temporary paths. They do not install the launcher or operate real services.

The integration script starts its own loopback test service and temporary database. It checks policy updates, outcomes, calibration and retained events after persistence failure, with no model or native execution. Native tests inject command runners for collectors/executors. The 27 synthetic routing regression cases preserve bounded recorded decision answers, with generic fixture paths/hosts; they do not establish current live-model accuracy.

The retained Hermes upstream payload was checked against its manifest: eleven pristine base-file hashes and the patch hash match; applying the patch in memory produces all sixteen recorded output hashes. Its upstream license and source files remain unchanged.

## Keyword activation regression checks

The keyword-search update adds deterministic runtime tests for `audiobookshelf` and `transcription model`: a valid current leading web-search choice accepts one explicit activation at low confidence, while recording the original confidence and `primed: false`. Full-question and setup-instruction agent cases retain their confirmation gates. Additional cases cover rebound executors, invalid arguments, gibberish, primary timeouts, superseded input and file ranking on deliberate selection.

A fake-provider reproduction showed that an auxiliary file-search signal could trigger a failing speculative file request and prevent an otherwise usable web decision from being committed. Eager file ranking now requires the leading action to need a file. This establishes that failure path; it does not establish the cause of every intermittent stale result seen in daily use. Real primary failures still block execution.

The update's offline checks passed 233 frontend/core tests, typechecking, 65 Rust tests, 15 installer tests and 25 publication tests. One Rust subprocess-helper entry is intentionally ignored as a standalone test and is invoked by the lifecycle cases. The configuration-migration tests cover exact before/after file stamps, unrelated-file changes, native-setting drift and restoration of an existing or newly created backend environment file after failure. Desktop acceptance of the built candidate is recorded separately from these offline checks; no live-model accuracy claim follows from injected decision fixtures.

Lifecycle regressions reproduce bounded direct-child leaks when the launcher exits normally or receives SIGTERM/SIGKILL, then verify parent-death cleanup while detached test applications survive. Startup-lock tests cover concurrent startup/update exclusion and the managed hidden-start exception. Installer tests verify safe stop-policy ordering and rollback at several failure points. These tests do not establish cleanup of arbitrary grandchildren or independent cgroup accounting for launched applications.

The configured production candidate passed 19 isolated embedded-WebKit checks using deterministic decisions and intercepted actions. Separate lifecycle checks used synthetic long-running application/browser stand-ins and bounded workers: after SIGTERM or SIGKILL to the launcher, WebKit exited, bounded workers exited and the application stand-ins survived. The test used private display, profile, session bus and network namespace; it did not open a real browser or contact a live model. One attempt failed its host-unchanged check during concurrent live logger activity and remains recorded as failed; the strict rerun passed. Asset verification checked the running executable hash and content-hashed resource names, not bytes read back from the webview. The X11 test did not exercise Hyprland placement or load the Wayland layer-shell library.

The configured build was installed through the guarded transaction after these checks. The installed binary then passed all 19 isolated acceptance checks in one run. Final verification confirmed the running executable, hidden startup, one launcher instance, the mapped adjacent layer-shell library, active services and unchanged database counts, inode, integrity and UI settings. An exact configuration migration made the previous effective backend settings explicit; no private settings are committed. A prior attempt collided with a concurrent hotkey invocation and rolled back. Recovery preserved that failure record, verified the old runtime before replacement and retained the paired rollback bundle. These local deployment records are ignored and are not portable fresh-clone fixtures.

## Optional live and desktop checks

The default offline sequence needs no decision server. Live tools such as `bench/live.ts`, `bench/rerank.ts` and `scripts/routing-verification.ts` send synthetic requests to an explicitly chosen compatible endpoint. Routing tools use `SOFT_LAUNCHER_CLEF_URL` and `SOFT_LAUNCHER_CLEF_MODEL`, defaulting to loopback and `clef-flash-q8:latest`. Laya experiments use `LAYLA_URL` and a separately provisioned Laya API; they are not part of launcher setup.

GUI scripts require separate preparation and authorization. `run-url-ui-isolated-qa.py`, `run-routing-ui-isolated-qa.py` and `run-hermes-desktop-isolated-acceptance.py` require a ready hidden agent workspace, private display/bus/profile, WebKitWebDriver and their optional services. Their network target can be selected with `SOFT_LAUNCHER_QA_CLEF_HOST`, default `127.0.0.1:11434`, and must match the candidate's configured backend/CSP. Hermes acceptance creates conversations and can use inference. Do not treat these as offline tests.

`desktop-smoke.py`, host binding probes and other desktop repro tools can show/hide windows, affect focus or send input. Inspect each tool's scope before use. Retired host-input acceptance entrypoints refuse unconditionally and remain only to support fake-input safety regression. Obsolete terminal-Hermes acceptance tools are not part of current validation.

## Limits

No live inference, current installed-desktop acceptance, runtime installation, service restart or AppImage validation was performed for this source cleanup. The documented production route builds a no-bundle binary with its adjacent verified library directory. The pinned bundle helper is specific to Arch Linux x86_64 and requires a local Arch keyring; other distributions need separate packaging validation.

Cold model load time and routing quality depend on the configured backend. A week of usability, misfire rates and calibration on daily use remain unmeasured by these tests. Local application matching works independently of inference; external action success still depends on installed host tools and compositor behavior.

Cargo reports a future-compatibility warning in the transitive `nom 1.2.4` dependency. No dependency upgrades were included in this portability cleanup.
