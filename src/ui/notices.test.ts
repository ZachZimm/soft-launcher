import { describe, expect, it } from 'vitest';
import type { CoreError } from '../lib/types';
import { EMPTY_LEDGER, SLOW_WARNING_LIMIT, errorKey, isSlowWarning, loadLedger, recordSlow, saveLedger, slowAllowed, stripErrors, type StripContext } from './notices';

const timeout = (at: number, timeoutMs = 60_000): CoreError => ({ source: 'decision', message: 'Decision timed out', at, code: 'timeout', retryable: true, timeoutMs });
const network = (at: number): CoreError => ({ source: 'decision', message: 'Failed to fetch', at, code: 'network', retryable: true });
const ctx = (over: Partial<StripContext> = {}): StripContext => ({
  hiddenBefore: 0,
  resolvedBefore: 0,
  bridge: { state: 'connected', pending: 0 },
  blockingNotice: false,
  ledger: EMPTY_LEDGER,
  ...over,
});

function memoryStorage() {
  const data = new Map<string, string>();
  return { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v), data };
}

describe('slow-response warning budget', () => {
  it('treats decision and model timeouts as slow warnings, not other failures', () => {
    expect(isSlowWarning(timeout(1))).toBe(true);
    expect(isSlowWarning({ ...timeout(1), source: 'model' })).toBe(true);
    expect(isSlowWarning({ source: 'decision', message: 'The operation was aborted', at: 1 })).toBe(true);
    expect(isSlowWarning(network(1))).toBe(false);
    expect(isSlowWarning({ source: 'bridge', message: 'timed out', at: 1, code: 'timeout' })).toBe(false);
  });

  it('counts distinct errors, not renders or reopens', () => {
    const first = timeout(100);
    let ledger = recordSlow(EMPTY_LEDGER, first);
    // The same occurrence rendered again, or seen after a reopen, does not count again.
    expect(recordSlow(ledger, first)).toBe(ledger);
    expect(recordSlow(ledger, { ...first })).toBe(ledger);
    ledger = recordSlow(ledger, timeout(200));
    expect(ledger.slowShown).toEqual([errorKey(first), errorKey(timeout(200))]);
  });

  it(`allows at most ${SLOW_WARNING_LIMIT} distinct appearances, but keeps showing one that already counted`, () => {
    const a = timeout(100);
    const b = timeout(200);
    const c = timeout(300);
    let ledger = recordSlow(recordSlow(EMPTY_LEDGER, a), b);
    expect(slowAllowed(ledger, c)).toBe(false);
    expect(recordSlow(ledger, c)).toBe(ledger);
    expect(slowAllowed(ledger, b)).toBe(true);
    expect(stripErrors([c], ctx({ ledger })).error).toBeNull();
    expect(stripErrors([b], ctx({ ledger })).error).toEqual(b);
  });

  it('persists the count across restarts and survives corrupt storage', () => {
    const storage = memoryStorage();
    saveLedger(recordSlow(recordSlow(EMPTY_LEDGER, timeout(1)), timeout(2)), storage);
    const restored = loadLedger(storage);
    expect(restored.slowShown).toHaveLength(2);
    expect(slowAllowed(restored, timeout(3))).toBe(false);
    storage.setItem('soft-launcher.notices.v1', '{nope');
    expect(loadLedger(storage)).toEqual({ slowShown: [] });
    storage.setItem('soft-launcher.notices.v1', JSON.stringify({ slowShown: ['a', 7, 'b', 'c'] }));
    expect(loadLedger(storage).slowShown).toEqual(['a', 'b']);
  });

  it('never suppresses other real errors once the slow budget is spent', () => {
    const ledger = recordSlow(recordSlow(EMPTY_LEDGER, timeout(1)), timeout(2));
    const n = network(50);
    expect(stripErrors([timeout(40), n], ctx({ ledger })).error).toEqual(n);
    const http: CoreError = { source: 'decision', message: 'HTTP 500', at: 60, code: 'http', httpStatus: 500 };
    expect(stripErrors([http, timeout(70)], ctx({ ledger }))).toEqual({ error: http, count: 1 });
    const apps: CoreError = { source: 'apps', message: 'index failed', at: 80 };
    expect(stripErrors([apps], ctx({ ledger })).error).toEqual(apps);
  });
});

describe('status strip shows only current problems', () => {
  it('drops a timeout once a later decision or readiness succeeds', () => {
    const t = timeout(100);
    expect(stripErrors([t], ctx()).error).toEqual(t);
    expect(stripErrors([t], ctx({ resolvedBefore: 150 }))).toEqual({ error: null, count: 0 });
    // A failure after the last success is still current.
    const later = network(200);
    expect(stripErrors([t, later], ctx({ resolvedBefore: 150 }))).toEqual({ error: later, count: 1 });
  });

  it('does not repeat an error the blocking notice already shows', () => {
    expect(stripErrors([timeout(100)], ctx({ blockingNotice: true })).error).toBeNull();
  });

  it('leaves execution errors to the outcome panel', () => {
    expect(stripErrors([{ source: 'execution', message: 'Could not open Brave', at: 5 }], ctx()).error).toBeNull();
  });

  it('drops bridge errors once logging reconnects', () => {
    const e: CoreError = { source: 'bridge', message: 'Failed to fetch', at: 5, code: 'network' };
    expect(stripErrors([e], ctx({ bridge: { state: 'error', pending: 2 } })).error).toEqual(e);
    expect(stripErrors([e], ctx()).error).toBeNull();
  });

  it('hides errors from before the user dismissed them or reopened the launcher', () => {
    const e: CoreError = { source: 'window', message: 'placement failed', at: 5 };
    expect(stripErrors([e], ctx({ hiddenBefore: 5 })).error).toBeNull();
    expect(stripErrors([e], ctx({ hiddenBefore: 4 })).error).toEqual(e);
  });
});
