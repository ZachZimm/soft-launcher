import type { BridgeStatus, CoreError } from '../lib/types';
import { errorCode } from './format';

// Which runtime error, if any, the status strip shows, and how often the passive
// "the model was slow" warning may appear.
//
// The runtime keeps a short error history for diagnostics. The strip is not that
// history: it shows only a problem that is still current.
//
// - Decision and model errors are resolved by any later successful decision or
//   readiness. A timeout the model recovered from leaves the strip.
// - Bridge errors are resolved once the logging bridge reconnects.
// - Execution errors are already shown in the outcome panel.
// - While the action row shows a blocking model notice (with Retry), the strip
//   does not repeat that error.
// - A slow-response warning (a decision or model timeout) is passive: actions
//   recover by themselves and installed apps keep working. Each profile sees at
//   most SLOW_WARNING_LIMIT distinct ones, counted per error, not per render or
//   per open, and remembered across restarts in localStorage. After that,
//   timeouts stay out of the strip; the blocking notice, status text and
//   runtime diagnostics are unchanged.

export const SLOW_WARNING_LIMIT = 2;
const KEY = 'soft-launcher.notices.v1';

export interface NoticeLedger {
  /** Keys of the distinct slow-response warnings already shown, oldest first. */
  slowShown: string[];
}

export const EMPTY_LEDGER: Readonly<NoticeLedger> = Object.freeze({ slowShown: [] });

export function isSlowWarning(error: CoreError): boolean {
  return (error.source === 'decision' || error.source === 'model') && errorCode(error) === 'timeout';
}

/** Identity of one error occurrence. The runtime never mutates an entry, so source, code and time identify it. */
export function errorKey(error: CoreError): string {
  return `${error.source}:${errorCode(error)}:${error.at}`;
}

export function loadLedger(storage: Pick<Storage, 'getItem'> | undefined = globalThis.localStorage): NoticeLedger {
  try {
    const raw = JSON.parse(storage?.getItem(KEY) ?? '{}') as { slowShown?: unknown };
    const shown = Array.isArray(raw.slowShown) ? raw.slowShown.filter((k): k is string => typeof k === 'string').slice(0, SLOW_WARNING_LIMIT) : [];
    return { slowShown: shown };
  } catch {
    return { slowShown: [] };
  }
}

export function saveLedger(ledger: NoticeLedger, storage: Pick<Storage, 'setItem'> | undefined = globalThis.localStorage): void {
  try {
    storage?.setItem(KEY, JSON.stringify(ledger));
  } catch {
    // Private mode or quota: the warning may appear again next session.
  }
}

/** May this slow warning be shown? Yes if it already counted, or the budget has room. */
export function slowAllowed(ledger: NoticeLedger, error: CoreError): boolean {
  return ledger.slowShown.includes(errorKey(error)) || ledger.slowShown.length < SLOW_WARNING_LIMIT;
}

/** Count a slow warning that is now on screen. Returns the same ledger if nothing changed. */
export function recordSlow(ledger: NoticeLedger, error: CoreError): NoticeLedger {
  const key = errorKey(error);
  if (ledger.slowShown.includes(key) || ledger.slowShown.length >= SLOW_WARNING_LIMIT) return ledger;
  return { slowShown: [...ledger.slowShown, key] };
}

export interface StripContext {
  /** Errors at or before this time were hidden by the user or belong to an earlier open. */
  hiddenBefore: number;
  /** Time of the last successful decision or readiness; earlier model errors are resolved. */
  resolvedBefore: number;
  bridge: BridgeStatus | null;
  /** The action row already shows a blocking model notice. */
  blockingNotice: boolean;
  ledger: NoticeLedger;
}

function current(e: CoreError, ctx: StripContext): boolean {
  if (e.at <= ctx.hiddenBefore) return false;
  if (e.source === 'execution') return false;
  if (e.source === 'decision' || e.source === 'model') {
    if (e.at <= ctx.resolvedBefore || ctx.blockingNotice) return false;
    if (isSlowWarning(e) && !slowAllowed(ctx.ledger, e)) return false;
    return true;
  }
  if (e.source === 'bridge') return ctx.bridge?.state !== 'connected';
  return true;
}

/** The newest still-current error for the strip, and how many current errors there are. */
export function stripErrors(errors: readonly CoreError[], ctx: StripContext): { error: CoreError | null; count: number } {
  const live = errors.filter((e) => current(e, ctx));
  return { error: live.at(-1) ?? null, count: live.length };
}
