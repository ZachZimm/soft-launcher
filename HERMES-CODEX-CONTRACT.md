# Codex text transport contract

Summary, translation and reviewed teaching use a configured remote Pi Codex proxy over SSH. This is optional; local application matching and other native actions do not require it. The current agent handoff is separate and described in [HERMES-DESKTOP-CONTRACT.md](HERMES-DESKTOP-CONTRACT.md).

## Configuration

Set these in the native process environment or the installed service's private `backend.env`:

```sh
SOFT_LAUNCHER_CODEX_SSH_HOST=buildbox
SOFT_LAUNCHER_CODEX_BRIDGE=/opt/soft-launcher/bridge.py
SOFT_LAUNCHER_CODEX_MODEL=gpt-6.1-sol
```

The host may be an SSH alias or `user@host`. The helper path must be an absolute path containing only letters, digits, `/`, `.`, `_` and `-`. No host or helper path is selected by default. SSH uses batch mode, strict known-host checking and a five-second connection timeout. Configure access and known hosts separately.

The helper source is `scripts/services/lab-codex-bridge.py`. Place it on the chosen host yourself. It reads that host's `~/.pi/agent/proxy-config.json`; credentials stay there and never return over SSH. The proxy hostname must be loopback or explicitly listed in the helper process's comma-separated `SOFT_LAUNCHER_CODEX_PROXY_HOSTS`. This allowlist is checked before forwarding credentials. The launcher does not deploy the helper or start the proxy.

## Bounded requests and responses

Native sends JSON with `model`, `system` and `input` over SSH stdin. Text never enters remote shell arguments. System/input fields are capped at 8 KiB each. The process deadline is 65 seconds and the response limit is 128 KiB.

The helper checks `GET /v1/models` for the configured model with `owned_by="openai-codex"`, then sends a nonstreaming `POST /v1/chat/completions` request with the system/user pair, no tools, 2048 maximum tokens and low reasoning effort. It accepts one completed `finish_reason="stop"` text response, at most 16 KiB. Redirects, partial responses, tool/function calls, refusals and malformed responses fail with safe codes. Errors do not include upstream response bodies, headers, configuration or credentials.

Native clipboard summary and translation read privacy-aware text only after selection, at most 8 KiB, and return text for review. Translation targets English. They do not copy text or execute generated commands. Legacy custom `kind="bonsai"` entries keep their data but use this transport.

Teaching accepts at most ten bounded examples and requests exactly one TOML action. Native validates the executor allowlist, keeps at most four proposals in memory for ten minutes and saves only after explicit review. Saving revalidates, rejects duplicates or external catalogue edits and atomically updates the catalogue. Generation alone cannot save or execute an action.

The generic helper expects the documented Pi-compatible proxy API; it does not claim direct compatibility with every OpenAI endpoint or account plan. No provider credentials or account sessions are part of this repository.
