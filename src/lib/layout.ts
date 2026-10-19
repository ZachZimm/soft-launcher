// Pure layout maths for the distribution row: card order with hysteresis, card
// widths by sqrt(p) or p with a minimum width, opacity, and "other" lead streaks.
// Nothing here touches the DOM, timers or the runtime, so the replay page and the
// live launcher produce identical layouts for identical inputs.

export type WidthMode = 'sqrt' | 'linear';

export interface LayoutParams {
  /** Width function: sqrt(p) keeps small options visible, linear is p with a floor. */
  widthMode: WidthMode;
  /** Total row width in px that cards and gaps must fill. */
  rowWidth: number;
  /** Gap between cards in px. */
  gap: number;
  /** Minimum card width in px, for legibility. */
  minWidth: number;
  /** Maximum number of cards in the row, before the width limit applies. */
  maxCards: number;
  /** Candidates below this probability are not shown as cards. */
  minProbability: number;
  /** A candidate must exceed its left neighbour by this margin to swap. */
  swapMargin: number;
  /** ...for this many consecutive ticks. */
  swapTicks: number;
  /** Transition duration in ms; carried here so every view eases identically. */
  easeMs: number;
}

export const DEFAULT_LAYOUT: Readonly<LayoutParams> = Object.freeze({
  widthMode: 'sqrt',
  rowWidth: 720,
  gap: 8,
  minWidth: 112,
  maxCards: 6,
  minProbability: 0.01,
  swapMargin: 0.08,
  swapTicks: 2,
  easeMs: 120,
});

/** Tunable bounds, shared by the launcher settings and the replay page. */
export const LAYOUT_LIMITS = Object.freeze({
  swapMargin: { min: 0, max: 0.3, step: 0.01 },
  swapTicks: { min: 1, max: 5, step: 1 },
  easeMs: { min: 0, max: 400, step: 10 },
  minWidth: { min: 48, max: 200, step: 4 },
  maxCards: { min: 2, max: 10, step: 1 },
});

export interface Entry {
  id: string;
  p: number;
}

/**
 * Display order carried between ticks. `streaks[challenger][incumbent]` counts
 * consecutive ticks in which the challenger, currently right of the incumbent,
 * exceeded it by more than the swap margin.
 */
export interface OrderState {
  order: string[];
  streaks: Record<string, Record<string, number>>;
}

export const EMPTY_ORDER: Readonly<OrderState> = Object.freeze({ order: [], streaks: {} });

const EPS = 1e-9;

function clamp(x: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, x));
}

function cleanP(p: number): number {
  return Number.isFinite(p) ? clamp(p, 0, 1) : 0;
}

/** Sort by probability descending; ties by id so the result never depends on input order. */
export function byProbability(entries: readonly Entry[]): Entry[] {
  return [...entries].sort((a, b) => cleanP(b.p) - cleanP(a.p) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

/**
 * Advance the display order by one tick.
 *
 * - Ids that disappear are dropped. New ids are inserted at the position their
 *   probability earns among the survivors (a new card has no position to defend).
 * - A surviving card moves left past a neighbour only once it has exceeded that
 *   neighbour by more than `swapMargin` for `swapTicks` consecutive ticks. A tick
 *   that fails the test resets that pair's streak, so alternating noise never swaps.
 * - A card may pass several neighbours in one tick when it has beaten each of them
 *   long enough, so a decisive shift does not crawl one slot per tick.
 */
export function stepOrder(prev: Readonly<OrderState>, entries: readonly Entry[], params: Pick<LayoutParams, 'swapMargin' | 'swapTicks'>): OrderState {
  const p = new Map<string, number>();
  for (const e of entries) p.set(e.id, cleanP(e.p));
  const margin = Math.max(0, params.swapMargin);
  const needed = Math.max(1, Math.round(params.swapTicks));

  const order = prev.order.filter((id) => p.has(id));
  const known = new Set(order);
  for (const e of byProbability(entries)) {
    if (known.has(e.id)) continue;
    const pe = p.get(e.id)!;
    let at = order.length;
    for (let i = 0; i < order.length; i++) {
      if (pe > p.get(order[i]!)!) {
        at = i;
        break;
      }
    }
    order.splice(at, 0, e.id);
    known.add(e.id);
  }

  // Update streaks for every challenger/incumbent pair in the current order.
  const streaks: Record<string, Record<string, number>> = {};
  for (let j = 1; j < order.length; j++) {
    const challenger = order[j]!;
    const pc = p.get(challenger)!;
    for (let i = 0; i < j; i++) {
      const incumbent = order[i]!;
      if (pc - p.get(incumbent)! > margin + EPS) {
        const before = prev.streaks[challenger]?.[incumbent] ?? 0;
        (streaks[challenger] ??= {})[incumbent] = before + 1;
      }
    }
  }

  const beats = (challenger: string, incumbent: string) => (streaks[challenger]?.[incumbent] ?? 0) >= needed;

  // Stable insertion: each card slides left past neighbours it has beaten.
  const next: string[] = [];
  for (const id of order) {
    let at = next.length;
    while (at > 0 && beats(id, next[at - 1]!)) at--;
    next.splice(at, 0, id);
  }

  // A completed swap spends its streak; the pair starts fresh from the new order.
  for (const [challenger, row] of Object.entries(streaks)) {
    for (const incumbent of Object.keys(row)) {
      if (next.indexOf(challenger) < next.indexOf(incumbent)) delete row[incumbent];
    }
    if (Object.keys(row).length === 0) delete streaks[challenger];
  }

  return { order: next, streaks };
}

/** Width weight for one probability. */
export function widthWeight(p: number, mode: WidthMode): number {
  const x = cleanP(p);
  return mode === 'sqrt' ? Math.sqrt(x) : x;
}

/** How many cards fit in the row at minimum width. */
export function capacity(params: Pick<LayoutParams, 'rowWidth' | 'gap' | 'minWidth' | 'maxCards'>): number {
  const fit = Math.floor((params.rowWidth + params.gap) / (params.minWidth + params.gap));
  return Math.max(1, Math.min(params.maxCards, fit));
}

/**
 * Distribute the row width across cards in proportion to their weights, with a
 * minimum width per card (water filling: cards that would fall under the floor are
 * pinned to it and the rest is shared by the others). Widths always sum to the
 * available width exactly.
 */
export function cardWidths(ps: readonly number[], params: Pick<LayoutParams, 'widthMode' | 'rowWidth' | 'gap' | 'minWidth'>): number[] {
  const n = ps.length;
  if (n === 0) return [];
  const available = Math.max(0, params.rowWidth - params.gap * (n - 1));
  const floor = Math.min(params.minWidth, available / n);
  const weights = ps.map((p) => widthWeight(p, params.widthMode));
  const widths = new Array<number>(n).fill(0);
  const free = new Set<number>(weights.map((_, i) => i));
  let remaining = available;

  for (;;) {
    let total = 0;
    for (const i of free) total += weights[i]!;
    let pinned = false;
    for (const i of free) {
      const share = total > EPS ? (remaining * weights[i]!) / total : remaining / free.size;
      if (share < floor - EPS) {
        widths[i] = floor;
        remaining -= floor;
        free.delete(i);
        pinned = true;
      }
    }
    if (!pinned) {
      for (const i of free) widths[i] = total > EPS ? (remaining * weights[i]!) / total : remaining / free.size;
      break;
    }
    if (free.size === 0) break;
  }
  return widths;
}

/** Opacity encodes the leading card's explicit model confidence; other cards stay constant. */
export const OTHER_CARD_OPACITY = 0.78;
export function leaderOpacity(confidence: number): number {
  return Number.isFinite(confidence) ? 0.35 + 0.65 * clamp(confidence, 0, 1) : 0.35;
}

export interface CardLayout {
  id: string;
  p: number;
  rank: number;
  x: number;
  width: number;
  opacity: number;
  leader: boolean;
}

export interface RowLayout {
  cards: CardLayout[];
  /** Candidates that exist but did not get a card (below threshold or over capacity). */
  hidden: Entry[];
  /** Next order state; pass back into the following tick. */
  state: OrderState;
}

/**
 * One tick of the row: advance the hysteretic order, choose visible cards in that
 * order, size and position them. `leaderConfidence` is the explicit model
 * confidence of the leading card, not its probability; pass a lookup when the
 * leader is not known until the order has been resolved.
 */
export type ConfidenceOf = number | ((id: string) => number);

export function layoutRow(prev: Readonly<OrderState>, entries: readonly Entry[], params: LayoutParams, leaderConfidence: ConfidenceOf): RowLayout {
  const state = stepOrder(prev, eligibleEntries(entries, params), params);
  return placeRow(state, entries, params, leaderConfidence);
}

function eligibleEntries(entries: readonly Entry[], params: Pick<LayoutParams, 'minProbability'>): Entry[] {
  return entries.filter((e) => cleanP(e.p) >= params.minProbability);
}

/**
 * Size and position cards in an already advanced order without touching streaks.
 * Use this when only the geometry changed (row resize, width mode, band refresh),
 * so re-rendering never counts as an extra hysteresis tick. Ids missing from the
 * order (possible after a parameter change) are appended by probability.
 */
export function placeRow(state: Readonly<OrderState>, entries: readonly Entry[], params: LayoutParams, leaderConfidence: ConfidenceOf): RowLayout {
  const eligible = eligibleEntries(entries, params);
  const p = new Map(eligible.map((e) => [e.id, cleanP(e.p)]));
  const ordered = state.order.filter((id) => p.has(id));
  const seen = new Set(ordered);
  for (const e of byProbability(eligible)) if (!seen.has(e.id)) ordered.push(e.id);
  const next: OrderState = { order: ordered, streaks: state.streaks };
  const visibleIds = ordered.slice(0, capacity(params));
  const widths = cardWidths(visibleIds.map((id) => p.get(id)!), params);
  let x = 0;
  const cards = visibleIds.map((id, rank) => {
    const card: CardLayout = {
      id,
      p: p.get(id)!,
      rank,
      x,
      width: widths[rank]!,
      opacity: rank === 0 ? leaderOpacity(typeof leaderConfidence === 'function' ? leaderConfidence(id) : leaderConfidence) : OTHER_CARD_OPACITY,
      leader: rank === 0,
    };
    x += widths[rank]! + params.gap;
    return card;
  });
  const shown = new Set(visibleIds);
  const hidden = byProbability(entries.filter((e) => !shown.has(e.id)));
  return { cards, hidden, state: next };
}

/**
 * Count of consecutive ticks in which `other` held the most mass of all actions.
 * The launcher offers "teach a new action" once this reaches three.
 */
export const OTHER_OFFER_TICKS = 3;
export function nextOtherStreak(streak: number, other: number, entries: readonly Entry[]): number {
  const best = entries.reduce((m, e) => Math.max(m, cleanP(e.p)), 0);
  return cleanP(other) > best + EPS ? streak + 1 : 0;
}

/** Count adjacent order changes across a sequence of display orders. */
export function countReorders(orders: readonly (readonly string[])[]): number {
  let n = 0;
  for (let t = 1; t < orders.length; t++) if (!sameOrder(common(orders[t - 1]!, orders[t]!), common(orders[t]!, orders[t - 1]!))) n++;
  return n;
}

/**
 * Flicker: a pair of cards whose relative order flips and then flips back within
 * `window` ticks. This is the jitter the hysteresis exists to remove.
 */
export function countFlicker(orders: readonly (readonly string[])[], window = 3): number {
  let flicker = 0;
  const lastFlip = new Map<string, { t: number; dir: boolean }>();
  for (let t = 1; t < orders.length; t++) {
    const before = orders[t - 1]!;
    const after = orders[t]!;
    const posB = new Map(before.map((id, i) => [id, i]));
    const posA = new Map(after.map((id, i) => [id, i]));
    const ids = before.filter((id) => posA.has(id)).sort();
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = ids[i]!;
        const b = ids[j]!;
        const wasAB = posB.get(a)! < posB.get(b)!;
        const isAB = posA.get(a)! < posA.get(b)!;
        if (wasAB === isAB) continue;
        const key = `${a}\u0000${b}`;
        const last = lastFlip.get(key);
        if (last && last.dir !== isAB && t - last.t <= window) flicker++;
        lastFlip.set(key, { t, dir: isAB });
      }
    }
  }
  return flicker;
}

function common(a: readonly string[], b: readonly string[]): string[] {
  const s = new Set(b);
  return a.filter((id) => s.has(id));
}

function sameOrder(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i]);
}
