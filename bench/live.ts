import { writeFile } from 'node:fs/promises';
import { SoftIf, Policy, ollama } from 'soft-if';
import { createLauncherRuntime, buildQuestions, DECISION_TIMEOUT_MS, WARMUP_TIMEOUT_MS } from '../src/lib/loop';
import { buildState } from '../src/lib/state';
import { percentile } from '../src/lib/metrics';
import { catalogueFixture, syntheticState, syntheticNative, settle } from './fixtures';

const count = Number(process.env.LAUNCHER_BENCH_SAMPLES ?? 40);
if (!Number.isInteger(count) || count < 2 || count > 500) throw new Error('Samples must be 2..500');
const actions = await catalogueFixture();
const state = syntheticState(actions);
const questions = buildQuestions(actions);
const backend = ollama({ model: 'clef-flash-q8:latest', url: 'http://127.0.0.1:11434' });
const soft = new SoftIf({ backend, policy: new Policy(), defaultTimeoutMs: 300 });
const inputs = ['open firefox','search for local hiking routes','12 * 17 + 4','open https://example.org','set a timer for 5 minutes','switch to workspace 3','define probability','draft a reply','play music','ssh user@buildbox','lock the screen','increase volume','note buy coffee','copy result','summarise clipboard','what is the weather','zzqxwv nonsense','open project editor','find meeting notes','take a screenshot'];
const warmBuilt = buildState('open firefox', state);
const warm = await soft.decide(warmBuilt.serialized, questions, { name: 'launcher.bench.warmup', tick: 0, timeoutMs: WARMUP_TIMEOUT_MS });
const direct: { latency_ms: number; error?: string; input_tokens?: unknown; state_estimated_tokens: number; action: string }[] = [];
for (let i = 0; i < count; i++) {
  const built = buildState(inputs[i % inputs.length]!, state);
  const decision = await soft.decide(built.serialized, questions, { name: 'launcher.bench', tick: i + 1, timeoutMs: 300 });
  direct.push({ latency_ms: decision.latency_ms, ...(decision.error ? { error: decision.error } : {}), input_tokens: decision.usage?.input_tokens, state_estimated_tokens: built.estimatedTokens, action: decision.action.choice });
}
const runtime = createLauncherRuntime({ native: syntheticNative(state), backend, bridge: null });
await runtime.open();
const endToEnd: { latency_ms: number; status: string; error?: string }[] = [];
for (let i = 0; i < count; i++) {
  const start = performance.now();
  const result = await settle(runtime, inputs[i % inputs.length]!);
  endToEnd.push({ latency_ms: performance.now() - start, status: result.status, ...(result.status === 'stale' ? { error: result.errors.at(-1)?.message } : {}) });
}
await runtime.close();
const fileState = syntheticState(actions, true);
const fileRuntime = createLauncherRuntime({ native: syntheticNative(fileState), backend, bridge: null });
const rerank = await settle(fileRuntime, 'the pdf I just downloaded');
const file = rerank.candidates.find(candidate => candidate.action.id === 'open_file')?.args.file;
await fileRuntime.close(); await soft.close();
const numbers = (samples: { latency_ms: number }[]) => ({ p50_ms: percentile(samples.map(item => item.latency_ms), .5), p95_ms: percentile(samples.map(item => item.latency_ms), .95) });
const output = {
  at: new Date().toISOString(), model: 'clef-flash-q8:latest', samples: count,
  catalogue_actions: actions.length, questions: Object.keys(questions).length, direct_timeout_ms: 300, runtime_timeout_ms: DECISION_TIMEOUT_MS, warmup_timeout_ms: WARMUP_TIMEOUT_MS, debounce_ms: 80,
  warmup: { latency_ms: warm.latency_ms, error: warm.error, input_tokens: warm.usage?.input_tokens },
  state: { chars: warmBuilt.serialized.length, estimated_tokens: warmBuilt.estimatedTokens },
  direct: { ...numbers(direct), errors: direct.filter(item => item.error).length, timeouts: direct.filter(item => /timeout/i.test(item.error ?? '')).length, successful_samples: direct.filter(item => !item.error).length, successful: numbers(direct.filter(item => !item.error)), samples: direct },
  end_to_end: { ...numbers(endToEnd), errors: endToEnd.filter(item => item.status !== 'ready').length, samples: endToEnd },
  rerank: { status: rerank.status, selected: file, expected: fileState.candidates.at(-1)?.path, newest_selected: file === fileState.candidates.at(-1)?.path, leading: rerank.candidates[0]?.action.id, errors: rerank.errors },
};
await writeFile(new URL('./live-results.json', import.meta.url), JSON.stringify(output, null, 2) + '\n');
console.log(JSON.stringify({ ...output, direct: { ...output.direct, samples: undefined }, end_to_end: { ...output.end_to_end, samples: undefined } }, null, 2));
