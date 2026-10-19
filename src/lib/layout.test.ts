import { describe, expect, it } from 'vitest';
import {
  DEFAULT_LAYOUT,
  EMPTY_ORDER,
  OTHER_CARD_OPACITY,
  byProbability,
  capacity,
  cardWidths,
  countFlicker,
  countReorders,
  layoutRow,
  leaderOpacity,
  nextOtherStreak,
  placeRow,
  stepOrder,
  type Entry,
  type LayoutParams,
  type OrderState,
} from './layout';
import { FIXTURE_20 } from '../demo/fixture';
import { digest, replay } from '../demo/replay';

const P: LayoutParams = { ...DEFAULT_LAYOUT };
const e = (m: Record<string, number>): Entry[] => Object.entries(m).map(([id, p]) => ({ id, p }));
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

function run(seq: Record<string, number>[], params = P): string[][] {
  let s: OrderState = EMPTY_ORDER;
  return seq.map((m) => {
    s = stepOrder(s, e(m), params);
    return s.order;
  });
}

describe('stepOrder hysteresis', () => {
  it('starts in probability order with deterministic ties', () => {
    expect(run([{ b: 0.3, a: 0.3, c: 0.4 }])[0]).toEqual(['c', 'a', 'b']);
  });

  it('does not swap on one tick above the margin', () => {
    const orders = run([{ a: 0.5, b: 0.4 }, { a: 0.3, b: 0.6 }]);
    expect(orders[1]).toEqual(['a', 'b']);
  });

  it('swaps after two consecutive ticks above the margin', () => {
    const orders = run([{ a: 0.5, b: 0.4 }, { a: 0.3, b: 0.6 }, { a: 0.3, b: 0.6 }]);
    expect(orders.map((o) => o.join(''))).toEqual(['ab', 'ab', 'ba']);
  });

  it('never swaps when the lead is within the 0.08 margin', () => {
    const orders = run([{ a: 0.5, b: 0.4 }, ...Array.from({ length: 6 }, () => ({ a: 0.45, b: 0.53 }))]);
    expect(orders.every((o) => o[0] === 'a')).toBe(true);
  });

  it('treats exactly the margin as not exceeding it', () => {
    const orders = run([{ a: 0.5, b: 0.4 }, { a: 0.4, b: 0.48 }, { a: 0.4, b: 0.48 }, { a: 0.4, b: 0.48 }]);
    expect(orders.at(-1)).toEqual(['a', 'b']);
  });

  it('resets the streak when a tick fails the test', () => {
    const orders = run([{ a: 0.5, b: 0.4 }, { a: 0.3, b: 0.6 }, { a: 0.45, b: 0.5 }, { a: 0.3, b: 0.6 }]);
    expect(orders.at(-1)).toEqual(['a', 'b']);
  });

  it('lets a decisive winner pass several cards at once', () => {
    const orders = run([{ a: 0.4, b: 0.3, c: 0.2, d: 0.1 }, { a: 0.1, b: 0.1, c: 0.1, d: 0.7 }, { a: 0.1, b: 0.1, c: 0.1, d: 0.7 }]);
    expect(orders.at(-1)).toEqual(['d', 'a', 'b', 'c']);
  });

  it('honours swapTicks and margin parameters', () => {
    const seq = [{ a: 0.5, b: 0.4 }, { a: 0.4, b: 0.5 }];
    expect(run(seq, { ...P, swapMargin: 0, swapTicks: 1 }).at(-1)).toEqual(['b', 'a']);
    const three = { ...P, swapTicks: 3 };
    expect(run([...seq.slice(0, 1), { a: 0.2, b: 0.7 }, { a: 0.2, b: 0.7 }], three).at(-1)).toEqual(['a', 'b']);
    expect(run([...seq.slice(0, 1), { a: 0.2, b: 0.7 }, { a: 0.2, b: 0.7 }, { a: 0.2, b: 0.7 }], three).at(-1)).toEqual(['b', 'a']);
  });

  it('drops vanished ids and inserts new ids where their probability earns', () => {
    const orders = run([{ a: 0.5, b: 0.3, c: 0.2 }, { a: 0.5, c: 0.2, n: 0.3 }]);
    expect(orders[1]).toEqual(['a', 'n', 'c']);
  });

  it('does not mutate the previous state', () => {
    const s0 = stepOrder(EMPTY_ORDER, e({ a: 0.5, b: 0.4 }), P);
    const frozen = JSON.stringify(s0);
    stepOrder(s0, e({ a: 0.2, b: 0.8 }), P);
    expect(JSON.stringify(s0)).toBe(frozen);
  });

  it('clears a spent streak after the swap', () => {
    let s = stepOrder(EMPTY_ORDER, e({ a: 0.5, b: 0.4 }), P);
    s = stepOrder(s, e({ a: 0.3, b: 0.6 }), P);
    s = stepOrder(s, e({ a: 0.3, b: 0.6 }), P);
    expect(s.order).toEqual(['b', 'a']);
    expect(s.streaks).toEqual({});
    // a needs its own two ticks to win the lead back.
    s = stepOrder(s, e({ a: 0.6, b: 0.3 }), P);
    expect(s.order).toEqual(['b', 'a']);
    s = stepOrder(s, e({ a: 0.6, b: 0.3 }), P);
    expect(s.order).toEqual(['a', 'b']);
  });

  it('ignores invalid probabilities instead of throwing', () => {
    expect(stepOrder(EMPTY_ORDER, [{ id: 'a', p: Number.NaN }, { id: 'b', p: 0.2 }], P).order).toEqual(['b', 'a']);
  });
});

describe('cardWidths', () => {
  const row = { widthMode: 'sqrt' as const, rowWidth: 720, gap: 8, minWidth: 96 };

  it('fills the available width exactly', () => {
    const ws = cardWidths([0.5, 0.3, 0.15, 0.05], row);
    expect(sum(ws)).toBeCloseTo(720 - 8 * 3, 6);
  });

  it('is proportional to sqrt(p) when no card hits the floor', () => {
    const ws = cardWidths([0.64, 0.36], row);
    expect(ws[0]! / ws[1]!).toBeCloseTo(0.8 / 0.6, 6);
  });

  it('is proportional to p in linear mode', () => {
    const ws = cardWidths([0.6, 0.4], { ...row, widthMode: 'linear' });
    expect(ws[0]! / ws[1]!).toBeCloseTo(1.5, 6);
  });

  it('pins small cards to the minimum width', () => {
    const ws = cardWidths([0.9, 0.05, 0.03, 0.02], { ...row, widthMode: 'linear' });
    expect(ws.slice(1)).toEqual([96, 96, 96]);
    expect(ws[0]).toBeCloseTo(720 - 24 - 96 * 3, 6);
  });

  it('keeps small options wider under sqrt than under linear', () => {
    const ps = [0.7, 0.2, 0.1];
    const sq = cardWidths(ps, row);
    const li = cardWidths(ps, { ...row, widthMode: 'linear' });
    expect(sq[2]!).toBeGreaterThan(li[2]!);
    expect(sq[0]!).toBeLessThan(li[0]!);
  });

  it('shares equally when all probabilities are zero', () => {
    expect(cardWidths([0, 0], row)).toEqual([356, 356]);
  });

  it('shrinks the floor rather than overflowing a narrow row', () => {
    const ws = cardWidths([0.25, 0.25, 0.25, 0.25], { ...row, rowWidth: 200 });
    expect(sum(ws)).toBeCloseTo(200 - 24, 6);
  });

  it('is monotonic in probability', () => {
    const ws = cardWidths([0.4, 0.3, 0.2, 0.1], row);
    for (let i = 1; i < ws.length; i++) expect(ws[i]!).toBeLessThanOrEqual(ws[i - 1]!);
  });
});

describe('layoutRow', () => {
  it('positions cards left to right without overlap', () => {
    const { cards } = layoutRow(EMPTY_ORDER, e({ a: 0.5, b: 0.3, c: 0.2 }), P, 0.7);
    for (let i = 1; i < cards.length; i++) expect(cards[i]!.x).toBeCloseTo(cards[i - 1]!.x + cards[i - 1]!.width + P.gap, 6);
    const last = cards.at(-1)!;
    expect(last.x + last.width).toBeCloseTo(P.rowWidth, 6);
  });

  it('caps the card count by capacity and reports the rest as hidden', () => {
    const many = Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`k${String(i).padStart(2, '0')}`, (12 - i) / 78]));
    const r = layoutRow(EMPTY_ORDER, e(many), P, 0.5);
    expect(r.cards.length).toBe(capacity(P));
    expect(r.hidden.length).toBe(12 - capacity(P));
    expect(r.hidden[0]!.p).toBeGreaterThanOrEqual(r.hidden.at(-1)!.p);
  });

  it('omits candidates under the minimum probability', () => {
    const r = layoutRow(EMPTY_ORDER, e({ a: 0.9, b: 0.005 }), P, 0.9);
    expect(r.cards.map((c) => c.id)).toEqual(['a']);
    expect(r.hidden.map((c) => c.id)).toEqual(['b']);
  });

  it('encodes leader confidence in opacity and keeps others constant', () => {
    const lo = layoutRow(EMPTY_ORDER, e({ a: 0.5, b: 0.5 }), P, 0.2).cards;
    const hi = layoutRow(EMPTY_ORDER, e({ a: 0.5, b: 0.5 }), P, 0.95).cards;
    expect(hi[0]!.opacity).toBeGreaterThan(lo[0]!.opacity);
    expect(lo[1]!.opacity).toBe(OTHER_CARD_OPACITY);
    expect(leaderOpacity(1)).toBe(1);
    expect(leaderOpacity(Number.NaN)).toBe(leaderOpacity(0));
  });
});

describe('other streak', () => {
  it('counts consecutive ticks with other leading and resets otherwise', () => {
    let s = 0;
    s = nextOtherStreak(s, 0.4, e({ a: 0.3 }));
    s = nextOtherStreak(s, 0.4, e({ a: 0.3 }));
    expect(s).toBe(2);
    s = nextOtherStreak(s, 0.3, e({ a: 0.3 }));
    expect(s).toBe(0);
  });
});

describe('flicker metrics', () => {
  it('counts back-and-forth flips but not a single settled swap', () => {
    expect(countFlicker([['a', 'b'], ['b', 'a'], ['a', 'b'], ['b', 'a']])).toBe(2);
    expect(countFlicker([['a', 'b'], ['b', 'a'], ['b', 'a'], ['b', 'a']])).toBe(0);
    expect(countReorders([['a', 'b'], ['b', 'a'], ['b', 'a'], ['a', 'b']])).toBe(2);
  });

  it('ignores order changes caused only by cards appearing', () => {
    expect(countReorders([['a', 'b'], ['a', 'n', 'b']])).toBe(0);
  });
});

describe('20-keystroke replay', () => {
  const orders = (params: LayoutParams) => replay(FIXTURE_20, params).map((f) => f.row.state.order);

  it('has twenty ticks', () => {
    expect(FIXTURE_20.ticks).toHaveLength(20);
  });

  it('is deterministic across runs', () => {
    expect(digest(replay(FIXTURE_20, P))).toBe(digest(replay(FIXTURE_20, P)));
    expect(digest(replay(FIXTURE_20, { ...P, widthMode: 'linear' }))).not.toBe(digest(replay(FIXTURE_20, P)));
  });

  it('shows no reorder flicker with the default hysteresis', () => {
    expect(countFlicker(orders(P))).toBe(0);
  });

  it('flickers without hysteresis, which is what the rule removes', () => {
    expect(countFlicker(orders({ ...P, swapMargin: 0, swapTicks: 1 }))).toBeGreaterThan(2);
  });

  it('swaps open_recent into the lead once and primes it at the end', () => {
    const frames = replay(FIXTURE_20, P);
    const leaders = frames.map((f) => f.row.cards[0]!.id);
    const firstRecent = leaders.indexOf('open_recent');
    expect(firstRecent).toBeGreaterThan(9);
    expect(leaders.slice(firstRecent).every((id) => id === 'open_recent')).toBe(true);
    expect(frames.at(-1)!.primedId).toBe('open_recent');
    expect(frames.slice(0, -1).every((f) => f.primedId === null)).toBe(true);
  });

  it('offers teaching on the third tick that other leads', () => {
    const offers = replay(FIXTURE_20, P).map((f) => f.offerTeach);
    expect(offers.slice(0, 4)).toEqual([false, false, true, true]);
    expect(offers.slice(4).some(Boolean)).toBe(false);
  });

  it('sorts helper output by probability then id', () => {
    expect(byProbability(e({ b: 0.2, a: 0.2, c: 0.5 })).map((x) => x.id)).toEqual(['c', 'a', 'b']);
  });
});

describe('placeRow', () => {
  it('re-lays out geometry without advancing hysteresis', () => {
    let r = layoutRow(EMPTY_ORDER, e({ a: 0.5, b: 0.4 }), P, 0.5);
    r = layoutRow(r.state, e({ a: 0.3, b: 0.6 }), P, 0.5);
    // Rendering the same tick again at a new width must not count as a second tick.
    const again = placeRow(r.state, e({ a: 0.3, b: 0.6 }), { ...P, rowWidth: 500 }, 0.5);
    expect(again.cards.map((c) => c.id)).toEqual(['a', 'b']);
    expect(again.state.streaks).toEqual(r.state.streaks);
    expect(again.cards.at(-1)!.x + again.cards.at(-1)!.width).toBeCloseTo(500, 6);
    const next = layoutRow(again.state, e({ a: 0.3, b: 0.6 }), P, 0.5);
    expect(next.cards.map((c) => c.id)).toEqual(['b', 'a']);
  });
});
