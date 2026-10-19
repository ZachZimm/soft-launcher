import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RingLog } from 'soft-if';
import type { WireRequest } from 'soft-if';
import { createLauncherRuntime } from './loop';
import { action, backendFixture, nativeFixture, response, stateFixture } from './core-test-fixtures';
import type { Action, BrowserBridge, LauncherRuntime } from './types';

const runtimes: LauncherRuntime[] = [];
const web = () => action('web_search');
const file = () => action('open_file', 'file', 'xdg-open');
const other = () => action('other', 'none', 'none');
const files = [
  { path: '/synthetic/one.txt', name: 'one.txt', score: 1 },
  { path: '/synthetic/two.txt', name: 'two.txt', score: .9 },
];
function uncertainWeb(request: WireRequest) {
  const result = response(request, 'web_search', .12);
  result.answers.action = { type: 'choice', choice: 'web_search', confidence: .12, probabilities: { web_search: .36, other: .34, open_file: .30 } };
  return result;
}
function make(options: Parameters<typeof createLauncherRuntime>[0]) {
  const runtime = createLauncherRuntime({ bridge: null, ...options });
  runtimes.push(runtime);
  return runtime;
}
beforeEach(() => vi.useFakeTimers());
afterEach(async () => {
  for (const runtime of runtimes.splice(0)) await runtime.close();
  vi.useRealTimers();
});

describe('explicit current web activation', () => {
  it.each(['audiobookshelf', 'transcription model'])('runs %s with one explicit activation without changing model evidence', async input => {
    const native = nativeFixture(stateFixture([web(), file(), other()]));
    native.execute = vi.fn(native.execute);
    const log = new RingLog();
    const bridge: BrowserBridge = { log, connect: async () => {}, flush: async () => {}, close: () => {}, status: () => ({ state: 'connected', pending: 0 }), onError: () => () => {}, onStatus: () => () => {} };
    const runtime = make({ native, bridge, backend: backendFixture(uncertainWeb) });
    runtime.setInput(input); await vi.advanceTimersByTimeAsync(80);
    const snapshot = runtime.getSnapshot();
    expect(snapshot).toMatchObject({ status: 'ready', stale: false });
    expect(snapshot.decision?.action).toMatchObject({ choice: 'web_search', confidence: .12, probabilities: { web_search: .36, other: .34, open_file: .30 } });
    expect(snapshot.decision?.action.fallback).not.toBe(true);
    expect(snapshot.candidates[0]).toMatchObject({ band: 'fallback', confidence: .12, directActivation: true, args: { input } });
    expect(native.execute).not.toHaveBeenCalled();
    expect((await runtime.execute('web_search', { primed: false, hold_ms: 0, confirmed: false })).status).toBe('executed');
    expect(native.execute).toHaveBeenCalledExactlyOnceWith('web_search', { input }, undefined);
    const outcome = log.events().find(event => event.type === 'outcome');
    expect(outcome).toMatchObject({ data: { payload: { primed: false, hold_ms: 0, confidence: .12, accepted: true, success: true } } });
    expect(runtime.getSnapshot().metrics.primed_acceptance).toBeNull();
  });

  it('keeps a web result usable when an unrelated file signal would trigger a failing rerank', async () => {
    const state = stateFixture([web(), file(), other()]); state.candidates = files;
    const native = nativeFixture(state); native.execute = vi.fn(native.execute);
    let fileCalls = 0;
    const runtime = make({ native, backend: backendFixture(request => {
      if (request.questions.file) { fileCalls++; throw new Error('Decision deadline exceeded'); }
      const result = uncertainWeb(request);
      result.answers.is_file_search = { type: 'noul', p: .6, confidence: .6, probabilities: { true: .6, false: .4 } };
      return result;
    }) });
    runtime.setInput('audiobookshelf'); await vi.advanceTimersByTimeAsync(80);
    expect(runtime.getSnapshot()).toMatchObject({ status: 'ready', stale: false, errors: [] });
    expect(fileCalls).toBe(0);
    expect((await runtime.execute('web_search', { primed: false, hold_ms: 0, confirmed: false })).status).toBe('executed');
    expect((await runtime.execute('open_file', { primed: false, hold_ms: 150, confirmed: false })).status).toBe('error');
    expect(fileCalls).toBe(1);
    expect(native.execute).toHaveBeenCalledTimes(1);
    expect(runtime.getSnapshot()).toMatchObject({ status: 'ready', stale: false });
    expect(runtime.getSnapshot().errors.at(-1)?.source).toBe('rerank');
  });

  it.each([
    ['empty input', '', web(), 'web_search', false],
    ['invalid arguments', 'bad\0input', web(), 'web_search', false],
    ['guarded web', 'topic', { ...web(), requiresConfirmation: true }, 'web_search', false],
    ['rebound executor', 'topic', { ...web(), exec: { kind: 'shell', arg: 'input' } }, 'web_search', false],
    ['different builtin', 'topic', { ...web(), exec: { kind: 'builtin', name: 'note', arg: 'input' } }, 'web_search', false],
    ['different argument', 'https://example.org', { ...web(), exec: { kind: 'builtin', name: 'web_search', arg: 'url' } }, 'web_search', false],
    ['nonleading web', 'topic', web(), 'open_file', false],
    ['gibberish brake', 'topic', web(), 'web_search', true],
  ] as const)('does not allow direct activation for %s', async (_name, input, candidate, leader, gibberish) => {
    const state = stateFixture([candidate as Action, file(), other()]); state.candidates = files.slice(0, 1);
    const native = nativeFixture(state); native.execute = vi.fn(native.execute);
    const runtime = make({ native, backend: backendFixture(request => {
      const result = response(request, leader, .12);
      if (gibberish) result.answers.is_gibberish = { type: 'noul', p: .99, confidence: .99, probabilities: { true: .99, false: .01 } };
      return result;
    }) });
    runtime.setInput(input); await vi.advanceTimersByTimeAsync(80);
    expect(runtime.getSnapshot().candidates.find(candidate => candidate.action.id === 'web_search')?.directActivation).not.toBe(true);
    expect((await runtime.execute('web_search', { primed: false, hold_ms: 0, confirmed: false })).status).toBe('error');
    expect(native.execute).not.toHaveBeenCalled();
  });

  it.each(['Is there a transcription server on lab?', 'set up deadlock mod manager'])('retains guarded agent recommendation for %s', async input => {
    const agent: Action = { ...action('send_to_agent', 'input', 'hermes'), requiresConfirmation: true };
    const native = nativeFixture(stateFixture([agent, web(), other()]));
    native.execute = vi.fn(async actionId => ({ status: 'confirmationRequired' as const, actionId, message: 'Confirm', confirmationToken: 'fixture-token' }));
    const runtime = make({ native, backend: backendFixture(request => response(request, 'send_to_agent', .99)) });
    runtime.setInput(input); await vi.advanceTimersByTimeAsync(80);
    expect(runtime.getSnapshot().candidates[0]).toMatchObject({ action: { id: 'send_to_agent' }, band: 'confirm', args: { input } });
    expect(runtime.getSnapshot().candidates[0]?.directActivation).not.toBe(true);
    expect((await runtime.execute('send_to_agent', { primed: false, hold_ms: 0, confirmed: false })).status).toBe('error');
    expect(native.execute).not.toHaveBeenCalled();
    expect((await runtime.execute('send_to_agent', { primed: false, hold_ms: 150, confirmed: false })).status).toBe('confirmationRequired');
    runtime.cancelConfirmation();
    expect((await runtime.execute('send_to_agent', { primed: false, hold_ms: 0, confirmed: true })).status).toBe('error');
    expect(native.execute).toHaveBeenCalledTimes(1);
  });

  it('blocks retained direct candidates after a primary deadline and recovers only on explicit retry', async () => {
    let fail = false;
    const native = nativeFixture(stateFixture([web(), file(), other()])); native.execute = vi.fn(native.execute);
    const runtime = make({ native, timeoutMs: 30, backend: backendFixture(async request => {
      if (fail && JSON.parse(request.state).input) await new Promise(resolve => setTimeout(resolve, 100));
      return uncertainWeb(request);
    }) });
    runtime.setInput('audiobookshelf'); await vi.advanceTimersByTimeAsync(80);
    const previous = runtime.getSnapshot().candidates;
    fail = true; runtime.setInput('transcription model'); await vi.advanceTimersByTimeAsync(111);
    expect(runtime.getSnapshot()).toMatchObject({ status: 'stale', stale: true, candidates: previous, model: { error: { code: 'timeout' } } });
    expect((await runtime.execute('web_search', { primed: false, hold_ms: 0, confirmed: false })).status).toBe('error');
    expect(native.execute).not.toHaveBeenCalled();
    fail = false; await runtime.retry();
    expect(runtime.getSnapshot()).toMatchObject({ status: 'ready', stale: false });
    expect((await runtime.execute('web_search', { primed: false, hold_ms: 0, confirmed: false })).status).toBe('executed');
    expect(native.execute).toHaveBeenCalledExactlyOnceWith('web_search', { input: 'transcription model' }, undefined);
  });

  it('keeps a leading recent-file deadline blocking and retains the previous distribution', async () => {
    const recent = action('open_recent', 'file', 'xdg-open');
    const state = stateFixture([recent, web(), other()]);
    state.recent = files;
    state.candidates = [{ path: '/synthetic/unrelated.txt', name: 'unrelated.txt', score: 1 }];
    const native = nativeFixture(state); native.execute = vi.fn(native.execute);
    let fail = false;
    const runtime = make({ native, timeoutMs: 30, backend: backendFixture(async request => {
      if (request.questions.file && fail) await new Promise(resolve => setTimeout(resolve, 100));
      return response(request, 'open_recent', .99, 'f1');
    }) });
    runtime.setInput('the other recent file'); await vi.advanceTimersByTimeAsync(80);
    const previous = runtime.getSnapshot().candidates;
    expect(previous[0]?.args.file).toBe('/synthetic/two.txt');
    fail = true; runtime.setInput('a different recent file'); await vi.advanceTimersByTimeAsync(111);
    expect(runtime.getSnapshot()).toMatchObject({ status: 'stale', stale: true, candidates: previous, model: { error: { code: 'timeout' } } });
    expect((await runtime.execute('open_recent', { primed: false, hold_ms: 150, confirmed: false })).status).toBe('error');
    expect(native.execute).not.toHaveBeenCalled();
  });

  it('never executes superseded web intent, including late transport replies during debounce', async () => {
    const native = nativeFixture(stateFixture([web(), file(), other()])); native.execute = vi.fn(native.execute);
    const runtime = make({ native, debounceMs: 150, backend: backendFixture(async request => {
      if (JSON.parse(request.state).input === 'old') await new Promise(resolve => setTimeout(resolve, 100));
      return uncertainWeb(request);
    }) });
    runtime.setInput('old'); await vi.advanceTimersByTimeAsync(150);
    runtime.setInput('audiobookshelf'); await vi.advanceTimersByTimeAsync(100);
    expect((await runtime.execute('web_search', { primed: false, hold_ms: 0, confirmed: false })).status).toBe('error');
    expect(native.execute).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(50);
    expect((await runtime.execute('web_search', { primed: false, hold_ms: 0, confirmed: false })).status).toBe('executed');
    await vi.advanceTimersByTimeAsync(100);
    expect(native.execute).toHaveBeenCalledExactlyOnceWith('web_search', { input: 'audiobookshelf' }, undefined);
    expect(runtime.getSnapshot().candidates[0]?.args).toEqual({ input: 'audiobookshelf' });
  });
});
