import { writeFile } from 'node:fs/promises';
import { ollama } from 'soft-if';
import { createLauncherRuntime } from '../src/lib/loop';
import { buildState } from '../src/lib/state';
import { catalogueFixture, syntheticState, syntheticNative, settle } from './fixtures';

const state = syntheticState(await catalogueFixture(), true);
// File metadata replaces ambient context. This keeps the full action decision
// inside 300ms while exercising semantic selection of five synthetic files.
state.window = null; state.clipboard = { kind: 'empty', head: '' }; state.recent = [];
const runtime = createLauncherRuntime({ native: syntheticNative(state), backend: ollama({ model: 'clef-flash-q8:latest' }), bridge: null });
const results = [];
try {
  await runtime.open();
  for (let i = 0; i < 3; i++) {
    const start = performance.now();
    const snapshot = await settle(runtime, 'the pdf I just downloaded');
    const candidate = snapshot.candidates.find(candidate => candidate.action.id === 'open_file');
    results.push({ status: snapshot.status, elapsed_ms: performance.now() - start, selected: candidate?.args.file, band: candidate?.band, argumentError: candidate?.argumentError, expected: state.candidates.at(-1)?.path, newest_selected: candidate?.args.file === state.candidates.at(-1)?.path, errors: snapshot.errors });
  }
} finally { await runtime.close(); }
const output = { model: 'clef-flash-q8:latest', state: buildState('the pdf I just downloaded', state), results };
await writeFile(new URL('./rerank-results.json', import.meta.url), JSON.stringify(output, null, 2) + '\n');
console.log(JSON.stringify(output, null, 2));
