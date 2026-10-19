import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { SoftIf, ollama } from 'soft-if';
import { buildQuestions } from '../src/lib/loop';
import { buildActionState, buildState } from '../src/lib/state';
import { catalogueFixture, syntheticState } from '../bench/fixtures';
import { routingCases } from './routing-cases';

const phase = process.argv[2];
if (phase !== 'baseline' && phase !== 'revised') throw new Error('Specify baseline or revised');
const actions = await catalogueFixture();
if (actions.length !== 31) throw new Error('Expected the real 31-action catalogue');
const questions = phase === 'baseline'
  ? JSON.parse(await readFile(new URL('./fixtures/routing-baseline-questions.json', import.meta.url), 'utf8')) as ReturnType<typeof buildQuestions>
  : buildQuestions(actions);
const backend = ollama({ url: (process.env.SOFT_LAUNCHER_CLEF_URL ?? 'http://127.0.0.1:11434'), model: (process.env.SOFT_LAUNCHER_CLEF_MODEL ?? 'clef-flash-q8:latest') });
const soft = new SoftIf({ backend });
const state = syntheticState(actions);
const warmState = { ...state, window: null, clipboard: { kind: 'empty' as const, head: '' }, recent: [], candidates: [], apps: [], time: '' };
const startedAt = new Date().toISOString();
const samples = [];
try {
  // One public empty cold-load request, separate from active routing samples.
  const warm = await soft.decide(buildState('', warmState).serialized, questions, { name: 'launcher.routing.warmup', timeoutMs: 180000 });
  if (warm.error || warm.action.fallback) throw new Error(warm.error ?? 'Warmup failed');
  console.log(`warmup ${Math.round(warm.latency_ms)} ms`);
  for (const test of (process.argv.includes('--focus') ? routingCases.slice(0, 10) : routingCases)) {
    const fixture = structuredClone(state);
    if ('file' in test) fixture.candidates = [{ path: '/home/synthetic/Documents/transcription-notes.txt', name: 'transcription-notes.txt', score: 1 }];
    const built = phase === 'baseline' ? buildState(test.input, fixture) : buildActionState(test.input, fixture, fixture.apps);
    const result = await soft.decide(built.serialized, questions, { name: 'launcher.routing.validation', timeoutMs: 15000 });
    const { id: _id, ...decision } = result;
    const leading = Object.entries(result.action.probabilities).sort((a,b) => b[1] - a[1])[0]?.[0];
    const sample = { ...test, state: built.state, estimatedTokens: built.estimatedTokens, decision, leading, passed: !result.error && !result.action.fallback && leading === test.expected && result.action.choice === test.expected };
    samples.push(sample);
    console.log(`${sample.passed ? 'PASS' : 'FAIL'} ${test.input} -> ${result.action.choice} confidence=${result.action.confidence} ${Math.round(result.latency_ms)} ms`);
    if (result.error) throw new Error('Decision failed; stopping bounded run');
  }
  const output = { phase, startedAt, completedAt: new Date().toISOString(), endpoint: (process.env.SOFT_LAUNCHER_CLEF_URL ?? 'http://127.0.0.1:11434'), model: (process.env.SOFT_LAUNCHER_CLEF_MODEL ?? 'clef-flash-q8:latest'), catalogueSha256: createHash('sha256').update(await readFile(new URL('../catalogue.toml', import.meta.url))).digest('hex'), catalogueActions: actions.length, questions, warmup: { latencyMs: warm.latency_ms, usage: warm.usage }, activeTimeoutMs: 15000, syntheticOnly: true, sequential: true, samples, passed: samples.every(sample => sample.passed) };
  await writeFile(new URL(`./fixtures/routing-${phase}${process.argv.includes('--focus') ? '-focus' : ''}-results.json`, import.meta.url), JSON.stringify(output, null, 2) + '\n');
  if (phase === 'revised' && !output.passed) process.exitCode = 1;
} finally { await soft.close(); }
