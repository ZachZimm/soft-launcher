# Launcher core contract

Core owns `src/lib/{loop,args,state,bridge,types,teach,metrics}.ts`, `policy.json`,
core tests, and integration/benchmark scripts. UI owns rendering, layout, keyboard,
selection, hold timing, and review dialogs. Rust owns collectors, execution, free
text Codex calls, catalogue persistence, and its native contract.

## Frontend API

```ts
import { createLauncherRuntime } from './lib/loop';
import { createNativeBridge } from './lib/bridge';

const native = createNativeBridge(invoke); // inject Tauri invoke; no Tauri import in core
const runtime = createLauncherRuntime({ native });
const unsubscribe = runtime.subscribe(snapshot => { /* render */ });
await runtime.open(); // bridge connection and warmup; reports loading/errors
runtime.setInput('the pdf I just downloaded');
runtime.setDebounce(80); // clamped to 40..150 ms; default 80
await runtime.execute('open_file', { primed: true, hold_ms: 0, confirmed: false });
runtime.cancelConfirmation(); // selection change or Escape
await runtime.dismiss(); // rejection only for an unsettled, unexecuted current decision
runtime.markUndone(outcomeId); // within 5 seconds for acceptance metrics
const proposal = await runtime.proposeTeaching();
await runtime.saveTeaching(proposal.id, editedToml); // explicit reviewed save
await runtime.flush();
await runtime.close();
unsubscribe();
```

`getSnapshot()` and `subscribe` return detached snapshots; callers cannot mutate
runtime state through them.
`snapshot` has `tick`, `input`, `status`, `stale`, `candidates`, `decision`,
`signals`, `teaching`, `bridge`, `warnings`, `errors`, and `metrics`.
`warnings` retains nonfatal NativeState context warnings, such as an unavailable
foreground window or clipboard. It refreshes with each native state and does not
change readiness or enter the error strip. Native invoke failures remain errors.

Candidate records have
`action`, `probability`, `confidence`, `band`, `args`, and `argumentError?`.
Calc and convert candidates can also expose
`preview: {status: pending | ready | error, text?, error?}`. The runtime sends
validated code-extracted arguments to the read-only native preview command.
Results are bound to the current tick and catalogue generation; superseded replies
are discarded. Preview never calls execute, logs an outcome, updates lastResult
or changes launch metrics. Optional `NativeBridge.preview(actionId,args)` lets
legacy test adapters omit preview; the real Tauri bridge always provides it.
`action` is the full native Action object, including description. `other` is
included. Only the leading candidate can get an act band, using soft-if's explicit
answer confidence. Probability is the per-action mass for frontend sizing.
Other candidates require at least 150 ms deliberate hold. UI owns measuring that
hold, layout, selection, hysteresis and keyboard behavior.
`directActivation: true` allows one explicit Enter or click on a current web-search
leader even below the act confidence threshold. It requires both the model choice
and probability leader to be `web_search`, the canonical `builtin/web_search/input`
executor, nonblank valid arguments, no confirmation guard and no gibberish brake.
The UI sends `primed: false`; this capability changes neither confidence nor band.
Pending, stale and superseded decisions remain blocked. Other actions retain their
hold and native confirmation requirements. A result never executes by arrival.
File candidates can expose `fileConfidence` and `argumentPending`. A selected
file action with unresolved alternatives gets a second semantic Choice before
execution, including when the user deliberately chooses a nonleading action.
`launcher.file` bands and optional threshold gate that choice. Recent-file
actions rerank their own recent list. Low-confidence file choices block execution.

`status` is `idle | warming | debouncing | deciding | ready | stale | closed`.
Errors are visible as `{source,message,at,code?,retryable?,timeoutMs?,httpStatus?}`.
Decision errors use `timeout | network | http | invalid-response | unknown`.
Superseded requests never append errors. Consecutive errors with the same source
and code are deduplicated. HTTP body text is excluded from decision diagnostics. Logging failures preserve bridge
pending records and remain visible; `flush()` rejects when persistence fails.

## Action intent and ambient context

Primary action decisions and public empty warmup use `buildActionState`. Typed
input determines the intended action. The state retains bounded window class,
clipboard kind, recent basenames, candidate metadata and time, but omits window
title and clipboard head entirely. Ambient prose cannot supply a different task
or consume the typed input's budget. Sensitive-window detection still uses the
original native snapshot to mark clipboard kind private. Neither builder mutates
that snapshot. Existing `buildState` and its bounded, redacted title/head remain
available for secondary semantic file reranking. Native execution still reads the
original validated arguments or full privacy-aware clipboard.

An optional `installedAppMatch` boolean is factual availability from the same
`matchInstalledApps` function used by local app rows. It is supplied only after a
successful app-index read. Pending or failed `getApps` leaves it absent, meaning
unknown. Legacy bridges without `getApps` use the complete `getState().apps`
index. Warmup omits it. No app names, IDs, keywords, icons or ranking enter this
metadata. A known empty index gives false. Local app matching and execution are
unchanged.

Model-only criteria preserve concrete catalogue operations before generic
routing. Otherwise complete information questions, including local/LAN inspection
with optional question punctuation, and setup/install/configure/investigate work
requests use `send_to_agent`. Bare topics without a stronger match and explicit
web searches use `web_search`. This does not use a lexical router or replace
model choice, confidence or probability mass. Displayed catalogue descriptions,
policy thresholds, confirmation, overrides and outcome semantics stay unchanged.
The primary decision deliberately cannot infer an action from clipboard prose or
a window title. The user must express that intent in the input. There is no
runtime semantic prepass. Synthetic routing regression cases are in `scripts/fixtures/routing-live-regressions.json`.

Input changes immediately invalidate in-flight work, including while a newer
input waits for debounce. Each decision includes all action, file/calc/url/paste/
question/gibberish, and urgency questions. Decisions go directly to browser-safe
`soft-if`, Ollama `http://127.0.0.1:11434`, model `clef-flash-q8:latest`, with a
1,500 ms active-decision timeout. Timeout keeps the previous distribution.
Initial readiness uses a separate 60,000 ms budget and a public empty state with
no clipboard, foreground window, file names or user input. It shares one in-flight
load across typing, dismiss and reopen. Only the latest unsuspended input resumes
after readiness; startup never executes an action. Closing the runtime cancels
readiness. Reopening after five minutes without a successful decision checks
readiness again, before the server's normal ten-minute model expiry.

`snapshot.model` contains `status: idle | warming | ready | error | closed`,
`timeoutMs`, `since` as epoch milliseconds, and optional structured `error`.
`runtime.retry()` explicitly retries readiness and the current input. A failed
startup waits for this retry rather than sending another load on each keystroke.
An active-decision failure can recover on the next input. `RuntimeOptions` accepts
`timeoutMs` and `warmupTimeoutMs` overrides for tests or deployments. Latency targets
are unchanged; the longer deadlines describe failure bounds, not achieved speed. File actions use a
second Choice over at most five code-generated fuzzy candidates. Eager reranking
runs only for a leading file action, using that action's recent or candidate list.
Nonleading file actions rerank on explicit execution. Auxiliary file-search signals
do not start speculative reranking or invalidate an otherwise usable decision.

## Execution and teaching

`execute` requires a current, nonstale decision and validated code-extracted
arguments. Dangerous actions first return `confirmationRequired` after an
unconfirmed request. Core privately retains Rust's token bound to action,
arguments, tick, catalogue generation and expiry. A distinct UI confirmation
calls execute again with `confirmed: true`; input changes, reload and
`cancelConfirmation()` drop the token. Cancellation also invalidates execution
intent before an awaited file rerank can dispatch to native. UI supplies actual
hold time.
Execution returns `executed | confirmationRequired | error`, `actionId`,
`message`, displayable `args`, optional `text`, `expiresInMs`, and `outcomeId`.
Execution results and dismissals log `action, primed, hold_ms, accepted`
plus chosen action, tick, request ID, timing, success/error, and outcome ID.
Dismissal suspends the runtime and invalidates pending input and confirmation.
It records at most one rejection for an unexecuted decision. A completed native
success or failure remains the outcome when the launcher closes. If the same
decision has a native call in flight, dismissal waits for that call's outcome:
success or failure wins; a cancelled confirmation-only response records rejection
once. A newer decision can be dismissed independently. `markUndone` remains an
explicit revision of an earlier outcome. These rules preserve the latest stored
outcome per request without changing soft-if's storage contract.
`accepted` labels the original model recommendation. A successful deliberate
override records `accepted: false, success: true, override: true` and the original
`predicted` action. Override success does not count as a misfire. Dismissals
contribute rejection labels to calibration and stay out of primed-fire acceptance.

Teaching keeps the last ten inputs whose current decision is led by `other`.
Three consecutive leading-other decisions set `teaching.available`. Proposal
generation is a native free-text call. The returned TOML stays in memory until
`saveTeaching(proposal.id, editedToml)` records explicit review and calls native
save. Proposal exposes `id`, native `proposalId`, `toml`, and `expiresInMs`.
Teaching state exposes `available, otherStreak, inputs, proposal?, pending, error?`.
Nothing is saved by generation, input, dismiss, or close.

`lastResult` retains `{actionId,text,outcomeId}` after executed text results.
`copy_result` extracts `args.text` from that result, never the command input.
`reload()` invalidates decisions and confirmation. `reportNativeError(source,
message)` accepts native diagnostics; UI owns native event listeners and hiding.
`backend`, `policy`, and `bridge: null` are injectable for browser demos/tests.

## Native boundary

`NativeBridge` methods are `getState(input)`, `execute(actionId,args,token?)`,
`reload()`, `proposeTeaching(inputs)`, `saveTeaching(proposalId,reviewedToml)`,
optional `preview(actionId,args)` returning `{actionId,text}`,
and optional `hideWindow()`. The Tauri adapter maps these to the exact native
command names in `NATIVE-CONTRACT.md`; types.ts follows its camelCase shapes.
Snapshot includes action catalogue and at most
five fuzzy file candidates computed in Rust. File paths stay local in execution;
decision state uses bounded metadata and opaque file keys, not full paths.
File modification timestamps are retained as `modifiedAt` Unix milliseconds.
Readable dates, code-derived recency rank, and `isDirectory` aid semantic rerank.
Clipboard actions pass no truncated `args.text`; Rust reads its full bounded,
privacy-aware clipboard after action confirmation. The model sees only the head.

`soft-if/node` is confined to integration/service scripts. Browser core uses
`createBrowserBridge({url:'http://localhost:7331',policy})`, `log:bridge.log`,
`connect/flush/status/onStatus/onError`, and `policy.subscribe` for live bands.
Package metadata must include `soft-if: file:../soft-if`. The runtime does not manage package,
frontend configuration, App, native source, or shared root files.

## Validation commands

`pnpm test` includes core tests and frontend-owned layout tests.
`pnpm exec tsx scripts/integration.ts` runs real HTTP/SSE, SQLite, live-policy,
failure/retry and calibration integration without a model or native execution.
`pnpm exec tsx bench/live.ts` uses sequential direct local Clef calls and the
actual runtime with 80 ms debounce. `bench/rerank.ts` checks synthetic Downloads
PDFs through the live runtime. Coordinate the single inference slot before runs.
Generated JSON evidence stays local and ignored.
`scripts/integration-results.json` is generated by the integration check.

Optional desktop validation requires a separately provisioned isolated display and WebKitWebDriver. See `VALIDATION.md` for scope and requirements.

## Explicit installed applications

`APP-UX-CONTRACT.md` adds `snapshot.appCandidates`, separate code-derived app
matches without probability, band or confidence. `runtime.executeApp(appId)`
executes an explicitly selected installed target independently of model readiness
and without a hold. Closed, suspended, stale-match, repeated and concurrent calls
reject. A missing, guarded or rebound app executor rejects with a clear error.
The production bridge sends `expectedApp:true` to enforce that constraint under
Rust's catalogue lock. App dispatch invalidates pending decisions and suspends
further input decisions until the next open/input. It does not cancel a shared
readiness load. Local launches update `localAppMetrics` and `localLastLaunch`,
with no model outcome, confidence or calibration label. All existing model
execution and terminal-outcome rules remain unchanged.
