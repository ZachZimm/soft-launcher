import { readFile, writeFile } from 'node:fs/promises';
import { SoftIf, Policy, type ChoiceAnswer, type NoulAnswer, type ScoreAnswer } from 'soft-if';
import { buildQuestions, DECISION_TIMEOUT_MS } from '../src/lib/loop';
import { buildState } from '../src/lib/state';
import { percentile } from '../src/lib/metrics';
import { catalogueFixture, syntheticState } from './fixtures';
import { laylaBackend, flatBackend, splitQuestions, GROUPS } from './layla-backend';
import { choice } from 'soft-if';
import { intents, acceptable, nouls } from './layla-intents';

// Laya launcher benchmark. Synthetic inputs only; never executes actions.
//   LAYLA_URL=http://127.0.0.1:8024 LAYLA_MODELS=english,typed-decisions,multilingual \
//   LAYLA_WARM_SAMPLES=100 pnpm exec tsx bench/layla.ts
const url = process.env.LAYLA_URL ?? 'http://127.0.0.1:8024';
const models = (process.env.LAYLA_MODELS ?? 'english,typed-decisions,multilingual').split(',') as ('english' | 'multilingual' | 'typed-decisions')[];
const warmCount = Number(process.env.LAYLA_WARM_SAMPLES ?? 100);
if (!Number.isInteger(warmCount) || warmCount < 2 || warmCount > 2000) throw new Error('Warm samples must be 2..2000');
const policyData = JSON.parse(await readFile(new URL('../policy.json', import.meta.url), 'utf8'));
const actions = await catalogueFixture();
const fullState = syntheticState(actions);
// Ablation: same catalogue and questions, but no window, clipboard or recent files.
const inputOnlyState = { ...fullState, window: null, clipboard: { kind: 'empty' as const, head: '' }, recent: [] };
const contexts = (process.env.LAYLA_CONTEXTS ?? 'launcher,input-only').split(',');
let state = fullState;
const questions = buildQuestions(actions);
const criteriaCount = Object.keys((questions.action as { criteria: Record<string, string> }).criteria).length;
const decideOptions = (tick: number) => ({ name: 'launcher.keystroke', branches: { action: 'launcher.action', is_gibberish: 'launcher.gibberish' }, tick, timeoutMs: DECISION_TIMEOUT_MS });
const get = async (path: string) => (await fetch(url + path)).json();
const stats = (values: number[]) => values.length ? { n: values.length, p50: percentile(values, .5), p95: percentile(values, .95), max: Math.max(...values), mean: values.reduce((a, b) => a + b, 0) / values.length } : { n: 0 };
const round = (x: number, d = 4) => Math.round(x * 10 ** d) / 10 ** d;
function ece(points: { confidence: number; correct: boolean }[], bins = 10) {
  let total = 0;
  const table = [];
  for (let b = 0; b < bins; b++) {
    const lo = b / bins, hi = (b + 1) / bins;
    const sel = points.filter(p => p.confidence >= lo && (b === bins - 1 ? p.confidence <= hi : p.confidence < hi));
    if (!sel.length) continue;
    const conf = sel.reduce((a, p) => a + p.confidence, 0) / sel.length, acc = sel.filter(p => p.correct).length / sel.length;
    total += sel.length / points.length * Math.abs(conf - acc);
    table.push({ lower: lo, upper: hi, count: sel.length, confidence: round(conf), accuracy: round(acc) });
  }
  return { n: points.length, ece: round(total), bins: table };
}
const policy = () => new Policy(policyData);
const bandOf = (p: Policy, confidence: number, emulated = false) => p.band({ type: 'choice', choice: 'x', probabilities: {}, confidence, emulated } as ChoiceAnswer, 'launcher.action');

const health = await get('/health');
const served = await get('/v1/models');

// Contract probe: the launcher's unmodified 31-criteria request.
const flatSoft = new SoftIf({ backend: flatBackend(url), policy: policy(), defaultTimeoutMs: DECISION_TIMEOUT_MS });
const flat = await flatSoft.decide(buildState('open firefox', fullState).serialized, questions, decideOptions(0));
await flatSoft.close();
const contractProbe = { request: 'buildQuestions(catalogue) unmodified: 8 questions, action choice with 31 criteria', error: flat.error ?? null, ok: !flat.error };

const results: Record<string, unknown> = {};
const wireBodies = intents.map(intent => ({ input: intent.input, state: buildState(intent.input, fullState).serialized, state_input_only: buildState(intent.input, inputOnlyState).serialized }));
const wireQuestions = splitQuestions(questions);
for (const context of contexts) for (const model of models) {
  {
    state = context === 'input-only' ? inputOnlyState : fullState;
    const mode = 'maxprob' as const;
    const backend = laylaBackend({ url, model, confidence: mode });
    const soft = new SoftIf({ backend, policy: policy(), defaultTimeoutMs: DECISION_TIMEOUT_MS });
    let tick = 0;
    const run = async (input: string) => {
      const built = buildState(input, state);
      const start = performance.now();
      const decision = await soft.decide(built.serialized, questions, decideOptions(++tick));
      return { decision, client_ms: performance.now() - start, built, raw: backend.last };
    };
    // First launcher-shaped request after whatever preceded this run.
    const first = await run(intents[0]!.input);
    const items = [];
    for (const intent of intents) {
      const { decision, client_ms, built, raw } = await run(intent.input);
      const ok = !decision.error;
      const action = decision.action as ChoiceAnswer | undefined;
      const accept = acceptable(intent);
      const probs = action?.probabilities ?? {};
      const values = Object.values(probs);
      const sum = values.reduce((a, b) => a + b, 0);
      const entropyConf = values.length ? Math.max(0, 1 - -values.reduce((s, p) => s + (p > 0 ? p * Math.log(p) : 0), 0) / Math.log(values.length)) : 0;
      const labels = nouls(intent);
      const nv = Object.fromEntries(['is_file_search', 'is_calc', 'is_url', 'wants_paste', 'is_question', 'is_gibberish'].map(k => [k, ok ? round((decision as any)[k].p) : null]));
      items.push({
        input: intent.input, category: intent.category, expected: intent.expected, accept: [...accept], original: !!intent.original,
        error: decision.error ?? null,
        schema_ok: ok && !!action && values.length === criteriaCount && Math.abs(sum - 1) < 0.005 && action.choice in probs && values.every(p => p >= 0 && p <= 1),
        brier: round(Object.entries(probs).reduce((s, [k, p]) => s + (p - (k === intent.expected ? 1 : 0)) ** 2, 0)),
        choice: action?.choice ?? null, p_top: action ? round(probs[action.choice]!) : null,
        p_expected: round(probs[intent.expected] ?? 0), p_accept: round([...accept].reduce((a, k) => a + (probs[k] ?? 0), 0)),
        exact: action?.choice === intent.expected, accepted: !!action && accept.has(action.choice),
        top3: Object.entries(probs).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => [k, round(v)]),
        confidence_maxprob: action ? round(action.confidence) : null, confidence_entropy: round(entropyConf),
        band_maxprob: action ? bandOf(policy(), action.confidence) : null, band_entropy: action ? bandOf(policy(), entropyConf) : null,
        band_maxprob_emulated_cap: action ? bandOf(policy(), action.confidence, true) : null,
        probabilities: Object.fromEntries(Object.entries(probs).map(([k, v]) => [k, round(v, 5)])),
        group_probabilities: raw?.group?.probabilities ?? null,
        group: raw?.group?.choice, group_p: raw?.group ? round(raw.group.probabilities[raw.group.choice]!) : null, group_native_confidence: raw?.group?.confidence,
        act_probability_group: raw?.act_probability?.action__group ?? null,
        nouls: nv, noul_labels: labels, urgency: ok ? round((decision.urgency as ScoreAnswer).score) : null,
        client_ms: round(client_ms, 2), server_ms: raw?.elapsed_ms ?? null, input_tokens: decision.usage?.input_tokens ?? null, output_tokens: decision.usage?.output_tokens ?? null,
        state_chars: built.serialized.length, state_estimated_tokens: built.estimatedTokens, routing: (raw?.routing as any)?.model ?? null,
      });
    }
    const warm = [];
    for (let i = 0; i < warmCount; i++) {
      const { decision, client_ms, raw } = await run(intents[i % intents.length]!.input);
      warm.push({ client_ms, server_ms: raw?.elapsed_ms ?? NaN, error: decision.error ?? null, input_tokens: decision.usage?.input_tokens });
    }
    await soft.close();
    const valid = items.filter(i => !i.error);
    const by = (pred: (i: typeof items[number]) => boolean) => { const sel = valid.filter(pred); return { n: sel.length, exact: round(sel.filter(i => i.exact).length / Math.max(1, sel.length)), accepted: round(sel.filter(i => i.accepted).length / Math.max(1, sel.length)) }; };
    const categories = Object.fromEntries([...new Set(items.map(i => i.category))].map(c => [c, by(i => i.category === c)]));
    const bandSummary = (key: 'band_maxprob' | 'band_entropy' | 'band_maxprob_emulated_cap') => {
      const act = valid.filter(i => i[key] === 'act'), confirm = valid.filter(i => i[key] === 'confirm');
      return { act: act.length, act_correct: act.filter(i => i.accepted).length, act_misfires: act.filter(i => !i.accepted).map(i => `${i.input} -> ${i.choice}`), act_on_ambiguous: act.filter(i => i.category === 'ambiguous').length, confirm: confirm.length, confirm_correct: confirm.filter(i => i.accepted).length, fallback: valid.filter(i => i[key] === 'fallback').length };
    };
    const noulAccuracy = Object.fromEntries(['is_file_search', 'is_calc', 'is_url', 'wants_paste', 'is_question', 'is_gibberish'].map(k => {
      const sel = valid.filter(i => i.noul_labels[k] !== undefined);
      const correct = sel.filter(i => (i.nouls[k]! >= .5) === i.noul_labels[k]);
      return [k, { n: sel.length, accuracy: round(correct.length / Math.max(1, sel.length)), positives: sel.filter(i => i.noul_labels[k]).length, wrong: sel.filter(i => !correct.includes(i)).map(i => `${i.input} p=${i.nouls[k]}`).slice(0, 12) }];
    }));
    const gibberishThreshold = new Policy(policyData).threshold('launcher.gibberish');
    // Separate the adapter's two stages: was the group right, and given the
    // expected group, did the within-group question rank the expected action first?
    const groupOf = (id: string) => Object.entries(GROUPS).find(([, g]) => g.actions.includes(id))![0];
    const stageGroup = valid.map(i => ({ ok: i.accept.some(a => groupOf(a) === i.group), p: i.group_probabilities?.[groupOf(i.expected)] ?? 0 }));
    const multi = valid.filter(i => GROUPS[groupOf(i.expected)]!.actions.length > 1);
    const stageSub = multi.map(i => { const members = GROUPS[groupOf(i.expected)]!.actions; return members.reduce((b, id) => i.probabilities[id]! > i.probabilities[b]! ? id : b, members[0]!) === i.expected; });
    const decomposition = {
      group_stage_accepted: round(stageGroup.filter(g => g.ok).length / Math.max(1, stageGroup.length)),
      group_stage_mean_p_expected_group: round(stageGroup.reduce((a, g) => a + g.p, 0) / Math.max(1, stageGroup.length)),
      within_group_given_expected_group: { n: stageSub.length, accuracy: round(stageSub.filter(Boolean).length / Math.max(1, stageSub.length)), chance_mean: round(multi.reduce((a, i) => a + 1 / GROUPS[groupOf(i.expected)]!.actions.length, 0) / Math.max(1, multi.length)) },
      group_choices: Object.fromEntries([...new Set(valid.map(i => i.group))].map(g => [g, valid.filter(i => i.group === g).length])),
    };
    const routing = [...new Set(items.map(i => i.routing))];
    results[`${model}/${context}`] = {
      model, context, confidence_rule: mode, intents: items.length, errors: items.length - valid.length,
      schema_valid: items.filter(i => i.schema_ok).length, routing_models: routing, routing_ok: routing.length === 1 && routing[0] === model,
      decomposition,
      accuracy: by(() => true), accuracy_original20: by(i => i.original), accuracy_unambiguous: by(i => i.category !== 'ambiguous'), categories,
      other_recall: by(i => i.expected === 'other'),
      calibration: {
        maxprob_vs_accepted: ece(valid.map(i => ({ confidence: i.confidence_maxprob!, correct: i.accepted }))),
        entropy_vs_accepted: ece(valid.map(i => ({ confidence: i.confidence_entropy, correct: i.accepted }))),
        brier_exact: round(valid.reduce((s, i) => s + i.brier, 0) / Math.max(1, valid.length)),
        mean_p_expected: round(valid.reduce((s, i) => s + i.p_expected, 0) / Math.max(1, valid.length)),
        act_probability_group: stats(valid.map(i => i.act_probability_group ?? NaN).filter(Number.isFinite)),
      },
      bands: { maxprob: bandSummary('band_maxprob'), entropy: bandSummary('band_entropy'), maxprob_emulated_cap: bandSummary('band_maxprob_emulated_cap') },
      nouls: noulAccuracy,
      gibberish_false_positive_at_threshold: { threshold: gibberishThreshold, count: valid.filter(i => !i.noul_labels.is_gibberish && i.category !== 'ambiguous' && i.nouls.is_gibberish! >= gibberishThreshold).length, of: valid.filter(i => i.noul_labels.is_gibberish === false).length },
      latency: {
        first_request: { client_ms: round(first.client_ms, 2), server_ms: first.raw?.elapsed_ms, error: first.decision.error ?? null },
        labelled_pass: { client: stats(valid.map(i => i.client_ms)), server: stats(valid.map(i => i.server_ms!).filter(Number.isFinite)) },
        warm: { samples: warm.length, errors: warm.filter(w => w.error).length, client: stats(warm.filter(w => !w.error).map(w => w.client_ms)), server: stats(warm.filter(w => !w.error).map(w => w.server_ms).filter(Number.isFinite)) },
      },
      usage: { wire_questions: first.raw?.wire_questions, launcher_questions: Object.keys(questions).length, input_tokens: stats(valid.map(i => i.input_tokens as number)), output_tokens: [...new Set(valid.map(i => i.output_tokens))], state_estimated_tokens: stats(valid.map(i => i.state_estimated_tokens)), state_chars: stats(valid.map(i => i.state_chars)) },
      items,
    };
  }
}

// Diagnostic without the grouping adapter: one native choice over a 20-action
// subset (the server maximum), only on intents whose expected action is in it.
const FLAT20 = ['open_app', 'open_file', 'open_recent', 'web_search', 'open_url', 'calc', 'convert', 'draft_reply', 'summarise_clipboard', 'switch_workspace', 'lock_screen', 'screenshot_region', 'set_timer', 'note_to_inbox', 'search_notes', 'media_play_pause', 'volume_up', 'ssh_to_host', 'define_word', 'other'];
const allCriteria = (questions.action as { criteria: Record<string, string> }).criteria;
const flat20Questions = { action: choice((questions.action as { instructions: string }).instructions, Object.fromEntries(FLAT20.map(id => [id, allCriteria[id]!]))) };
const flat20Intents = intents.filter(i => FLAT20.includes(i.expected));
const flat20: Record<string, unknown> = {};
for (const context of contexts) for (const model of models) {
  const soft = new SoftIf({ backend: flatBackend(url, model), policy: policy(), defaultTimeoutMs: DECISION_TIMEOUT_MS });
  const rows = [];
  for (const intent of flat20Intents) {
    const d = await soft.decide(buildState(intent.input, context === 'input-only' ? inputOnlyState : fullState).serialized, flat20Questions, { timeoutMs: DECISION_TIMEOUT_MS });
    const a = d.action as ChoiceAnswer | undefined;
    rows.push({ input: intent.input, expected: intent.expected, choice: a?.choice ?? null, error: d.error ?? null, accepted: !!a && acceptable(intent).has(a.choice), native_confidence: a?.confidence ?? null, p_top: a ? round(a.probabilities[a.choice]!) : null, latency_ms: round(d.latency_ms, 2) });
  }
  await soft.close();
  const ok = rows.filter(r => !r.error);
  flat20[`${model}/${context}`] = { n: rows.length, errors: rows.length - ok.length, accepted: round(ok.filter(r => r.accepted).length / Math.max(1, ok.length)), mean_p_top: round(ok.reduce((a, r) => a + r.p_top!, 0) / Math.max(1, ok.length)), latency: stats(ok.map(r => r.latency_ms)), choices: Object.fromEntries([...new Set(ok.map(r => r.choice))].map(c => [c, ok.filter(r => r.choice === c).length])), rows };
}

// Clef reference from the existing bench/live-results.json (no new inference).
// live.ts cycled its 20 inputs in order, so sample i used input i % 20.
const live = JSON.parse(await readFile(new URL('./live-results.json', import.meta.url), 'utf8'));
const original = intents.filter(i => i.original);
const liveInputs = ['open firefox','search for local hiking routes','12 * 17 + 4','open https://example.org','set a timer for 5 minutes','switch to workspace 3','define probability','draft a reply','play music','ssh user@buildbox','lock the screen','increase volume','note buy coffee','copy result','summarise clipboard','what is the weather','zzqxwv nonsense','open project editor','find meeting notes','take a screenshot'];
const clefItems = live.direct.samples.map((s: any, i: number) => { const intent = original.find(o => o.input === liveInputs[i % 20])!; return { input: intent.input, choice: s.action, exact: s.action === intent.expected, accepted: acceptable(intent).has(s.action) }; });
const clefReference = {
  source: 'bench/live-results.json (' + live.at + ', ' + live.model + ', local host, earlier run; not re-run here)',
  samples: clefItems.length, exact: round(clefItems.filter((i: any) => i.exact).length / clefItems.length), accepted: round(clefItems.filter((i: any) => i.accepted).length / clefItems.length),
  disagreements: [...new Set(clefItems.filter((i: any) => !i.accepted).map((i: any) => `${i.input} -> ${i.choice}`))],
  latency: { direct_p50_ms: live.direct.p50_ms, direct_p95_ms: live.direct.p95_ms, input_tokens: live.warmup.input_tokens, note: 'runtime (80ms debounce) p95 340ms per VALIDATION.md' },
};

const output = {
  at: new Date().toISOString(), url, health, served_models: served.models, contract_probe: contractProbe,
  catalogue_actions: actions.length, launcher_questions: Object.keys(questions).length, intents: intents.length, warm_samples: warmCount, timeout_ms: DECISION_TIMEOUT_MS,
  results, flat20_diagnostic: { actions: FLAT20, note: 'single native choice, 20 criteria (server max); temperature bucket choice:11+ = 0.1 sharpens but does not change argmax; options share head_max_len', results: flat20 }, clef_reference: clefReference,
};
await writeFile(new URL('./layla-results.json', import.meta.url), JSON.stringify(output, null, 2) + '\n');
await writeFile(new URL('./layla-wire-requests.json', import.meta.url), JSON.stringify({ questions: { ...wireQuestions, flat20__action: flat20Questions.action }, states: wireBodies }) + '\n');
const brief = Object.fromEntries(Object.entries(results).map(([k, r]: [string, any]) => [k, { errors: r.errors, schema_valid: r.schema_valid, accuracy: r.accuracy, original20: r.accuracy_original20, unambiguous: r.accuracy_unambiguous, categories: r.categories, bands: { maxprob: { ...r.bands.maxprob, act_misfires: r.bands.maxprob.act_misfires.length }, entropy: { ...r.bands.entropy, act_misfires: r.bands.entropy.act_misfires.length } }, ece: [r.calibration.maxprob_vs_accepted.ece, r.calibration.entropy_vs_accepted.ece], nouls: Object.fromEntries(Object.entries(r.nouls).map(([n, v]: [string, any]) => [n, v.accuracy])), latency: { first: r.latency.first_request, warm_client: r.latency.warm.client, warm_server: r.latency.warm.server }, tokens: r.usage.input_tokens }]));
console.log(JSON.stringify({ contract_probe: contractProbe, results: brief, clef_reference: { ...clefReference } }, null, 2));
