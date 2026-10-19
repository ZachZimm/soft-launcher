# Native contract v1

Rust owns context collection, catalogue validation, execution and text generation.
All invoke arguments and returned JSON fields use camelCase. Enum values use
camelCase. An invoke failure rejects with a plain, safe error string.

## Commands

```ts
type Action = {
  id: string; description: string; exec: {
    kind: 'xdg-open' | 'builtin' | 'app' | 'shell' | 'codex' | 'hermes' | 'bonsai' | 'none';
    name?: string; arg?: string; template?: string;
  }; needsText: boolean; requiresConfirmation: boolean;
};
type Catalogue = { generation: number; actions: Action[] };
type FileCandidate = { path: string; name: string; score: number; modifiedAt?: number; isDirectory?: boolean };
type App = { id: string; name: string }; // no executable supplied by browser
// Fields are optional. Rust rejects unknown keys, oversized values and invalid
// action-specific arguments. input is the original user text, not a model command.
type ExecArgs = {
  input?: string; file?: string; appId?: string; url?: string; expr?: string;
  text?: string; destination?: string; workspace?: number; processName?: string;
  host?: string; seconds?: number; value?: number; fromUnit?: string; toUnit?: string;
};
type NativeState = {
  catalogue: Catalogue;
  window: { class: string; title: string } | null;
  clipboard: { kind: 'empty' | 'text' | 'url' | 'image' | 'private'; head: string };
  recent: FileCandidate[]; candidates: FileCandidate[]; apps: App[];
  time: string; warnings: string[]; filesIndexed: number;
};
type ExecuteResult = {
  status: 'executed' | 'confirmationRequired'; actionId: string;
  message: string; text?: string; confirmationToken?: string;
  expiresInMs?: number;
};
type Proposal = { proposalId: string; toml: string; expiresInMs: number };
```

- `invoke<NativeState>('get_state', { input: string })`. Input defaults to empty.
  Context is bounded. Candidates are at most five fuzzy home-file matches; recent
  is at most five. Clipboard head is at most 200 characters. Apps is capped.
- `invoke<ExecuteResult>('execute', { request: { actionId, args: ExecArgs,
  confirmationToken?: string } })`. Revalidates the ID against the loaded catalogue.
  For shell and dangerous actions the first call only returns a token. The UI
  must show the action and arguments and require a separate confirm interaction,
  then repeat the identical request with that token. It expires in 15 seconds,
  is single use, and is bound to the catalogue generation, action ID, arguments
  and the validated execution plan.
  If native clipboard content changes between paste confirmations, the token fails.
  A frontend band, boolean, primed state or hold time never bypasses this check.
  Shell takes `args.input` as the explicit user-entered one-liner.
- `invoke<Catalogue>('reload')`. Reloads TOML and refreshes cached collectors.
  An invalid catalogue keeps the last good generation and rejects.
- `invoke<Proposal>('propose_action', { inputs: string[] })`. At most ten recent
  "other" inputs. The configured Codex transport drafts a single TOML action; Rust validates it, stores it
  in memory for ten minutes and returns it for review. It does not write files.
- `invoke<Catalogue>('save_proposal', { proposalId: string, reviewedToml: string })`.
  Only call after the user explicitly reviews and saves. Validates the reviewed
  single action, rejects duplicate IDs, atomically appends it to catalogue.toml,
  consumes the proposal and reloads. Text generation never runs an action.
- `invoke<Preview>('preview', { request: { actionId, args: ExecArgs } })` where
  `Preview = { actionId: string; text: string }`. Read-only result for a card, only
  for `calc` and `convert` bound to their own builtin evaluator. It shares the
  executor's evaluator but never executes, records history or logs an outcome.
  Unknown request fields, other IDs and rebound executors reject.
- `invoke<void>('hide_window')`. Hide the overlay after dismiss or successful fire
  and refocus the window that was active before it opened.
- `invoke<void>('fit_overlay', { height: number })`. The panel's content height in
  CSS px; native clamps it to 232..600 and resizes the 760-wide window (re-anchored
  on Hyprland). Ignored while hidden.
- The window also hides itself when it loses focus for 120 ms, emitting
  `native:visibility` `{ visible: false }`; the UI then dismisses the current input.

Plain paste reads native clipboard text separately, capped at 64 KiB; it does not
paste the truncated context head. Paste and region screenshots hide the overlay
and restore the original Hyprland focus before sending input or capturing.

`exec.kind=codex`, and legacy custom `bonsai` entries routed through Codex, read native clipboard text after action choice, capped at 8 KiB,
and returns generated `text` in ExecuteResult. The browser snippet is context
only. It never copies,
launches commands or changes files. `other` is a no-op. Timers emit an event when due.
Rust does not implement decision requests, policy storage, or outcome logging.
Browser soft-if connects directly to Ollama with `clef-flash-q8:latest` and the
sibling soft-if CLI at `http://127.0.0.1:7331` for persistence.

## Events

- `native:catalogue` payload `Catalogue`, emitted after successful hot reload/save.
- `native:error` payload `{ source: string, message: string }`, safe diagnostics.
- `native:visibility` payload `{ visible: boolean }`, focus input when true.
- `native:timer` payload `{ actionId: 'set_timer', seconds: number }`.

Listen through `@tauri-apps/api/event`. A failed background reload emits an error
and retains the last good catalogue. Poll get_state on open and after debounced
input; no raw command programs or secrets appear in snapshots.

## Native packaging

The desktop feature enables Tauri 2. Run `cargo tauri dev --features desktop` from
soft-launcher after Vite is listening at `http://127.0.0.1:5173`. Production assets
are `../dist` relative to src-tauri. Native config deliberately has no beforeDev
or beforeBuild hooks. See `README.md` in this directory for source-build commands.

## Installed application additions

`invoke<App[]>('get_apps')` returns the cached native index without clipboard,
foreground, file-search or model work. App metadata adds optional `genericName`,
`keywords` and `icon`, a bounded `data:image/png;base64` image. Executables,
desktop paths and icon source paths stay native. The index contains at most 256
valid installed targets, applying XDG precedence before Hidden/NoDisplay filters.
Icons are at most 64 px, 24 KiB each, with a 2 MiB raw-PNG total bound. Missing or
invalid icons are omitted. Bounded installed PNGs and self-contained SVGs are
rasterized locally; raw SVG and external references never cross IPC. `get_state` retains matching metadata and omits icons.
`reload` refreshes installed discovery as well as the existing collectors.

The execute request accepts optional `expectedApp:true`, used only by the
production explicit-app adapter. Under the same catalogue lock as validation and
dispatch, it requires `open_app` to remain an unguarded app executor. It cannot
bypass native confirmation or execute another action. The executor resolves only
a known indexed ID, re-reads that same desktop entry, rejects changed/removed or
hidden entries and unavailable executables, and uses its tokenized native argv.
See `APP-UX-CONTRACT.md` for core/UI ownership and the full consumer API.
