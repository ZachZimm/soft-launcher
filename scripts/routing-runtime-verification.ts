import { writeFile } from 'node:fs/promises';
import { ollama } from 'soft-if';
import { createLauncherRuntime } from '../src/lib/loop';
import { catalogueFixture, syntheticState } from '../bench/fixtures';
import { routingCases } from './routing-cases';
import type { RuntimeSnapshot } from '../src/lib/types';

const state = syntheticState(await catalogueFixture());
const runtime = createLauncherRuntime({
  bridge: null,
  backend: ollama({ url: (process.env.SOFT_LAUNCHER_CLEF_URL ?? 'http://127.0.0.1:11434'), model: (process.env.SOFT_LAUNCHER_CLEF_MODEL ?? 'clef-flash-q8:latest') }),
  warmupTimeoutMs: 180000,
  native: {
    getState: async input => ({ ...structuredClone(state), candidates: input === 'open transcription-notes.txt' ? [{ path: '/home/synthetic/Documents/transcription-notes.txt', name: 'transcription-notes.txt', score: 1 }] : [] }),
    getApps: async () => structuredClone(state.apps),
    execute: async () => { throw new Error('Routing validation never executes actions'); },
    reload: async () => state.catalogue,
    proposeTeaching: async () => { throw new Error('Routing validation never generates text'); },
    saveTeaching: async () => { throw new Error('Routing validation never saves catalogue'); },
  },
});
const settle = (input: string): Promise<RuntimeSnapshot> => {
  runtime.setInput(input);
  const tick = runtime.getSnapshot().tick;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { unsubscribe(); reject(new Error('Runtime did not settle within bounded validation wait')); }, 5000);
    const unsubscribe = runtime.subscribe(snapshot => {
      if (snapshot.tick === tick && ['ready','stale'].includes(snapshot.status)) {
        clearTimeout(timer); unsubscribe(); resolve(snapshot);
      }
    });
  });
};
const samples = [];
const startedAt = new Date().toISOString();
try {
  await runtime.open();
  if (runtime.getSnapshot().status !== 'ready') throw new Error('Runtime warmup failed');
  for (let repeat = 1; repeat <= 2; repeat++) for (const test of routingCases) {
    const start = performance.now();
    const snapshot = await settle(test.input);
    const leading = snapshot.candidates[0];
    const passed = snapshot.status === 'ready' && !snapshot.stale && leading?.action.id === test.expected;
    samples.push({ ...test, repeat, totalMs: performance.now() - start, snapshot, passed });
    console.log(`${passed ? 'PASS' : 'FAIL'} ${repeat} ${test.input} -> ${leading?.action.id} ${leading?.confidence} ${snapshot.status} band=${leading?.band}`);
    if (snapshot.status !== 'ready') throw new Error('Real runtime failed at its production deadline; stopping');
  }
} finally {
  await runtime.close();
  await writeFile(new URL('./fixtures/routing-runtime-results.json', import.meta.url), JSON.stringify({ startedAt, completedAt: new Date().toISOString(), model: (process.env.SOFT_LAUNCHER_CLEF_MODEL ?? 'clef-flash-q8:latest'), endpoint: (process.env.SOFT_LAUNCHER_CLEF_URL ?? 'http://127.0.0.1:11434'), catalogueActions: state.catalogue.actions.length, activeDeadlineMs: 1500, debounceMs: 80, repetitions: 2, syntheticOnly: true, noExecution: true, samples, passed: samples.length === routingCases.length * 2 && samples.every(sample => sample.passed) }, null, 2)+'\n');
}
if (samples.some(sample => !sample.passed)) process.exitCode = 1;
