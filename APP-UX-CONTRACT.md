# Installed application API

Rust provides installed discovery, bounded PNG icon data and execution. The core runtime matches applications locally; the UI owns ranking display and keyboard selection.

```ts
interface App {
  id: string; name: string; genericName?: string; keywords?: string[];
  icon?: string; // data:image/png;base64 only, never a file URL
}
interface InstalledAppCandidate {
  app: App; match: 'exact' | 'prefix' | 'terms'; score: number;
  matchedField?: 'name' | 'genericName' | 'keywords' | 'id';
}
// NativeBridge.getApps?(): Promise<App[]> -> get_apps
// RuntimeSnapshot.appCandidates: InstalledAppCandidate[]
// LauncherRuntime.executeApp(appId: string): Promise<ExecutionResponse>
```

`get_apps` reads the native cached index without collecting clipboard or window
context and without contacting the model. `open()` starts it alongside normal
readiness. Input changes synchronously match the index. App choices remain usable
while the model warms, fails or is offline. They have no confidence, probability
or policy band. Empty input gives no matches. Every query term must prefix-match
a metadata word from Name, GenericName, Keywords or the desktop ID stem. Opening
verbs are stripped. At most six deterministic matches are returned. Exact names, whole name words,
name prefixes, name words, desktop IDs, then generic/keyword matches determine
score, with a small multi-field bonus, shorter-name and alphabetical tie breaks.
No application has a hardcoded preference.

Native discovery applies XDG precedence before filtering Hidden and NoDisplay,
so a hidden user entry masks its system entry. The index has at most 256 valid
installed applications. Browser payloads contain no executable or desktop path.
Icons are cached PNG data, resized to at most 64 px, at most 24 KiB per encoded
PNG and at most 2 MiB of raw PNG data across the index. Oversized, missing or
invalid icons are omitted. PNG sources are capped at 4 MiB and 4096 px per
dimension. Self-contained SVG sources are capped at 512 KiB and rasterized
through the existing desktop pixbuf loader, with external references rejected.
Only PNG data crosses IPC. UI draws a name fallback. The existing production
`img-src 'self' data:` CSP permits these icons. Apps and icons never enter
`buildState`, model requests or app outcome logs.

`executeApp` captures the selected known installed ID and passes only `{appId}`
to the existing `open_app` native executor. If the initial native catalogue is
still pending, the selection waits only for that native state and rechecks its
input/selection intent before dispatch. It never waits for model readiness. It rejects closed/suspended runtimes,
nonmatching IDs, concurrent calls and repeat dispatch for the same input tick.
It rejects a missing, guarded or rebound `open_app` catalogue action with a clear
error. It never bypasses native confirmation, runs model commands or supplies a
shell/file path. The production adapter sends `expectedApp:true`; Rust checks the app-only
executor under the catalogue lock at dispatch. Native revalidates the known
installed entry and executable.
UI uses one Enter press/release for an ordinary selected application.

Local launches update `localAppMetrics: {launches,failures}` and
`localLastLaunch: {appId,name,status:'executed'|'error',at,launchId}`. They create
no synthetic model request or model outcome, and do not change calibration or
primed-action acceptance. Hiding after local dispatch does not label a model
recommendation accepted or replace an existing terminal outcome. The ordinary
model action lifecycle, hold and native confirmation rules remain in effect.
