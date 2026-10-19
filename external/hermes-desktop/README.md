# Optional Hermes Desktop integration

This directory carries a launcher handoff patch for [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) at the exact revision in [MANIFEST.json](MANIFEST.json). The upstream MIT license remains in [LICENSE](LICENSE). That license covers this upstream material; it is not a license grant for the launcher project.

`base/` contains the eleven pristine touched upstream files. Their SHA256 hashes are recorded in `base/SHA256SUMS` and `MANIFEST.json` under `source.before`. The unified patch adds or changes sixteen files; `source.after` records the resulting hashes. Keep these sources byte-identical for patch review and optional rollback. No upstream checkout or packaged executable is bundled.

To reproduce in a separate upstream checkout, fetch and check out the pinned commit, verify the patch hash, then use `git apply --check` before applying. `HERMES_ROOT` below is that separate checkout and `SOFT_LAUNCHER_ROOT` is this repository:

```sh
sha256sum "$SOFT_LAUNCHER_ROOT/external/hermes-desktop/hermes-desktop-launcher-handoff.patch"
git -C "$HERMES_ROOT" checkout 7cdc6a73388356e9eaa5122b010dc20b9179170c
git -C "$HERMES_ROOT" apply --check "$SOFT_LAUNCHER_ROOT/external/hermes-desktop/hermes-desktop-launcher-handoff.patch"
git -C "$HERMES_ROOT" apply "$SOFT_LAUNCHER_ROOT/external/hermes-desktop/hermes-desktop-launcher-handoff.patch"
```

Compare the printed patch digest with the manifest and the resulting source files with `source.after`. Follow that upstream revision's dependency setup before running the manifest's desktop build commands. Build to a separate candidate directory. Applying the patch and building a candidate do not establish acceptance against a running Desktop.

The optional `deploy-hermes-desktop-package.py` tool performs a verified transactional package swap and rollback. It never starts, stops or signals Desktop and refuses to deploy while Desktop is running. It requires a separate ignored `MANIFEST.local.json`, or an explicit `SOFT_LAUNCHER_HERMES_DEPLOY_MANIFEST` path, containing the portable source hashes plus a locally measured `package` section with candidateDir, treeSha256 and key file hashes, and `liveReleaseBefore` hashes for the current executable, app.asar and build stamp. Do not reuse another machine's deployment baseline. Run its `check` command before deployment. Its fixture tests use temporary trees only.

The public manifest intentionally excludes local installed-package baselines, deployment paths and run reports. Those files are private operational records. The [handoff contract](../../HERMES-DESKTOP-CONTRACT.md) documents the protocol and uncertain-delivery behavior.
