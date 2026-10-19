import { describe, expect, it } from 'vitest';
import {
  HOLD_MS,
  INITIAL,
  appKey,
  enterDown,
  enterUp,
  escape,
  executed,
  holdElapsed,
  inputChanged,
  pointerDown,
  pointerUp,
  refresh,
  row,
  tab,
  target,
  type Effect,
  type InteractionState,
  type View,
} from './interaction';

const view = (over: Partial<View> = {}): View => ({ ready: true, order: ['a', 'b', 'c'], leaderId: 'a', primedId: null, ...over });
const primed = view({ primedId: 'a' });
const exec = (fx: Effect[]) => fx.filter((e) => e.type === 'execute');

describe('Enter on the primed leader', () => {
  it('fires immediately as primed with no hold', () => {
    const [s, fx] = enterDown(INITIAL, primed, 0, false);
    expect(fx).toEqual([{ type: 'execute', actionId: 'a', primed: true, holdMs: 0, confirmed: false }]);
    expect(s.busy).toBe(true);
  });

  it('ignores key repeat and further presses while busy', () => {
    const [s] = enterDown(INITIAL, primed, 0, false);
    expect(enterDown(s, primed, 30, true)[1]).toEqual([]);
    const [s2] = enterUp(s, 40);
    expect(exec(enterDown(s2, primed, 50, false)[1])).toEqual([]);
  });
});

describe('Enter on a non-primed card', () => {
  it('requires a 150 ms hold', () => {
    const [s, fx] = enterDown(INITIAL, view(), 1000, false);
    expect(fx).toEqual([{ type: 'scheduleHold', actionId: 'a', delayMs: HOLD_MS }]);
    expect(exec(fx)).toEqual([]);
    const [s2, fx2] = holdElapsed(s, 'a', 1000 + HOLD_MS);
    expect(fx2).toEqual([{ type: 'execute', actionId: 'a', primed: false, holdMs: HOLD_MS, confirmed: false }]);
    expect(s2.busy).toBe(true);
  });

  it('cancels when released early and explains why', () => {
    const [s] = enterDown(INITIAL, view(), 0, false);
    const [s2] = enterUp(s, 80);
    expect(s2.hold).toBeNull();
    expect(s2.hint).toMatch(/hold/i);
    expect(holdElapsed(s2, 'a', HOLD_MS)[1]).toEqual([]);
  });

  it('reschedules if the timer fires early', () => {
    const [s] = enterDown(INITIAL, view(), 0, false);
    expect(holdElapsed(s, 'a', 100)[1]).toEqual([{ type: 'scheduleHold', actionId: 'a', delayMs: 50 }]);
  });

  it('does not prime a selected non-leader even when the leader is primed', () => {
    const [sel] = tab(INITIAL, primed, 1);
    expect(sel.selectedId).toBe('b');
    const [, fx] = enterDown(sel, primed, 0, false);
    expect(fx).toEqual([{ type: 'scheduleHold', actionId: 'b', delayMs: HOLD_MS }]);
  });
});

describe('confirmation actions', () => {
  function requestConfirmation(): InteractionState {
    let [s] = enterDown(INITIAL, view(), 0, false);
    [s] = holdElapsed(s, 'a', HOLD_MS);
    [s] = executed(s, { status: 'confirmationRequired', actionId: 'a', message: 'Run ls?', expiresInMs: 15_000 }, 200);
    return s;
  }

  it('is not armed while the first Enter is still held', () => {
    const s = requestConfirmation();
    expect(s.confirm).toMatchObject({ actionId: 'a', armed: false });
    expect(exec(enterDown(s, view(), 300, true)[1])).toEqual([]);
    expect(exec(enterDown(s, view(), 300, false)[1])).toEqual([]);
  });

  it('needs an explicit second Enter press', () => {
    let [s] = enterUp(requestConfirmation(), 400);
    expect(s.confirm?.armed).toBe(true);
    const [s2, fx] = enterDown(s, view(), 500, false);
    expect(fx).toEqual([{ type: 'execute', actionId: 'a', primed: false, holdMs: 0, confirmed: true }]);
    [s] = executed(s2, { status: 'executed', actionId: 'a', message: 'done' }, 600);
    expect(s.confirm).toBeNull();
  });

  it('arms immediately when the reply arrives after the key was released', () => {
    let [s] = enterDown(INITIAL, view(), 0, false);
    [s] = holdElapsed(s, 'a', HOLD_MS);
    [s] = enterUp(s, 160);
    [s] = executed(s, { status: 'confirmationRequired', actionId: 'a', message: 'Run?', expiresInMs: 15_000 }, 300);
    expect(s.confirm?.armed).toBe(true);
  });

  it('is cleared by input changes, selection changes and Escape', () => {
    const [armed] = enterUp(requestConfirmation(), 400);
    for (const [s, fx] of [inputChanged(armed), tab(armed, view(), 1), escape(armed, { outcomeVisible: false })]) {
      expect(s.confirm).toBeNull();
      expect(fx).toContainEqual({ type: 'cancelConfirmation' });
      expect(exec(enterDown(enterUp(s, 500)[0], view(), 600, false)[1]).some((e) => e.type === 'execute' && e.confirmed)).toBe(false);
    }
  });

  it('cannot be cancelled by Escape once the confirmed send is in flight', () => {
    const [armed] = enterUp(requestConfirmation(), 400);
    const [sending] = enterDown(armed, view(), 500, false);
    const [s, fx] = escape(sending, { outcomeVisible: false });
    expect(s).toEqual(sending);
    expect(fx).toEqual([]);
  });

  it('expires instead of confirming a stale token', () => {
    const [armed] = enterUp(requestConfirmation(), 400);
    const [s, fx] = enterDown(armed, view(), 200 + 15_000, false);
    expect(s.confirm).toBeNull();
    expect(fx).toEqual([{ type: 'cancelConfirmation' }]);
    expect(refresh(armed, view(), 200 + 15_001)[0].confirm).toBeNull();
  });
});

describe('Tab selection', () => {
  it('cycles forwards and backwards through visible cards', () => {
    let [s] = tab(INITIAL, view(), 1);
    expect(s.selectedId).toBe('b');
    [s] = tab(s, view(), 1);
    [s] = tab(s, view(), 1);
    expect(s.selectedId).toBe('a');
    [s] = tab(s, view(), -1);
    expect(s.selectedId).toBe('c');
  });

  it('follows the selected card by id when the row reorders', () => {
    const [s] = tab(INITIAL, view(), 1);
    expect(target(s, view({ order: ['b', 'a', 'c'], leaderId: 'b' }))).toBe('b');
  });

  it('falls back to the leader when the selected card disappears', () => {
    const [s] = tab(INITIAL, view(), 1);
    const [s2] = refresh(s, view({ order: ['a', 'c'] }), 10);
    expect(s2.selectedId).toBeNull();
  });

  it('is reset by typing', () => {
    const [s] = tab(INITIAL, view(), 1);
    expect(inputChanged(s)[0].selectedId).toBeNull();
  });
});

describe('Enter before the decision lands', () => {
  it('only explains itself and never executes later, held or released', () => {
    const [s, fx] = enterDown(INITIAL, view({ ready: false }), 0, false);
    expect(fx).toEqual([]);
    expect(s.hint).toMatch(/press Enter again/);
    // Key still held when a primed decision arrives: nothing fires.
    expect(refresh(s, primed, 100)[1]).toEqual([]);
    // Released while pending, then the primed decision arrives: nothing fires.
    const [released] = enterUp(s, 50);
    const [after, fx2] = refresh(released, primed, 120);
    expect(fx2).toEqual([]);
    expect(holdElapsed(after, 'a', 400)[1]).toEqual([]);
    // A fresh press on the now-visible primed card does fire.
    expect(exec(enterDown(after, primed, 500, false)[1])).toHaveLength(1);
  });
});

describe('pointer', () => {
  it('selects on press and runs after a 150 ms hold', () => {
    const [s, fx] = pointerDown(INITIAL, view(), 'c', 0);
    expect(s.selectedId).toBe('c');
    expect(fx).toEqual([{ type: 'scheduleHold', actionId: 'c', delayMs: HOLD_MS }]);
    expect(exec(holdElapsed(s, 'c', HOLD_MS)[1])).toHaveLength(1);
  });

  it('a click only selects', () => {
    const [s] = pointerDown(INITIAL, view(), 'c', 0);
    const [s2] = pointerUp(s, 40);
    expect(s2.selectedId).toBe('c');
    expect(holdElapsed(s2, 'c', HOLD_MS)[1]).toEqual([]);
  });
});

describe('Escape', () => {
  it('steps back one level at a time and finally dismisses', () => {
    let [s] = tab(INITIAL, view(), 1);
    s = { ...s, hint: 'x' };
    let fx: Effect[];
    [s, fx] = escape(s, { outcomeVisible: true });
    expect(fx).toEqual([{ type: 'dismissOutcome' }]);
    [s, fx] = escape(s, { outcomeVisible: false });
    expect(s.hint).toBeNull();
    [s, fx] = escape(s, { outcomeVisible: false });
    expect(s.selectedId).toBeNull();
    [s, fx] = escape(s, { outcomeVisible: false });
    expect(fx).toEqual([{ type: 'dismissLauncher' }]);
  });

  it('cancels a hold before anything else', () => {
    const [s] = enterDown(INITIAL, view(), 0, false);
    const [s2, fx] = escape(s, { outcomeVisible: true });
    expect(s2.hold).toBeNull();
    expect(fx).toEqual([]);
    expect(holdElapsed(s2, 'a', HOLD_MS)[1]).toEqual([]);
  });
});

describe('installed-app cards', () => {
  const apps = (over: Partial<View> = {}): View => view({ apps: ['brave-browser.desktop', 'brogue-ce.desktop'], ...over });
  const launches = (fx: Effect[]) => fx.filter((e) => e.type === 'launchApp');

  it('targets the first app by default, ahead of a primed action leader', () => {
    expect(target(INITIAL, apps({ primedId: 'a' }))).toBe(appKey('brave-browser.desktop'));
    expect(target(INITIAL, view())).toBe('a');
  });

  it('opens the default app on one Enter press, with no hold and before the model answers', () => {
    for (const v of [apps(), apps({ ready: false, leaderId: null, order: [] })]) {
      const [s, fx] = enterDown(INITIAL, v, 0, false);
      expect(fx).toEqual([{ type: 'launchApp', appId: 'brave-browser.desktop' }]);
      expect(fx.some((e) => e.type === 'scheduleHold' || e.type === 'execute')).toBe(false);
      expect(s.busy).toBe(true);
      // Releasing Enter does nothing more; holding is never required.
      expect(enterUp(s, 5)[1]).toEqual([]);
    }
  });

  it('never opens twice from key repeat or a second press while the first is running', () => {
    const [s] = enterDown(INITIAL, apps(), 0, false);
    expect(launches(enterDown(s, apps(), 30, true)[1])).toEqual([]);
    const [s2] = enterUp(s, 40);
    expect(launches(enterDown(s2, apps(), 50, false)[1])).toEqual([]);
    const [done] = executed(s2, { status: 'executed', actionId: 'open_app', message: 'ok' }, 60);
    expect(done.busy).toBe(false);
    expect(done.selectedId).toBeNull();
  });

  it('opens an app chosen with Tab or Arrow keys on one Enter', () => {
    const [s] = tab(INITIAL, apps(), 1);
    expect(s.selectedId).toBe(appKey('brogue-ce.desktop'));
    expect(enterDown(s, apps(), 0, false)[1]).toEqual([{ type: 'launchApp', appId: 'brogue-ce.desktop' }]);
    // Tab continues from the apps into the action cards, and wraps.
    const [s2] = tab(s, apps(), 1);
    expect(s2.selectedId).toBe('a');
    const [back] = tab(INITIAL, apps(), -1);
    expect(back.selectedId).toBe('c');
    // Arrow Down jumps to the action row, Arrow Up back to the first app.
    const [down] = row(INITIAL, apps(), 1);
    expect(down.selectedId).toBe('a');
    expect(row(down, apps(), -1)[0].selectedId).toBe(appKey('brave-browser.desktop'));
  });

  it('keeps the action-card rules when an action card is selected', () => {
    const [s] = row(INITIAL, apps(), 1);
    const [, fx] = enterDown(s, apps(), 0, false);
    expect(fx).toEqual([{ type: 'scheduleHold', actionId: 'a', delayMs: HOLD_MS }]);
  });

  it('opens on a click released on the same card, not when released elsewhere', () => {
    const key = appKey('brogue-ce.desktop');
    const [s, fx] = pointerDown(INITIAL, apps(), key, 0);
    expect(fx).toEqual([]);
    expect(s.selectedId).toBe(key);
    expect(pointerUp(s, 40, key)[1]).toEqual([{ type: 'launchApp', appId: 'brogue-ce.desktop' }]);
    const [away, fx2] = pointerUp(s, 40, null);
    expect(fx2).toEqual([]);
    expect(away.selectedId).toBe(key);
    // Escape during a press cancels it.
    expect(pointerUp(escape(s, { outcomeVisible: false })[0], 40, key)[1]).toEqual([]);
  });

  it('drops a selection whose app no longer matches and returns to the default', () => {
    const [s] = tab(INITIAL, apps(), 1);
    const narrowed = apps({ apps: ['brave-browser.desktop'] });
    const [r] = refresh(s, narrowed, 0);
    expect(r.selectedId).toBeNull();
    expect(target(r, narrowed)).toBe(appKey('brave-browser.desktop'));
  });

  it('does not launch from a stale pending confirmation', () => {
    const [s] = executed(INITIAL, { status: 'confirmationRequired', actionId: 'b', message: 'Confirm', expiresInMs: 15_000 }, 0);
    const [s2, fx] = tab(s, apps(), 1);
    expect(fx).toEqual([{ type: 'cancelConfirmation' }]);
    expect(s2.confirm).toBeNull();
  });
});

describe('cards that cannot run', () => {
  it('explain themselves on Enter instead of holding or executing', () => {
    const v = view({ blocked: { a: 'Open app can’t run: no matching installed application' } });
    const [s, fx] = enterDown(INITIAL, v, 0, false);
    expect(fx).toEqual([]);
    expect(s.hold).toBeNull();
    expect(s.hint).toMatch(/can’t run/);
  });
});
