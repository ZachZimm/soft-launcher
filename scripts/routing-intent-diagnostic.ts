import { writeFile } from 'node:fs/promises';
import { SoftIf, choice, ollama } from 'soft-if';
import { buildQuestions } from '../src/lib/loop';
import { buildState } from '../src/lib/state';
import { catalogueFixture, syntheticState } from '../bench/fixtures';
import { routingCases } from './routing-cases';

const soft = new SoftIf({ backend: ollama({ url: (process.env.SOFT_LAUNCHER_CLEF_URL ?? 'http://127.0.0.1:11434'), model: (process.env.SOFT_LAUNCHER_CLEF_MODEL ?? 'clef-flash-q8:latest') }) });
const actions = await catalogueFixture();
const questions = { intent: choice('Classify only the user input by intended operation, not ambient clipboard/window words. Prefer concrete catalogue operations over general questions/work/topics. Question punctuation is optional.', {
  catalogue_operation: 'A specific launcher operation: open an app/file/URL, arithmetic or conversion, timer, clipboard operation, definition, explicit shell command, SSH connection, media/volume/desktop control, notes or Clef gateway status. Polite question wording does not change the operation.',
  question_or_work: 'A complete question asking for an answer/explanation or an instruction to perform work such as setup, install, configure, repair, investigate or inspect local/LAN services. No narrower launcher operation fits. Punctuation is optional.',
  topic_search: 'A topic expressed as bare keywords or a noun phrase, with no work instruction and no complete question. Also an explicit public web search. No narrower launcher operation fits.',
  other: 'No meaningful actionable intent or none of these fit.',
}) };
const supplement = process.argv.includes('--concrete-supplement');
const cases = supplement ? routingCases.filter(test => ['What is 2+2?', 'ssh buildbox'].includes(test.input)) : routingCases.filter(test => [0,1,2,3,5,6,13,14,17,18,19].includes(routingCases.indexOf(test)));
const output: unknown[] = [];
try {
  const warm = await soft.decide(JSON.stringify({ input: '' }), questions, { timeoutMs: 180000 });
  if (warm.error) throw new Error(warm.error);
  for (let repeat = 1; repeat <= 2; repeat++) for (const test of cases) {
    const started = performance.now();
    const intent = await soft.decide(JSON.stringify({ input: test.input }), questions, { name: 'launcher.routing', timeoutMs: 1500 });
    if (intent.error) throw new Error(intent.error);
    const finalQuestions = buildQuestions(actions);
    const trusted = !intent.intent.fallback && intent.intent.confidence >= .6;
    if (trusted) {
      const hint = { catalogue_operation: 'a specific catalogue operation; preserve that operation', question_or_work: 'a complete question or work instruction for send_to_agent, unless a narrower catalogue operation fits', topic_search: 'topic keywords or explicit web search for web_search, unless a narrower catalogue operation fits', other: 'an unmatched request' }[intent.intent.choice];
      finalQuestions.action.instructions += ` Semantic assessment of this input: ${hint}.`;
    }
    const final = await soft.decide(buildState(test.input, syntheticState(actions)).serialized, finalQuestions, { timeoutMs: Math.max(1, 1500 - (performance.now() - started)) });
    const totalMs = performance.now() - started;
    const sample = { repeat, input: test.input, expected: test.expected, intent, trusted, final, totalMs, passed: !final.error && final.action.choice === test.expected };
    output.push(sample);
    console.log(`${sample.passed ? 'PASS':'FAIL'} ${test.input}: ${intent.intent.choice} ${intent.intent.confidence.toFixed(3)} trusted=${trusted} -> ${final.action.choice} ${totalMs.toFixed(0)}ms ${final.error ?? ''}`);
  }
} finally {
  await soft.close();
  await writeFile(new URL(`./fixtures/routing-intent${supplement ? '-concrete-supplement' : ''}-diagnostic.json`, import.meta.url), JSON.stringify({ questions, samples: output }, null, 2)+'\n');
}
