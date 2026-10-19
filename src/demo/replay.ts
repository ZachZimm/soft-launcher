import { EMPTY_ORDER, OTHER_OFFER_TICKS, layoutRow, nextOtherStreak, type LayoutParams, type OrderState, type RowLayout } from '../lib/layout';
import type { RecordedTick, Recording } from './recording';

export interface ReplayFrame {
  tick: number;
  input: string;
  row: RowLayout;
  other: number;
  otherStreak: number;
  offerTeach: boolean;
  /** The leading card is primed when it is the model's choice and its band is act. */
  primedId: string | null;
  confidence: number;
  source: RecordedTick;
}

/** Replay a recording through the layout engine. Pure: same input, same frames. */
export function replay(recording: Recording, params: LayoutParams): ReplayFrame[] {
  let state: OrderState = EMPTY_ORDER;
  let streak = 0;
  return recording.ticks.map((t, tick) => {
    const row = layoutRow(state, t.candidates, params, t.confidence);
    state = row.state;
    streak = nextOtherStreak(streak, t.other, t.candidates);
    const leader = row.cards[0];
    return {
      tick,
      input: t.input,
      row,
      other: t.other,
      otherStreak: streak,
      offerTeach: streak >= OTHER_OFFER_TICKS,
      primedId: leader && t.band === 'act' && t.choice === leader.id ? leader.id : null,
      confidence: t.confidence,
      source: t,
    };
  });
}

/** Stable digest of the visible layout, used to prove that replays are deterministic. */
export function digest(frames: readonly ReplayFrame[]): string {
  let h = 2166136261;
  const feed = (s: string) => {
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
  };
  for (const f of frames) {
    feed(`${f.tick}|${f.primedId ?? ''}|${f.offerTeach ? 1 : 0}|`);
    for (const c of f.row.cards) feed(`${c.id}:${c.x.toFixed(3)}:${c.width.toFixed(3)}:${c.opacity.toFixed(3)};`);
  }
  return h.toString(16).padStart(8, '0');
}
