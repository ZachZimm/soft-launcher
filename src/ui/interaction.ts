// Keyboard and pointer interaction for the distribution row, as a pure state
// machine. The component feeds it events with timestamps and performs the
// returned effects (execute, schedule a hold timer, drop a native confirmation),
// which keeps the safety rules testable without a DOM:
//
// - Installed-app cards come first. The first one is the default target, and
//   one Enter press (or a click) opens the targeted app: no hold, no model
//   readiness or confidence involved. It is a code match the user can see.
// - Enter on the primed leader fires at once.
// - Enter on any other action card fires only after a deliberate 150 ms hold.
// - Enter on a card whose arguments are invalid explains why and runs nothing.
// - Actions that need confirmation fire a first request that returns a token;
//   only a *separate* Enter press (after the first is released) confirms it.
//   Key repeat never counts.
// - Any input or selection change drops a pending confirmation.
// - Enter while a decision is pending does nothing but explain itself: the user
//   must press again once the result is visible. Nothing fires later on its own.
// - Escape steps back one level: hold, confirmation, outcome, hint, selection,
//   and finally dismisses the launcher.

export const HOLD_MS = 150;
/** Selection IDs of installed-app cards; action IDs never contain a colon. */
export const APP_PREFIX = 'app:';
export const appKey = (appId: string): string => APP_PREFIX + appId;
export const appIdOf = (key: string | null): string | null => (key?.startsWith(APP_PREFIX) ? key.slice(APP_PREFIX.length) : null);

export interface View {
  /** True when the runtime has a current, nonstale decision that execute will accept. */
  ready: boolean;
  /** Visible card ids in display order. */
  order: readonly string[];
  /** The leftmost card. */
  leaderId: string | null;
  /** The leader, if it is primed (model's top choice in the act band, no confirmation needed). */
  primedId: string | null;
  /** Installed-app IDs in display order, shown before the action cards. */
  apps?: readonly string[];
  /** Action cards that cannot run, with the sentence to show instead. */
  blocked?: Readonly<Record<string, string>>;
}

/** Every selectable card in Tab order: apps first, then actions. */
export function selectable(v: View): string[] {
  return [...(v.apps ?? []).map(appKey), ...v.order];
}

export interface Hold {
  actionId: string;
  startedAt: number;
  source: 'key' | 'pointer';
}

export interface PendingConfirm {
  actionId: string;
  message: string;
  expiresAt: number;
  /** Becomes true once the Enter that requested confirmation is released. */
  armed: boolean;
}

/** A pointer press on an app card; releasing it on the same card opens the app. */
export interface AppPress {
  key: string;
}

export interface InteractionState {
  selectedId: string | null;
  enterDown: boolean;
  pointerDown: boolean;
  hold: Hold | null;
  confirm: PendingConfirm | null;
  busy: boolean;
  hint: string | null;
  appPress?: AppPress | null;
}

export type Effect =
  | { type: 'execute'; actionId: string; primed: boolean; holdMs: number; confirmed: boolean }
  | { type: 'launchApp'; appId: string }
  | { type: 'scheduleHold'; actionId: string; delayMs: number }
  | { type: 'cancelConfirmation' }
  | { type: 'dismissOutcome' }
  | { type: 'dismissLauncher' };

export type Step = [InteractionState, Effect[]];

export const INITIAL: Readonly<InteractionState> = Object.freeze({
  selectedId: null,
  enterDown: false,
  pointerDown: false,
  hold: null,
  confirm: null,
  busy: false,
  hint: null,
  appPress: null,
});

/** The card that Enter acts on: an explicit, still visible selection, else the first app, else the leader. */
export function target(s: Readonly<InteractionState>, v: View): string | null {
  if (s.selectedId && selectable(v).includes(s.selectedId)) return s.selectedId;
  const firstApp = v.apps?.[0];
  return firstApp !== undefined ? appKey(firstApp) : v.leaderId;
}

function launch(s: InteractionState, key: string): Step {
  return [{ ...s, busy: true, hold: null, appPress: null, hint: null }, [{ type: 'launchApp', appId: appIdOf(key)! }]];
}

function dropConfirm(s: InteractionState): Step {
  return s.confirm ? [{ ...s, confirm: null }, [{ type: 'cancelConfirmation' }]] : [s, []];
}

function fire(s: InteractionState, v: View, now: number): Step {
  const t = target(s, v);
  if (!t) return [{ ...s, hint: 'Nothing to run yet' }, []];
  if (appIdOf(t) !== null) return launch(s, t);
  const blocked = v.blocked?.[t];
  if (blocked) return [{ ...s, hold: null, hint: blocked }, []];
  if (t === v.primedId && t === v.leaderId) {
    return [{ ...s, busy: true, hold: null, hint: null }, [{ type: 'execute', actionId: t, primed: true, holdMs: 0, confirmed: false }]];
  }
  return [{ ...s, hold: { actionId: t, startedAt: now, source: 'key' }, hint: null }, [{ type: 'scheduleHold', actionId: t, delayMs: HOLD_MS }]];
}

export function enterDown(state: Readonly<InteractionState>, v: View, now: number, repeat: boolean): Step {
  if (repeat) return [{ ...state }, []];
  const s: InteractionState = { ...state, enterDown: true };
  if (s.confirm) {
    if (!s.confirm.armed || s.busy) return [s, []];
    if (now >= s.confirm.expiresAt) {
      const [d, fx] = dropConfirm(s);
      return [{ ...d, hint: 'Confirmation expired; press Enter to request it again' }, fx];
    }
    return [{ ...s, busy: true, hint: null }, [{ type: 'execute', actionId: s.confirm.actionId, primed: false, holdMs: 0, confirmed: true }]];
  }
  if (s.busy) return [{ ...s, hint: 'Still running the last action' }, []];
  // Apps are local code matches: they never wait for the model.
  if (appIdOf(target(s, v)) !== null) return fire(s, v, now);
  if (!v.ready) return [{ ...s, hint: 'Waiting for the current decision; press Enter again when it appears' }, []];
  return fire(s, v, now);
}

export function enterUp(state: Readonly<InteractionState>, now: number): Step {
  const s: InteractionState = { ...state, enterDown: false };
  if (s.confirm && !s.confirm.armed) s.confirm = { ...s.confirm, armed: true };
  if (s.hold?.source === 'key') {
    if (now - s.hold.startedAt < HOLD_MS) return [{ ...s, hold: null, hint: 'Hold Enter a moment longer to run this action' }, []];
    return [{ ...s, hold: null }, []];
  }
  return [s, []];
}

/** The hold timer fired. Executes if the same card is still being held long enough. */
export function holdElapsed(state: Readonly<InteractionState>, actionId: string, now: number): Step {
  const h = state.hold;
  if (!h || h.actionId !== actionId || state.busy) return [{ ...state }, []];
  const down = h.source === 'key' ? state.enterDown : state.pointerDown;
  if (!down) return [{ ...state, hold: null }, []];
  const held = now - h.startedAt;
  if (held < HOLD_MS) return [{ ...state }, [{ type: 'scheduleHold', actionId, delayMs: HOLD_MS - held }]];
  return [{ ...state, hold: null, busy: true, hint: null }, [{ type: 'execute', actionId, primed: false, holdMs: held, confirmed: false }]];
}

export function pointerDown(state: Readonly<InteractionState>, v: View, id: string, now: number): Step {
  if (appIdOf(id) !== null) {
    const [d, fx] = state.confirm ? dropConfirm({ ...state }) : [{ ...state }, [] as Effect[]];
    const s: InteractionState = { ...d, selectedId: id, pointerDown: true, hold: null, hint: null };
    if (s.busy || !selectable(v).includes(id)) return [s, fx];
    return [{ ...s, appPress: { key: id } }, fx];
  }
  let [s, fx] = state.selectedId === id || !state.confirm ? [{ ...state }, [] as Effect[]] : dropConfirm({ ...state });
  s = { ...s, selectedId: id, pointerDown: true, hint: null };
  if (s.busy || s.confirm || !v.ready || !v.order.includes(id)) return [s, fx];
  return [{ ...s, hold: { actionId: id, startedAt: now, source: 'pointer' } }, [...fx, { type: 'scheduleHold', actionId: id, delayMs: HOLD_MS }]];
}

/**
 * The pointer was released. `releasedOn` is the card under the pointer at release
 * (null when outside every card); releasing a pressed app card on itself opens it.
 */
export function pointerUp(state: Readonly<InteractionState>, now: number, releasedOn: string | null = null): Step {
  const s: InteractionState = { ...state, pointerDown: false };
  if (s.appPress) {
    const key = s.appPress.key;
    s.appPress = null;
    return key === releasedOn && !s.busy ? launch(s, key) : [s, []];
  }
  if (s.hold?.source === 'pointer') {
    const short = now - s.hold.startedAt < HOLD_MS;
    return [{ ...s, hold: null, hint: short ? 'Selected. Press and hold, or hold Enter, to run it' : s.hint }, []];
  }
  return [s, []];
}

export function tab(state: Readonly<InteractionState>, v: View, dir: 1 | -1): Step {
  const all = selectable(v);
  if (all.length === 0) return [{ ...state }, []];
  const current = target(state, v);
  const i = current ? all.indexOf(current) : -1;
  const n = all.length;
  const next = all[(((i < 0 ? (dir > 0 ? -1 : 0) : i) + dir) % n + n) % n]!;
  const [s, fx] = dropConfirm({ ...state, hold: null, appPress: null, hint: null });
  return [{ ...s, selectedId: next }, fx];
}

/** Arrow Down/Up: jump between the app row and the action row (first card of the other row). */
export function row(state: Readonly<InteractionState>, v: View, dir: 1 | -1): Step {
  const apps = (v.apps ?? []).map(appKey);
  const current = target(state, v);
  const onApps = appIdOf(current) !== null;
  const next = dir > 0 ? (onApps ? v.order[0] : undefined) : !onApps ? apps[0] : undefined;
  if (!next || next === current) return [{ ...state }, []];
  const [s, fx] = dropConfirm({ ...state, hold: null, appPress: null, hint: null });
  return [{ ...s, selectedId: next }, fx];
}

export function inputChanged(state: Readonly<InteractionState>): Step {
  return dropConfirm({ ...state, selectedId: null, hold: null, appPress: null, hint: null });
}

/** A new runtime view arrived: drop vanished selections and expire confirmations. Never executes. */
export function refresh(state: Readonly<InteractionState>, v: View, now: number): Step {
  let s: InteractionState = { ...state };
  let fx: Effect[] = [];
  if (s.selectedId && !selectable(v).includes(s.selectedId) && !s.confirm) s.selectedId = null;
  if (s.confirm && now >= s.confirm.expiresAt && !s.busy) {
    [s, fx] = dropConfirm(s);
    s.hint = 'Confirmation expired; press Enter to request it again';
  }
  return [s, fx];
}

export interface ExecutionReply {
  status: 'executed' | 'confirmationRequired' | 'error';
  actionId: string;
  message: string;
  expiresInMs?: number;
}

export function executed(state: Readonly<InteractionState>, reply: ExecutionReply, now: number): Step {
  const s: InteractionState = { ...state, busy: false, hold: null };
  if (reply.status === 'confirmationRequired') {
    return [{ ...s, hint: null, confirm: { actionId: reply.actionId, message: reply.message, expiresAt: now + (reply.expiresInMs ?? 15_000), armed: !s.enterDown && !s.pointerDown } }, []];
  }
  return [{ ...s, confirm: null, selectedId: reply.status === 'executed' ? null : s.selectedId }, []];
}

export interface EscapeContext {
  outcomeVisible: boolean;
}

export function escape(state: Readonly<InteractionState>, ctx: EscapeContext): Step {
  const s: InteractionState = { ...state };
  if (s.hold || s.appPress) return [{ ...s, hold: null, appPress: null, hint: null }, []];
  // The second Enter already sent it; nothing can be cancelled, so don't claim to.
  if (s.confirm && s.busy) return [s, []];
  if (s.confirm) {
    const [d, fx] = dropConfirm(s);
    return [{ ...d, hint: 'Cancelled' }, fx];
  }
  if (ctx.outcomeVisible) return [s, [{ type: 'dismissOutcome' }]];
  if (s.hint) return [{ ...s, hint: null }, []];
  if (s.selectedId) return [{ ...s, selectedId: null }, []];
  return [s, [{ type: 'dismissLauncher' }]];
}
