import { fake, type Answer, type Backend, type WireRequest, type WireResponse } from 'soft-if';
import { demoKeywords } from './catalogue';

// Deterministic stand-in for Clef in the browser demo. It scores catalogue
// actions by keyword overlap with the input and answers every question the core
// asks, so the real runtime, policy bands and stale handling all run unchanged.
// It is a toy: plausible enough to drive the UI, not a model of Clef.

interface DemoState {
  input?: string;
  candidates?: { key: string; name: string; location: string; modifiedAt?: number }[];
}

function parseState(state: string): DemoState {
  try {
    return JSON.parse(state) as DemoState;
  } catch {
    return {};
  }
}

function softmax(logits: Record<string, number>, temperature: number): Record<string, number> {
  const max = Math.max(...Object.values(logits));
  const exp = Object.fromEntries(Object.entries(logits).map(([k, v]) => [k, Math.exp((v - max) / temperature)]));
  const total = Object.values(exp).reduce((s, x) => s + x, 0);
  return Object.fromEntries(Object.entries(exp).map(([k, v]) => [k, v / total]));
}

const isCalc = (s: string) => /^\s*[\d.(]+[\d\s.+\-*/%()^]*[+\-*/%^][\d\s.+\-*/%()^]*\d\)?\s*=?\s*$/.test(s);
const isUrl = (s: string) => /(?:https?:\/\/|www\.)\S+|\b\w+\.(?:com|org|net|io|dev)\b/i.test(s);

export function scoreActions(input: string, ids: readonly string[]): Record<string, number> {
  const text = input.toLowerCase().trim();
  const words = text.split(/\s+/).filter(Boolean);
  const last = words.at(-1) ?? '';
  const logits: Record<string, number> = {};
  for (const id of ids) {
    let s = 0;
    for (const kw of demoKeywords(id)) {
      if (words.includes(kw)) s += 1.4;
      else if (kw.length > 1 && last.length >= 2 && kw.startsWith(last)) s += 0.6;
      else if (!/^\w+$/.test(kw) && text.includes(kw)) s += 1.2;
    }
    // Unmatched actions sit well below zero so a long tail cannot outweigh real matches.
    logits[id] = s > 0 ? s : -2;
  }
  const matched = Object.values(logits).some((v) => v > 0);
  if (isCalc(text) && 'calc' in logits) logits.calc = (logits.calc ?? 0) + 4;
  if (isUrl(text) && 'open_url' in logits) logits.open_url = (logits.open_url ?? 0) + 4;
  if (/^[$!]/.test(text) && 'shell' in logits) logits.shell = (logits.shell ?? 0) + 4;
  if ('other' in logits) logits.other = words.length === 0 ? 0.4 : !matched ? 2.2 : text.length < 4 ? 1.2 : 0.6;
  return softmax(logits, 0.8);
}

function respond(req: WireRequest): WireResponse {
  const state = parseState(req.state);
  const input = state.input ?? '';
  const answers: Record<string, Answer> = {};
  for (const [key, q] of Object.entries(req.questions)) {
    if (q.type === 'choice' && key === 'action') {
      const probabilities = scoreActions(input, Object.keys(q.criteria));
      const [choice, top] = Object.entries(probabilities).sort((a, b) => b[1] - a[1])[0]!;
      // Explicit confidence is deliberately not the top probability, as with Clef.
      const confidence = Math.min(0.99, Math.max(0.05, top * 0.95 + (top > 0.5 ? 0.15 : 0)));
      answers[key] = { type: 'choice', choice, confidence, probabilities };
    } else if (q.type === 'choice') {
      // File rerank: prefer Downloads and recency for "downloaded/just/latest".
      const keys = Object.keys(q.criteria);
      const meta = new Map((state.candidates ?? []).map((c) => [c.key, c]));
      const wantsRecent = /download|just|latest|newest|recent/i.test(input);
      const logits = Object.fromEntries(
        keys.map((k, i) => {
          const c = meta.get(k);
          let s = -i * 0.3;
          if (wantsRecent && c?.location === 'Downloads') s += 1.5;
          if (wantsRecent && c?.modifiedAt) s += c.modifiedAt / 1e13;
          return [k, s];
        }),
      );
      const probabilities = softmax(logits, 0.5);
      const [choice, top] = Object.entries(probabilities).sort((a, b) => b[1] - a[1])[0]!;
      answers[key] = { type: 'choice', choice, confidence: top, probabilities };
    } else if (q.type === 'noul') {
      const text = input.toLowerCase();
      const p =
        key === 'is_calc' ? (isCalc(text) ? 0.95 : 0.04)
        : key === 'is_url' ? (isUrl(text) ? 0.93 : 0.03)
        : key === 'is_file_search' ? (/pdf|file|doc|download|open|invoice|report/.test(text) ? 0.8 : 0.1)
        : key === 'is_question' ? (/\?$|^(how|what|why|who|when|where)\b/.test(text) ? 0.85 : 0.1)
        : key === 'wants_paste' ? (/paste/.test(text) ? 0.9 : 0.03)
        : key === 'is_gibberish' ? (text.length >= 5 && !/[aeiou]/.test(text) && !/\d/.test(text) ? 0.9 : 0.04)
        : 0.1;
      answers[key] = { type: 'noul', p, confidence: Math.max(p, 1 - p), probabilities: { true: p, false: 1 - p } };
    } else {
      const n = q.criteria.length;
      const probabilities = Object.fromEntries(q.criteria.map((_, i) => [String(i), i === 1 ? 0.7 : 0.3 / (n - 1)]));
      const value = Object.entries(probabilities).reduce((s, [k, p]) => s + Number(k) * p, 0);
      answers[key] = { type: 'score', score: value, confidence: 0.7, probabilities };
    }
  }
  return { answers, backend: 'demo', latency_ms: 0, usage: { input_tokens: Math.ceil(req.state.length / 3), output_tokens: 0 } };
}

export interface DemoBackendOptions {
  /**
   * Make every Nth request after the first (readiness) answer late, past the
   * runtime's active-decision deadline, to exercise timeout and recovery UI.
   * Demo page: `?slow=2`.
   */
  slowEvery?: number;
  slowMs?: number;
}

export function createDemoBackend(delayMs = 35, options: DemoBackendOptions = {}): Backend {
  let requests = 0;
  const every = options.slowEvery && options.slowEvery > 0 ? Math.floor(options.slowEvery) : 0;
  return fake({
    name: 'demo',
    delayMs,
    respond: async (req: WireRequest) => {
      const n = requests++;
      if (every && n > 0 && n % every === 0) await new Promise((r) => setTimeout(r, options.slowMs ?? 2500));
      return respond(req);
    },
  });
}
