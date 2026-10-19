import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createBrowserBridge, Policy, calibrate } from 'soft-if';
import { SQLiteLog, startDebugger } from 'soft-if/node';
import type { BridgeEventSource } from 'soft-if';
import { createLauncherRuntime } from '../src/lib/loop';
import { backendFixture, nativeFixture } from '../src/lib/core-test-fixtures';
import { settle } from '../bench/fixtures';

// Small Node-only SSE client. Production uses the webview's native EventSource.
function eventSourceFactory(url: string): BridgeEventSource {
  const listeners = new Map<string, ((event: { data: string }) => void)[]>();
  const controller = new AbortController();
  const emit = (type: string, data = '') => { for (const fn of listeners.get(type) ?? []) fn({ data }); };
  void (async () => {
    try {
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok || !response.body) throw new Error(`SSE HTTP ${response.status}`);
      emit('open'); let buffer = ''; const decoder = new TextDecoder();
      for await (const chunk of response.body) {
        buffer += decoder.decode(chunk, { stream: true });
        let index: number;
        while ((index = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, index); buffer = buffer.slice(index + 2);
          const type = frame.match(/^event: (.+)$/m)?.[1];
          const data = frame.match(/^data: (.+)$/m)?.[1];
          if (type && data) emit(type, data);
        }
      }
    } catch { if (!controller.signal.aborted) emit('error'); }
  })();
  return { addEventListener(type, listener) { listeners.set(type, [...listeners.get(type) ?? [], listener]); }, close() { controller.abort(); } };
}
async function until(test: () => boolean, description: string): Promise<void> {
  const deadline = Date.now() + 3000;
  while (!test()) { if (Date.now() >= deadline) throw new Error(`Timed out: ${description}`); await new Promise(resolve => setTimeout(resolve, 10)); }
}
const directory = await mkdtemp(join(tmpdir(), 'soft-launcher-integration-'));
const log = new SQLiteLog(join(directory, 'soft-if.db'));
const serverPolicy = new Policy();
const server = await startDebugger({ log, policy: serverPolicy, port: 0 });
const policy = new Policy();
let failPosts = false;
const bridge = createBrowserBridge({ url: server.url, policy, eventSourceFactory, fetch: (url, init) => {
  if (failPosts && init?.method === 'POST') return Promise.resolve(new Response('simulated disk failure', { status: 507 }));
  return fetch(url, init);
} });
const runtime = createLauncherRuntime({ native: nativeFixture(), backend: backendFixture(), policy, bridge });
try {
  await runtime.open(); await until(() => bridge.status().state === 'connected', 'SSE connected');
  await settle(runtime, 'search something');
  assert.equal(runtime.getSnapshot().candidates[0]?.band, 'act');
  const fired = await runtime.execute('web_search', { primed: true, hold_ms: 0, confirmed: false });
  assert.equal(fired.status, 'executed');
  await runtime.flush();
  assert.equal(log.outcomes().length, 1);
  assert.deepEqual(Object.fromEntries(['action','primed','hold_ms','accepted'].map(key => [key, log.outcomes()[0]!.payload[key]])), { action: 'web_search', primed: true, hold_ms: 0, accepted: true });
  const update = await fetch(`${server.url}/policy`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bands: { act: .99 } }) });
  assert.equal(update.status, 200);
  await until(() => runtime.getSnapshot().candidates[0]?.band === 'confirm', 'live policy band changed');
  assert.equal(log.policyChanges().length, 1);
  assert.equal(runtime.getSnapshot().decision?.action.confidence, .94);
  failPosts = true;
  await settle(runtime, 'dismiss something'); await runtime.dismiss();
  await assert.rejects(runtime.flush(), /507/);
  const retained = bridge.status().pending;
  assert.ok(retained >= 3);
  assert.equal(runtime.getSnapshot().bridge.state, 'error');
  failPosts = false; await runtime.flush();
  assert.equal(bridge.status().pending, 0);
  assert.equal(log.outcomes().length, 2);
  assert.equal(log.outcomes().at(-1)?.payload.accepted, false);
  const calibration = calibrate(log.decisions(), log.outcomes(), log.branches(), { branch: 'launcher.action' });
  assert.equal(calibration.count, 2);
  assert.ok(Number.isFinite(calibration.ece));
  const evidence = { at: new Date().toISOString(), integration: 'passed', decisions: log.decisions().length, outcomes: log.outcomes().length, policyChanges: log.policyChanges().length, failure_pending_retained: retained, calibration: { count: calibration.count, ece: calibration.ece, branches: Object.keys(calibration.branches) } };
  await writeFile(new URL('./integration-results.json', import.meta.url), JSON.stringify(evidence, null, 2) + '\n');
  console.log(JSON.stringify(evidence, null, 2));
} finally {
  await runtime.close().catch(() => {}); await server.close(); log.close();
  await rm(directory, { recursive: true, force: true });
}
