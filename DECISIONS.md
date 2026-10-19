# Architecture decisions

The Svelte webview imports the browser-safe `soft-if` package root and sends decision requests directly to the configured endpoint. Rust collects context and validates execution. It does not proxy decision inference or write soft-if logs.

The sibling soft-if Node CLI owns SQLite and the localhost HTTP/SSE bridge on port 7331. Browser logging and policy updates use that bridge. Persistence failures retain pending events and remain visible. The sibling is a `file:../soft-if` dependency with an explicit build-first setup.

Action probabilities determine distribution layout; the server's separate confidence value determines policy bands. Concrete argument extraction stays in code. Primary action decisions exclude ambient clipboard prose and window titles. File reranking can use bounded context.

Rust owns confirmation. Tokens bind action, exact arguments, catalogue generation and validated execution plan. They expire after 15 seconds and are single use. A confidence band cannot bypass confirmation. Generated catalogue entries stay in memory until reviewed, validated and explicitly saved.

Installed applications use deterministic local matching and native indexed IDs. They can launch while inference is unavailable and do not produce synthetic model outcomes or calibration labels.

Text generation uses an optional configured Codex SSH helper; legacy custom `bonsai` catalogue kinds route through the replacement transport. Send to agent uses a separately confirmed local Hermes Desktop file/socket handoff. Neither path runs generated shell commands automatically.

The Hyprland overlay uses GTK 3 layer-shell when available. A normal-window fallback validates its own process, class and window address before placement. On Wayland, the native process applies an NVIDIA explicit-sync compatibility default only when no renderer override is supplied. It changes no system or compositor configuration.
