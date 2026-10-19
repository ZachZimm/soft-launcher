// Recording format for the replay page. A recording is a list of rendered ticks:
// the input after each keystroke and the distribution the launcher received for it.
// The live launcher can export its recent ticks in this format; the replay page
// ships a synthetic 20-keystroke fixture so it runs with no model at all.

export type Band = 'act' | 'confirm' | 'fallback';

export interface RecordedTick {
  input: string;
  /** Milliseconds since the first keystroke. Informational only; replay is tick based. */
  at: number;
  /** Per-action probability mass, excluding "other". */
  candidates: { id: string; p: number }[];
  /** Mass on "other". */
  other: number;
  /** Explicit model confidence for the leading action (not its probability). */
  confidence: number;
  /** Policy band for the leading action. */
  band: Band;
  /** Model's top choice, which may be "other". */
  choice: string;
}

export interface Recording {
  name: string;
  source: 'synthetic' | 'live';
  createdAt: string;
  ticks: RecordedTick[];
}

const BANDS = new Set<Band>(['act', 'confirm', 'fallback']);

function num(x: unknown, label: string): number {
  if (typeof x !== 'number' || !Number.isFinite(x)) throw new Error(`${label} must be a finite number`);
  return x;
}

/** Validate an untrusted recording (pasted or loaded from a file). */
export function parseRecording(raw: unknown): Recording {
  if (!raw || typeof raw !== 'object') throw new Error('Recording must be a JSON object');
  const r = raw as Record<string, unknown>;
  if (!Array.isArray(r.ticks) || r.ticks.length === 0) throw new Error('Recording needs a nonempty "ticks" array');
  if (r.ticks.length > 500) throw new Error('Recording is limited to 500 ticks');
  const ticks = r.ticks.map((t, i): RecordedTick => {
    if (!t || typeof t !== 'object') throw new Error(`tick ${i} must be an object`);
    const o = t as Record<string, unknown>;
    if (typeof o.input !== 'string') throw new Error(`tick ${i}: input must be text`);
    if (!Array.isArray(o.candidates)) throw new Error(`tick ${i}: candidates must be an array`);
    const candidates = o.candidates.map((c, j) => {
      const cc = c as Record<string, unknown>;
      if (!cc || typeof cc.id !== 'string' || !cc.id) throw new Error(`tick ${i} candidate ${j}: id must be text`);
      return { id: cc.id, p: num(cc.p, `tick ${i} candidate ${j} p`) };
    });
    const band = (BANDS.has(o.band as Band) ? o.band : 'fallback') as Band;
    return {
      input: o.input,
      at: typeof o.at === 'number' && Number.isFinite(o.at) ? o.at : i * 120,
      candidates,
      other: num(o.other ?? 0, `tick ${i} other`),
      confidence: num(o.confidence ?? 0, `tick ${i} confidence`),
      band,
      choice: typeof o.choice === 'string' ? o.choice : (candidates[0]?.id ?? 'other'),
    };
  });
  return {
    name: typeof r.name === 'string' && r.name ? r.name : 'Imported recording',
    source: r.source === 'live' ? 'live' : 'synthetic',
    createdAt: typeof r.createdAt === 'string' ? r.createdAt : new Date(0).toISOString(),
    ticks,
  };
}

/** Normalise a tick so actions plus "other" sum to one. */
export function normaliseTick(t: RecordedTick): RecordedTick {
  const total = t.candidates.reduce((s, c) => s + Math.max(0, c.p), 0) + Math.max(0, t.other);
  if (total <= 0) return t;
  return { ...t, candidates: t.candidates.map((c) => ({ id: c.id, p: Math.max(0, c.p) / total })), other: Math.max(0, t.other) / total };
}
