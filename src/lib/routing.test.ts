import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Answer, WireRequest } from 'soft-if';
import measured from '../../scripts/fixtures/routing-live-regressions.json';
import { buildQuestions, createLauncherRuntime } from './loop';
import { backendFixture, nativeFixture, response, stateFixture } from './core-test-fixtures';
import { catalogueFixture } from '../../bench/fixtures';
import type { LauncherRuntime } from './types';

const runtimes: LauncherRuntime[] = [];
beforeEach(() => vi.useFakeTimers());
afterEach(async () => {
  for (const runtime of runtimes.splice(0)) await runtime.close();
  vi.useRealTimers();
});

describe('routing contract', () => {
  // Recorded real Clef answers preserve the transport/runtime contract. Live
  // prompt correctness is checked by routing-runtime-verification.ts.
  it.each(measured.samples)('preserves the measured recommendation and distribution for $input', async sample => {
    const actions = await catalogueFixture();
    const state = stateFixture(actions);
    if (sample.expected === 'open_file') state.candidates = [{ path: '/home/synthetic/Documents/transcription-notes.txt', name: 'transcription-notes.txt', score: 1 }];
    const native = nativeFixture(state); native.execute = vi.fn(native.execute);
    const runtime = createLauncherRuntime({ native, bridge: null, backend: backendFixture(() => ({ answers: sample.answers as Record<string, Answer>, backend: 'recorded-clef-answer' })) });
    runtimes.push(runtime);
    runtime.setInput(sample.input); await vi.advanceTimersByTimeAsync(80);
    const snapshot = runtime.getSnapshot();
    expect(snapshot.status).toBe(sample.status);
    expect(snapshot.decision?.action.probabilities).toEqual(sample.answers.action.probabilities);
    expect(snapshot.decision?.action.confidence).toBe(sample.answers.action.confidence);
    expect(snapshot.candidates[0]).toMatchObject({ action: { id: sample.expected }, band: sample.band, args: sample.args });
    expect(native.execute).not.toHaveBeenCalled();
    if (sample.expected === 'send_to_agent') expect(snapshot.candidates[0]?.band).not.toBe('act');
  });

  it('keeps displayed descriptions and catalogue membership while adding model-only intent criteria', async () => {
    const actions = await catalogueFixture();
    const before = structuredClone(actions);
    const questions = buildQuestions(actions);
    expect(Object.keys(questions.action.criteria)).toEqual(actions.map(action => action.id));
    expect(actions).toEqual(before);
    expect(questions.action.criteria.send_to_agent).toContain('complete user question');
    expect(questions.action.criteria.send_to_agent).toContain('set up, install');
    expect(questions.action.criteria.open_app).toContain('installedAppMatch');
    expect(questions.action.criteria.web_search).toContain('bare topic keywords');
    expect(buildQuestions(actions.filter(action => action.id !== 'send_to_agent')).action.criteria).not.toHaveProperty('send_to_agent');
    expect(questions.action.instructions).toContain('question mark is optional');
  });

  it.each(['known', 'pending', 'failed'] as const)('sends factual app availability only from a %s app index', async mode => {
    const native = nativeFixture();
    native.getApps = mode === 'pending' ? () => new Promise(() => {}) : mode === 'failed' ? async () => { throw new Error('index unavailable'); } : async () => stateFixture().apps;
    const requests: WireRequest[] = [];
    const runtime = createLauncherRuntime({ native, bridge: null, backend: backendFixture(request => { requests.push(request); return response(request); }) });
    runtimes.push(runtime);
    // open need not finish while the optional app index is pending.
    void runtime.open(); await vi.advanceTimersByTimeAsync(1);
    runtime.setInput('open Firefox'); await vi.advanceTimersByTimeAsync(80);
    const primary = JSON.parse(requests.at(-1)!.state);
    expect(primary.input).toBe('open Firefox');
    if (mode === 'known') expect(primary.installedAppMatch).toBe(true);
    else expect(primary).not.toHaveProperty('installedAppMatch');
    expect(primary.window).not.toHaveProperty('title');
    expect(primary.clipboard).not.toHaveProperty('head');
    expect(requests[0]!.state).not.toContain('installedAppMatch');
    expect(requests.at(-1)!.state).not.toContain('firefox.desktop');
  });

  it('uses complete getState apps for legacy bridges without getApps and never primes agent recommendations', async () => {
    const actions = await catalogueFixture();
    const native = nativeFixture(stateFixture(actions));
    native.execute = vi.fn(async actionId => ({ status: 'confirmationRequired' as const, actionId, message: 'Confirm', confirmationToken: 'fixture-token' }));
    const requests: WireRequest[] = [];
    const runtime = createLauncherRuntime({ native, bridge: null, backend: backendFixture(request => { requests.push(request); return response(request, 'send_to_agent', .99); }) });
    runtimes.push(runtime);
    runtime.setInput('set up Firefox'); await vi.advanceTimersByTimeAsync(80);
    expect(JSON.parse(requests.at(-1)!.state).installedAppMatch).toBe(false);
    expect(runtime.getSnapshot().candidates[0]).toMatchObject({ action: { id: 'send_to_agent', requiresConfirmation: true }, band: 'confirm', args: { input: 'set up Firefox' } });
    expect((await runtime.execute('send_to_agent', { primed: true, hold_ms: 0, confirmed: false })).status).toBe('error');
    expect(native.execute).not.toHaveBeenCalled();
    const first = await runtime.execute('send_to_agent', { primed: false, hold_ms: 150, confirmed: false });
    expect(first.status).toBe('confirmationRequired');
    runtime.setInput('Firefox'); await vi.advanceTimersByTimeAsync(80);
    expect(JSON.parse(requests.at(-1)!.state).installedAppMatch).toBe(true);
    expect((await runtime.execute('send_to_agent', { primed: false, hold_ms: 0, confirmed: true })).status).toBe('error');
    expect(native.execute).toHaveBeenCalledTimes(1);
  });
});
