# Implementation status and remaining evaluation

The launcher implements the native shell, decision runtime, distribution UI, local app matching, validated executors, file reranking, outcome logging and reviewed catalogue teaching. The current contracts describe those interfaces; this file records remaining work rather than the original machine-specific implementation schedule.

The runtime uses an 80 ms default debounce, tick-based stale-answer rejection, confidence policy bands, a 150 ms deliberate hold and separate native confirmation for guarded actions. Installed app choices do not wait for model readiness. The browser demo and replay use synthetic fixtures.

Remaining evaluation includes sustained daily-use misfire rates, primed-fire acceptance, latency across user-provided backends and calibration with real outcomes. Automated fixtures cannot establish a week of usability evidence. Model confidence, semantic routing and cold-start latency depend on the selected server and model.

Desktop portability also needs validation beyond the supported Hyprland configuration. The pinned layer-shell packaging path targets Arch Linux x86_64. AppImage packaging and distribution are not established. Hermes upgrades require checking the optional patch against its pinned upstream revision.

Use [README.md](README.md) for source setup, [VALIDATION.md](VALIDATION.md) for reproducible checks, and the core/native contracts for current behavior. Future changes should preserve model-independent app launching, explicit confirmation, visible persistence failures and review-before-save teaching.
