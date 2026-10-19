# Hermes Desktop handoff contract

Send to agent is an optional integration with a patched local Hermes Desktop. It requires the pinned upstream patch and matching source documented in [external/hermes-desktop/README.md](external/hermes-desktop/README.md). An unpatched Desktop fails visibly. This repository does not install or restart Hermes.

## Native boundary

Every Hermes action still requires native confirmation, regardless of catalogue
flags. The first execute returns a 15-second single-use token bound to catalogue
generation, action, arguments and validated plan. Typing, preview and cancellation
before the second execute dispatch nothing. Blank text, NUL and more than 4,096
UTF-8 bytes are rejected. Clipboard text never supplies the task.

After consuming the confirmation, native probes the adapter with
`python3 ADAPTER --capabilities`, no stdin and a one-second deadline. Only
`{"version":1,"destination":"hermes-desktop"}` is supported. The old terminal
adapter receives no task and cannot launch a terminal during this probe.
Native then sends `{"text":EXACT_TYPED_TEXT}` on adapter stdin with a 49-second
process deadline. Total native process budgets are 50 seconds. The adapter's
Desktop receipt deadline is 45 seconds. There is one dispatch attempt.

Success requires the adapter's version 1 Desktop marker and request UUID. Native
returns `status="executed"`, `message="Sent to Hermes Desktop"`, no generated
text. Unsupported bridge, unavailable Desktop and explicit rejection have separate
safe errors. Timeout, malformed receipt and lost transport say delivery is
unconfirmed and instruct the user to check Desktop before sending again. An
available UUID is included. No automatic retry follows an uncertain outcome.

## Local file/socket protocol, version 1

The adapter creates an owned mode-0700 `slhd-*` directory under an owned private
`XDG_RUNTIME_DIR`, or `/tmp` when no runtime directory is configured. It creates
`request.json` exclusively, without following a symlink, mode 0600. `ack.sock` is
an owned mode-0600 AF_UNIX socket in that same directory, at most 100 path bytes.
The adapter checks the reply peer UID using SO_PEERCRED when available.

Only the private filename enters argv:

```text
~/.local/bin/hermes desktop --skip-build --launcher-handoff PRIVATE_FILE
```

The launch has HOME as its working directory, detached process session, and
null stdin/stdout/stderr. No terminal, shell, clipboard or input automation is
involved. Inherited `HERMES_YOLO_MODE`, `HERMES_ACCEPT_HOOKS` and
`HERMES_SINGLE_QUERY_SESSION` are removed. `--skip-build` prevents an incidental
Desktop rebuild while sending a task.

Request JSON:

```json
{
  "version": 1,
  "requestId": "UUID",
  "text": "EXACT_TYPED_TEXT",
  "replySocket": "/private/slhd-RANDOM/ack.sock",
  "expiresAt": 1792340000000,
  "provider": "openai-codex",
  "model": "gpt-6.1-sol"
}
```

`expiresAt` is epoch milliseconds, 42 seconds after request creation. The adapter
waits 45 seconds, leaving three seconds for Desktop to report an expired or
uncertain dispatch. The CLI
forwards a single `--hermes-launcher-handoff=PRIVATE_FILE` token to Electron.
Electron validates absolute paths; an owned, nonsymlink 0700 parent; an owned,
nonsymlink regular 0600 request file capped at 64 KiB; the UUID, version, text,
provider/model and expiration; and an owned socket at exactly `<parent>/ack.sock`.
It unlinks the request after reading it. Adapter cleanup tolerates this.

Electron queues until the primary renderer is ready and deduplicates request UUIDs
in memory and a bounded persisted list. The renderer deduplicates again. Expired
or duplicate requests are rejected. An already-running primary instance receives
the file through Electron's second-instance path. Existing conversations and
windows are retained. No restart of a running Desktop is part of this handoff.

The renderer creates a fresh Desktop conversation with per-session provider/model
overrides. It sends the raw text once, without trimming, slash-command handling,
terminal-context parsing, staged attachments, or automatic prompt-submit retry.
Text which the backend's bracketed-paste sanitizer would alter is rejected before
submission. Ordinary Desktop approval behavior remains active. No global model,
authentication, account or sticky selection is written, and no credentials are
copied or new login requested.

One newline-terminated ACK, at most 4,096 bytes, goes over the reply socket:

```json
{
  "version": 1,
  "handler": "hermes-desktop-launcher-handoff",
  "requestId": "UUID",
  "status": "accepted",
  "sessionId": "DURABLE_STORED_SESSION_ID",
  "runtimeSessionId": "GATEWAY_RUNTIME_SESSION_ID",
  "turnId": "DURABLE_STORED_SESSION_ID:user:1"
}
```

Acceptance follows a real successful `prompt.submit`, not process start or UI
mount. The gateway returns `status="streaming"` and issues no turn ID. `turnId`
is therefore a documented correlation for the first user turn of this fresh
session, not a backend-issued identifier. Session IDs come from the real created
session. Error ACKs keep version/handler/requestId and use `status="error"` with
`error="desktop"|"unsupported"|"rejected"|"unknown"`. ACKs contain no task text.

The adapter blocks on socket/process readiness, without a polling loop. A
successful second-instance process exit is not acceptance. Argparse exit 2 means
the CLI lacks this bridge. An old already-running Electron may ignore the flag;
that produces an honest unconfirmed timeout. The adapter never kills Desktop or
retries on a missing receipt. Expiry is checked before submission.

The adapter removes request text and socket files when it exits. A cleanup failure
preserves the accepted or uncertain delivery result and flags `cleanup="failed"`
in the adapter response and text-free receipt, so cleanup cannot invite a resend. Text-free
receipts under `$XDG_STATE_HOME/soft-launcher/hermes-desktop/<UUID>.json` retain
only version, UUID, status and timestamp. They permit correlation after a timeout
without retaining the typed text. Hermes retains its normal conversation history.


## Runtime configuration

The adapter defaults to `~/.local/bin/hermes`. Native locates it at `~/.local/lib/soft-launcher/hermes-handoff.py`, or the explicit `SOFT_LAUNCHER_HERMES_ADAPTER` path. Source builds can set that variable to this checkout's `scripts/services/hermes-handoff.py`. The optional service installer stages the adapter beside the binary. Hermes retains its normal conversation history.

The old terminal handoff is no longer used. No automatic fallback or retry follows uncertain delivery. Offline transport tests validate protocol behavior with fakes; actual Desktop acceptance requires a separately configured isolated GUI environment. See [VALIDATION.md](VALIDATION.md).
