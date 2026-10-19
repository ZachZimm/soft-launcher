import { writeFile } from 'node:fs/promises';
import { SoftIf, ollama } from 'soft-if';
import { buildQuestions } from '../src/lib/loop';
import { buildState } from '../src/lib/state';
import { catalogueFixture, syntheticState } from '../bench/fixtures';
import { routingCases } from './routing-cases';

const actions = await catalogueFixture();
const questions = buildQuestions(actions);
const state = syntheticState(actions);
const inputs = ['Is there a transcription server on buildbox?', 'transcription model', 'set up deadlock mod manager', 'Is there a transcription server on buildbox', 'speech recognition models', 'What is 2+2?', 'ssh buildbox', 'summarise clipboard'];
const soft = new SoftIf({ backend: ollama({ url: (process.env.SOFT_LAUNCHER_CLEF_URL ?? 'http://127.0.0.1:11434'), model: (process.env.SOFT_LAUNCHER_CLEF_MODEL ?? 'clef-flash-q8:latest') }) });
const samples = [];
const variants = process.argv.includes('--input-only') ? ['input_only'] : ['original', 'ambient_text_removed'];
try {
  const warm = await soft.decide(buildState('', { ...state, window: null, clipboard: { kind: 'empty', head: '' }, recent: [], candidates: [], time: '' }).serialized, questions, { timeoutMs: 180000 });
  if (warm.error) throw new Error(warm.error);
  for (const input of inputs) for (const variant of variants) {
    const fixture = structuredClone(state);
    if (variant !== 'original') {
      if (fixture.window) fixture.window.title = '';
      fixture.clipboard.head = '';
    }
    const serialized = variant === 'input_only' ? JSON.stringify({ input }) : buildState(input, fixture).serialized;
    const result = await soft.decide(serialized, questions, { name: 'launcher.routing.context-ablation', timeoutMs: 1500 });
    const expected = routingCases.find(test => test.input === input)!.expected;
    const passed = !result.error && !result.action.fallback && result.action.choice === expected;
    samples.push({ input, variant, expected, state: JSON.parse(serialized), result, passed });
    console.log(`${passed ? 'PASS' : 'FAIL'} ${variant} ${input} -> ${result.action.choice} ${result.action.confidence.toFixed(4)} ${Math.round(result.latency_ms)}ms ${result.error ?? ''}`);
    if (result.error) throw new Error('Ablation request failed; stop bounded run');
  }
} finally {
  await soft.close();
  await writeFile(new URL(`./fixtures/routing-context${process.argv.includes('--input-only') ? '-input-only' : ''}-ablation.json`, import.meta.url), JSON.stringify({ at: new Date().toISOString(), catalogueActions: actions.length, questions, timeoutMs: 1500, samples }, null, 2)+'\n');
}
