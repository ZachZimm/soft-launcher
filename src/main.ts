import './styles/app.css';
import { mount, unmount } from 'svelte';
import App from './App.svelte';
import type { AppEnv } from './ui/env';
import { createLauncherRuntime } from './lib/loop';
import { createNativeBridge } from './lib/bridge';
import type { LauncherRuntime } from './lib/types';
import { hasNative, listenNative, nativeInvoke, windowVisible } from './ui/platform';
import { createDecisionBackend, describeDecision, loadDecision, migrateDecision, saveDecision, warmupTimeoutFor, type DecisionConfig } from './ui/backendConfig';
import { loadSettings } from './ui/settings';
import { createDemoNative } from './demo/native';
import { createDemoBackend } from './demo/backend';

// Three ways to run:
//  - Tauri overlay: Rust native bridge, configured decision backend, 7331 logging bridge.
//  - Browser demo (default outside Tauri): simulated native, offline toy model, no logging.
//  - Browser demo with ?model=live: simulated native, real decision backend and logging,
//    still no native execution. Useful for feeling the row against Clef without Tauri.

const native = hasNative();
const params = new URLSearchParams(location.search);
const model: AppEnv['model'] = native || params.get('model') === 'live' ? 'live' : 'demo';
document.body.classList.toggle('browser', !native);
document.documentElement.dataset.mode = native ? 'native' : 'demo';
// "layer" (layer-shell overlay, no compositor border) or "window".
if (native) void nativeInvoke<string>('overlay_surface').then((surface) => (document.documentElement.dataset.surface = surface)).catch(() => undefined);

const target = document.getElementById('app')!;
let app: ReturnType<typeof mount> | undefined;
let runtime: LauncherRuntime | undefined;

function start(decision: DecisionConfig) {
  const bridge = native ? createNativeBridge(nativeInvoke) : createDemoNative({ latencyMs: 20, fail: params.get('fail')?.split(',') ?? [] });
  runtime = createLauncherRuntime({
    native: bridge,
    backend: model === 'live' ? createDecisionBackend(decision) : createDemoBackend(35, { slowEvery: Number(params.get('slow')) || 0 }),
    ...(model === 'demo' ? { bridge: null } : {}),
    ...(model === 'live' && warmupTimeoutFor(decision) ? { warmupTimeoutMs: warmupTimeoutFor(decision) } : {}),
  });
  const env: AppEnv = {
    native,
    model,
    backendLabel: model === 'live' ? describeDecision(decision) : 'offline demo model',
    decision,
    settings: loadSettings(),
    hideWindow: () => (native ? nativeInvoke<void>('hide_window') : Promise.resolve()),
    ...(native ? { isVisible: windowVisible } : {}),
    ...(native ? { fitWindow: (height: number) => nativeInvoke<void>('fit_overlay', { height }) } : {}),
    restart: (next: DecisionConfig) => {
      saveDecision(next);
      void restart(next);
    },
    ...(native ? { listen: listenNative } : {}),
  };
  app = mount(App, { target, props: { runtime, env } });
}

async function restart(decision: DecisionConfig) {
  const old = runtime;
  if (app) await unmount(app);
  app = undefined;
  await old?.close().catch(() => undefined);
  start(decision);
}

// A profile saved with the legacy on-this-computer default follows the build default once.
migrateDecision();
start(loadDecision());

window.addEventListener('pagehide', () => {
  void runtime?.flush().catch(() => undefined);
});
