import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Policy, RingLog, calibrate } from 'soft-if';
import type { WireRequest } from 'soft-if';
import { createLauncherRuntime } from './loop';
import { action, backendFixture, nativeFixture, response, stateFixture } from './core-test-fixtures';
import type { BrowserBridge, LauncherRuntime } from './types';

const runtimes: LauncherRuntime[] = [];
function make(options: Parameters<typeof createLauncherRuntime>[0] = { native: nativeFixture() }): LauncherRuntime {
  const runtime = createLauncherRuntime({ bridge: null, backend: backendFixture(), ...options });
  runtimes.push(runtime); return runtime;
}
const advance = async (ms: number): Promise<void> => { await vi.advanceTimersByTimeAsync(ms); };
beforeEach(() => vi.useFakeTimers());
afterEach(async () => {
  for (const runtime of runtimes.splice(0)) {
    const closing = runtime.close().catch(() => {});
    await vi.advanceTimersByTimeAsync(1); await closing;
  }
  vi.useRealTimers();
});

describe('decision runtime with actual soft-if', () => {
  it('makes exact bare-domain Open URL candidates executable with normalized native arguments', async () => {
    const native = nativeFixture(stateFixture([action('open_url', 'url', 'xdg-open'), action('other', 'none', 'none')]));
    native.execute = vi.fn(native.execute);
    const runtime = make({ native, backend: backendFixture(request => response(request, 'open_url')) });
    await runtime.open();
    runtime.setInput('openrouter.ai'); await advance(80);
    const candidate = runtime.getSnapshot().candidates.find(candidate => candidate.action.id === 'open_url');
    expect(candidate?.argumentError).toBeUndefined();
    expect(candidate?.args).toEqual({ url: 'https://openrouter.ai/' });
    expect((await runtime.execute('open_url', { primed: true, hold_ms: 0, confirmed: false })).status).toBe('executed');
    expect(native.execute).toHaveBeenCalledExactlyOnceWith('open_url', { url: 'https://openrouter.ai/' }, undefined);
  });

  it('warms up, asks all eight questions, debounces once and clamps tuning', async () => {
    const requests: WireRequest[] = [];
    const runtime = make({ native: nativeFixture(), backend: backendFixture(request => { requests.push(request); return response(request); }) });
    await runtime.open(); expect(runtime.getSnapshot().status).toBe('ready');
    expect(Object.keys(requests[0]!.questions)).toEqual(['action','is_file_search','is_calc','is_url','wants_paste','is_question','is_gibberish','urgency']);
    runtime.setInput('s'); await advance(30); runtime.setInput('se'); await advance(79);
    expect(requests).toHaveLength(2); await advance(1); expect(requests).toHaveLength(3);
    expect(runtime.getSnapshot().metrics.decisions).toBe(1);
    runtime.setDebounce(1); expect(runtime.getSnapshot().debounceMs).toBe(40);
    runtime.setDebounce(999); expect(runtime.getSnapshot().debounceMs).toBe(150);
    runtime.setDebounce(NaN); expect(runtime.getSnapshot().debounceMs).toBe(80);
  });

  it('never renders an injected 500 ms stale answer even while the newer input waits for debounce', async () => {
    const completed: string[] = [];
    const runtime = make({ native: nativeFixture(), timeoutMs: 1000, debounceMs: 150,
      backend: backendFixture(async request => {
        const input = JSON.parse(request.state).input as string;
        // Deliberately ignore cancellation to emulate a late transport response.
        if (input === 'old') await new Promise(resolve => setTimeout(resolve, 500));
        completed.push(input); return response(request, input === 'old' ? 'run_shell' : 'web_search');
      }),
    });
    await runtime.open(); const initialId = runtime.getSnapshot().decision?.id;
    const rendered: string[] = []; runtime.subscribe(snapshot => { if (snapshot.status === 'ready') rendered.push(snapshot.decision!.action.choice); });
    runtime.setInput('old'); await advance(150); await advance(450);
    runtime.setInput('new'); await advance(50); // old backend completes; new debounce still has 100 ms to run
    expect(completed).toContain('old');
    expect(runtime.getSnapshot()).toMatchObject({ input: 'new', status: 'debouncing', stale: true });
    expect(runtime.getSnapshot().decision?.id).toBe(initialId);
    expect(rendered).not.toContain('run_shell');
    await advance(100); expect(runtime.getSnapshot()).toMatchObject({ status: 'ready', stale: false });
    expect(runtime.getSnapshot().metrics.superseded).toBeGreaterThan(0);
  });

  it('retains the exact previous distribution at an explicit 300 ms timeout despite branch timeout settings', async () => {
    const policy = new Policy({ branches: { 'launcher.action': { timeoutMs: 1000 } } });
    const runtime = make({ native: nativeFixture(), policy, timeoutMs: 300, backend: backendFixture(async request => {
      if (JSON.parse(request.state).input === 'slow') await new Promise(resolve => setTimeout(resolve, 500));
      return response(request);
    }) });
    await runtime.open(); const before = runtime.getSnapshot().candidates;
    runtime.setInput('slow'); await advance(80 + 301);
    expect(runtime.getSnapshot()).toMatchObject({ status: 'stale', stale: true });
    expect(runtime.getSnapshot().candidates).toEqual(before);
    expect(runtime.getSnapshot().metrics.timedOut).toBe(1);
    expect((await runtime.execute('web_search', { primed: true, hold_ms: 0, confirmed: false })).status).toBe('error');
  });

  it('uses explicit confidence only for the leader, applies gibberish brake and live policy during pending input', async () => {
    const policy = new Policy(); const native = nativeFixture(); native.execute = vi.fn(native.execute);
    const runtime = make({ native, policy });
    runtime.setInput('find something'); await advance(80);
    expect(runtime.getSnapshot().candidates[0]).toMatchObject({ probability: .7, confidence: .94, band: 'act' });
    expect(runtime.getSnapshot().candidates.filter(candidate => candidate.band === 'act')).toHaveLength(1);
    policy.update({ bands: { act: .99 } }); expect(runtime.getSnapshot().candidates[0]?.band).toBe('confirm');
    runtime.setInput('new pending input'); policy.update({ bands: { act: .8 } });
    expect(runtime.getSnapshot().stale).toBe(true);
    expect(runtime.getSnapshot().candidates[0]?.args.input).toBe('find something');
    expect((await runtime.execute('web_search', { primed: true, hold_ms: 0, confirmed: false })).status).toBe('error');
    expect(native.execute).not.toHaveBeenCalled();
    await advance(80);
    const gibberish = make({ native: nativeFixture(), backend: backendFixture(request => {
      const result = response(request); result.answers.is_gibberish = { type: 'noul', p: .99, confidence: .99, probabilities: { true: .99, false: .01 } }; return result;
    }) });
    await gibberish.open(); expect(gibberish.getSnapshot().candidates[0]?.band).toBe('fallback');
  });

  it('semantically reranks only the top five code files and retains last distribution if rerank times out', async () => {
    const state = stateFixture([action('open_file', 'file', 'xdg-open'), action('web_search'), action('other', 'none', 'none')]);
    state.candidates = Array.from({ length: 7 }, (_, i) => ({ path: `/home/test/Downloads/report-${i}.pdf`, name: `report-${i}.pdf`, score: i, modifiedAt: 1000 + i }));
    const requests: WireRequest[] = []; let fail = false;
    const runtime = make({ native: nativeFixture(state), timeoutMs: 300, backend: backendFixture(async request => {
      requests.push(request);
      if (request.questions.file && fail) await new Promise(resolve => setTimeout(resolve, 500));
      return response(request, 'open_file', .94, 'f4');
    }) });
    runtime.setInput('the pdf I just downloaded'); await advance(80);
    expect(requests).toHaveLength(3);
    expect(Object.keys(requests[2]!.questions.file!.type === 'choice' ? requests[2]!.questions.file!.criteria : {})).toEqual(['f0','f1','f2','f3','f4']);
    expect(requests[2]!.state).toContain('modifiedAt'); expect(requests[2]!.state).not.toContain('/home/test');
    expect(runtime.getSnapshot().candidates[0]?.args.file).toBe('/home/test/Downloads/report-4.pdf');
    const previous = runtime.getSnapshot().candidates; fail = true;
    runtime.setInput('a different pdf'); await advance(80 + 301);
    expect(runtime.getSnapshot()).toMatchObject({ status: 'stale', stale: true });
    expect(runtime.getSnapshot().candidates).toEqual(previous);
  });

  it('reranks recent files as their own candidate set and respects live file-confidence policy', async () => {
    const state = stateFixture([action('open_recent', 'file', 'xdg-open'), action('open_file', 'file', 'xdg-open'), action('other', 'none', 'none')]);
    state.candidates = [{ path: '/home/test/unrelated.txt', name: 'unrelated.txt', score: 1 }];
    state.recent = [{ path: '/home/test/old.pdf', name: 'old.pdf', score: 1 }, { path: '/home/test/new.pdf', name: 'new.pdf', score: .9 }];
    const native = nativeFixture(state); native.execute = vi.fn(native.execute);
    const log = new RingLog();
    const bridge: BrowserBridge = { log, connect: async () => {}, flush: async () => {}, close: () => {}, status: () => ({ state: 'connected', pending: 0 }), onError: () => () => {}, onStatus: () => () => {} };
    const policy = new Policy({ branches: { 'launcher.file': { act: .8, confirm: .6 } } });
    let fileConfidence = .7;
    const runtime = make({ native, policy, bridge, backend: backendFixture(request => response(request, 'open_recent', request.questions.file ? fileConfidence : .99, 'f1')) });
    runtime.setInput('the other recent pdf'); await advance(80);
    const candidate = runtime.getSnapshot().candidates[0]!;
    expect(candidate).toMatchObject({ action: { id: 'open_recent' }, args: { file: '/home/test/new.pdf' }, band: 'confirm' });
    expect(log.decisions().find(decision => decision.name === 'launcher.file')?.answers.file).toMatchObject({ branch: 'launcher.file', confidence: .7 });
    expect((await runtime.execute('open_recent', { primed: true, hold_ms: 0, confirmed: false })).status).toBe('error');
    expect((await runtime.execute('open_recent', { primed: false, hold_ms: 150, confirmed: false })).status).toBe('executed');
    expect(native.execute).toHaveBeenCalledWith('open_recent', { file: '/home/test/new.pdf' }, undefined);
    policy.update({ branches: { 'launcher.file': { act: 1, confirm: 1 } } });
    expect(runtime.getSnapshot().candidates[0]).toMatchObject({ band: 'fallback', argumentError: expect.stringContaining('confidence') });
    expect((await runtime.execute('open_recent', { primed: false, hold_ms: 1000, confirmed: false })).status).toBe('error');
    fileConfidence = .1; runtime.setInput('low confidence recent'); await advance(80);
    expect(runtime.getSnapshot().candidates[0]?.band).toBe('fallback');
  });

  it('requires native confirmation token from a distinct call, binds it to tick, and permits nonleading deliberate selection', async () => {
    const native = nativeFixture(); native.execute = vi.fn(native.execute);
    const runtime = make({ native }); runtime.setInput('pwd'); await advance(80);
    const early = await runtime.execute('run_shell', { primed: true, hold_ms: 0, confirmed: false });
    expect(early.status).toBe('error');
    const first = await runtime.execute('run_shell', { primed: false, hold_ms: 150, confirmed: false });
    expect(first.status).toBe('confirmationRequired'); expect(first).not.toHaveProperty('confirmationToken');
    const second = await runtime.execute('run_shell', { primed: false, hold_ms: 0, confirmed: true });
    expect(second.status).toBe('executed');
    expect(native.execute).toHaveBeenNthCalledWith(2, 'run_shell', { input: 'pwd' }, 'secret-token');
    expect((await runtime.execute('run_shell', { primed: false, hold_ms: 0, confirmed: true })).status).toBe('error');
    await runtime.execute('run_shell', { primed: false, hold_ms: 150, confirmed: false });
    runtime.cancelConfirmation();
    expect((await runtime.execute('run_shell', { primed: false, hold_ms: 0, confirmed: true })).status).toBe('error');
    await runtime.execute('run_shell', { primed: false, hold_ms: 150, confirmed: false });
    runtime.setInput('whoami'); await advance(80);
    expect((await runtime.execute('run_shell', { primed: false, hold_ms: 0, confirmed: true })).status).toBe('error');
  });

  it('reranks a deliberately selected nonleading file action before passing arguments to native', async () => {
    const state = stateFixture([action('web_search'), action('open_file', 'file', 'xdg-open'), action('other', 'none', 'none')]);
    state.candidates = [{ path: '/home/test/one.pdf', name: 'one.pdf', score: 1 }, { path: '/home/test/two.pdf', name: 'two.pdf', score: .9 }];
    const native = nativeFixture(state); native.execute = vi.fn(native.execute);
    const runtime = make({ native, backend: backendFixture(request => response(request, 'web_search', .94, 'f1')) });
    runtime.setInput('the second pdf'); await advance(80);
    expect(runtime.getSnapshot().candidates.find(item => item.action.id === 'open_file')).toMatchObject({ argumentPending: true });
    const result = await runtime.execute('open_file', { primed: false, hold_ms: 150, confirmed: false });
    expect(result.status).toBe('executed');
    expect(native.execute).toHaveBeenCalledWith('open_file', { file: '/home/test/two.pdf' }, undefined);
  });

  it('does not resurrect a confirmation cancelled while the first native call is pending', async () => {
    const native = nativeFixture(); const original = native.execute;
    native.execute = async (...args) => { await new Promise(resolve => setTimeout(resolve, 50)); return original(...args); };
    const runtime = make({ native }); runtime.setInput('pwd'); await advance(80);
    const first = runtime.execute('run_shell', { primed: false, hold_ms: 150, confirmed: false });
    runtime.cancelConfirmation(); await advance(50);
    expect((await first).status).toBe('error');
    expect((await runtime.execute('run_shell', { primed: false, hold_ms: 0, confirmed: true })).status).toBe('error');
  });

  it('cancels execution intent before native dispatch when confirmation is dismissed during on-demand rerank', async () => {
    const state = stateFixture([action('web_search'), { ...action('rename_file', 'file'), requiresConfirmation: true }, action('other', 'none', 'none')]);
    state.candidates = [{ path: '/home/test/one.txt', name: 'one.txt', score: 1 }, { path: '/home/test/two.txt', name: 'two.txt', score: .9 }];
    const native = nativeFixture(state); native.execute = vi.fn(native.execute);
    const runtime = make({ native, backend: backendFixture(async request => {
      if (request.questions.file) await new Promise(resolve => setTimeout(resolve, 100));
      return response(request, 'web_search', .94, 'f1');
    }) });
    runtime.setInput('rename one.txt to new.txt'); await advance(80);
    const first = runtime.execute('rename_file', { primed: false, hold_ms: 150, confirmed: false });
    await advance(50); runtime.cancelConfirmation(); await advance(50);
    expect((await first).status).toBe('error');
    expect(native.execute).not.toHaveBeenCalled();
    expect((await runtime.execute('rename_file', { primed: false, hold_ms: 0, confirmed: true })).status).toBe('error');
    expect(native.execute).not.toHaveBeenCalled();
  });

  it('logs fire, failure and dismissal fields, and keeps execution linked to its original decision if input changes', async () => {
    const log = new RingLog();
    const outcomes = () => log.events().flatMap(event => event.type === 'outcome' ? [event.data] : []);
    const bridge: BrowserBridge = { log, connect: async () => {}, flush: async () => {}, close: () => {}, status: () => ({ state: 'connected', pending: 0 }), onError: () => () => {}, onStatus: () => () => {} };
    const native = nativeFixture();
    native.execute = async actionId => { await new Promise(resolve => setTimeout(resolve, 50)); return { status: 'executed', actionId, message: 'ok' }; };
    const runtime = make({ native, bridge }); runtime.setInput('original'); await advance(80);
    const original = runtime.getSnapshot().decision!.id;
    const fire = runtime.execute('web_search', { primed: true, hold_ms: 0, confirmed: false });
    runtime.setInput('new'); await advance(80); const result = await fire;
    expect(result.status).toBe('executed');
    const newer = runtime.getSnapshot().decision!.id;
    expect(newer).not.toBe(original);
    await runtime.dismiss(); await runtime.flush();
    expect(outcomes()[0]).toMatchObject({ request_id: original, payload: { action: 'web_search', primed: true, hold_ms: 0, accepted: true, success: true } });
    expect(outcomes().at(-1)).toMatchObject({ request_id: newer, payload: { accepted: false, dismissed: true, hold_ms: 0 } });
    expect(runtime.markUndone(result.outcomeId!)).toBe(true);
    expect(outcomes().at(-1)).toMatchObject({ request_id: original, payload: { undone: true, accepted: false } });
  });

  it('copies actual executed result text and exposes persistence errors without dropping queued status', async () => {
    const state = stateFixture([action('calc', 'expr'), action('copy_result', 'text'), action('other', 'none', 'none')]);
    const native = nativeFixture(state);
    native.execute = vi.fn(async actionId => ({ status: 'executed' as const, actionId, message: 'ok', ...(actionId === 'calc' ? { text: '42' } : {}) }));
    const runtime = make({ native, backend: backendFixture(request => response(request, JSON.parse(request.state).input === 'copy result' ? 'copy_result' : 'calc')) });
    runtime.setInput('21*2'); await advance(80);
    expect((await runtime.execute('calc', { primed: true, hold_ms: 0, confirmed: false })).status).toBe('executed');
    runtime.setInput('copy result'); await advance(80);
    await runtime.execute('copy_result', { primed: true, hold_ms: 0, confirmed: false });
    expect(native.execute).toHaveBeenLastCalledWith('copy_result', { text: '42' }, undefined);

    const bridge: BrowserBridge = { log: new RingLog(), connect: async () => {}, flush: async () => { throw new Error('disk unavailable'); }, close: () => {}, status: () => ({ state: 'error', pending: 3, error: 'disk unavailable' }), onError: () => () => {}, onStatus: () => () => {} };
    const failed = make({ native: nativeFixture(), bridge });
    await expect(failed.flush()).rejects.toThrow('disk unavailable');
    expect(failed.getSnapshot()).toMatchObject({ bridge: { pending: 3 }, errors: [{ source: 'persistence', message: 'disk unavailable' }] });
  });

  it('logs a rejected native execution and expires confirmation after fifteen seconds', async () => {
    const log = new RingLog();
    const bridge: BrowserBridge = { log, connect: async () => {}, flush: async () => {}, close: () => {}, status: () => ({ state: 'connected', pending: 0 }), onError: () => () => {}, onStatus: () => () => {} };
    const native = nativeFixture();
    const runtime = make({ native, bridge }); runtime.setInput('pwd'); await advance(80);
    await runtime.execute('run_shell', { primed: false, hold_ms: 150, confirmed: false });
    await advance(15001);
    expect((await runtime.execute('run_shell', { primed: false, hold_ms: 0, confirmed: true })).status).toBe('error');
    native.execute = async () => { throw new Error('native denied'); };
    expect((await runtime.execute('web_search', { primed: true, hold_ms: 0, confirmed: false })).status).toBe('error');
    const outcome = log.events().find(event => event.type === 'outcome');
    expect(outcome).toMatchObject({ type: 'outcome', data: { payload: { action: 'web_search', accepted: false, success: false, hold_ms: 0, error: 'native denied' } } });
  });

  it('labels a successful deliberate override as a rejected model recommendation, without counting a misfire', async () => {
    const state = stateFixture([action('web_search'), action('define_word'), action('other', 'none', 'none')]);
    const log = new RingLog();
    const bridge: BrowserBridge = { log, connect: async () => {}, flush: async () => {}, close: () => {}, status: () => ({ state: 'connected', pending: 0 }), onError: () => () => {}, onStatus: () => () => {} };
    const runtime = make({ native: nativeFixture(state), bridge });
    runtime.setInput('probability'); await advance(80);
    const result = await runtime.execute('define_word', { primed: true, hold_ms: 150, confirmed: false });
    expect(result.status).toBe('executed');
    const outcomes = log.events().flatMap(event => event.type === 'outcome' ? [event.data] : []);
    expect(outcomes[0]?.payload).toMatchObject({ action: 'define_word', chosen: 'define_word', predicted: 'web_search', success: true, accepted: false, override: true, primed: false });
    const calibration = calibrate(log.decisions(), outcomes, log.events().flatMap(event => event.type === 'branch' ? [event.data] : []), { branch: 'launcher.action' });
    expect(calibration.count).toBe(1); expect(calibration.ece).toBeCloseTo(.94);
    expect(runtime.getSnapshot().metrics).toMatchObject({ launches: 1, misfires_per_100: 0, ece: .94 });
    expect(runtime.markUndone(result.outcomeId!)).toBe(true);
    expect(runtime.getSnapshot().metrics.misfires_per_100).toBe(100);
  });

  it('coordinates teaching proposal review without an implicit catalogue save', async () => {
    const native = nativeFixture(); native.proposeTeaching = vi.fn(native.proposeTeaching); native.saveTeaching = vi.fn(native.saveTeaching);
    const runtime = make({ native, backend: backendFixture(request => response(request, 'other')) });
    for (const input of ['invent one', 'invent two', 'invent three']) { runtime.setInput(input); await advance(80); }
    expect(runtime.getSnapshot().teaching.available).toBe(true);
    const proposal = await runtime.proposeTeaching();
    expect(native.proposeTeaching).toHaveBeenCalledWith(['invent one','invent two','invent three']);
    expect(native.saveTeaching).not.toHaveBeenCalled();
    await runtime.saveTeaching(proposal.id, 'explicitly reviewed TOML');
    expect(native.saveTeaching).toHaveBeenCalledWith(proposal.proposalId, 'explicitly reviewed TOML');
    expect(runtime.getSnapshot()).toMatchObject({ status: 'idle', stale: true, teaching: { available: false } });
  });
});

describe('terminal decision outcomes', () => {
  function fixture(native = nativeFixture()) {
    const log = new RingLog();
    const bridge: BrowserBridge = { log, connect: async () => {}, flush: async () => {}, close: () => {}, status: () => ({ state: 'connected', pending: 0 }), onError: () => () => {}, onStatus: () => () => {} };
    const runtime = make({ native, bridge });
    const outcomes = () => log.events().flatMap(event => event.type === 'outcome' ? [event.data] : []);
    return { runtime, log, outcomes };
  }

  it.each([undefined, '4'])('keeps completed success after repeated dismissal, with result text %s', async text => {
    const native = nativeFixture();
    native.execute = async actionId => ({ status: 'executed', actionId, message: 'ok', ...(text === undefined ? {} : { text }) });
    const { runtime, log, outcomes } = fixture(native);
    runtime.setInput('public test'); await advance(80);
    const result = await runtime.execute('web_search', { primed: true, hold_ms: 0, confirmed: false });
    const completed = outcomes();
    const metrics = runtime.getSnapshot().metrics;
    await runtime.dismiss(); await runtime.dismiss(); await runtime.flush();
    expect(outcomes()).toEqual(completed);
    expect(outcomes()).toHaveLength(1);
    expect(outcomes()[0]?.payload).toMatchObject({ outcomeId: result.outcomeId, success: true, accepted: true });
    expect(runtime.getSnapshot()).toMatchObject({ status: 'idle', stale: true, metrics });
    const calibration = calibrate(log.decisions(), outcomes(), log.events().flatMap(event => event.type === 'branch' ? [event.data] : []), { branch: 'launcher.action' });
    expect(calibration.count).toBe(1); expect(calibration.ece).toBeCloseTo(.06);
    expect(runtime.markUndone(result.outcomeId!)).toBe(true);
    const undone = outcomes();
    expect(undone.at(-1)).toMatchObject({ request_id: completed[0]!.request_id, payload: { outcomeId: result.outcomeId, undone: true, accepted: false } });
    await runtime.dismiss(); await runtime.flush();
    expect(outcomes()).toEqual(undone);
    expect(runtime.getSnapshot().metrics.misfires_per_100).toBe(100);
  });

  it('keeps a terminal execution failure when the launcher is dismissed', async () => {
    const native = nativeFixture();
    native.execute = async () => { throw new Error('native denied'); };
    const { runtime, outcomes } = fixture(native);
    runtime.setInput('public test'); await advance(80);
    const result = await runtime.execute('web_search', { primed: true, hold_ms: 0, confirmed: false });
    expect(result.status).toBe('error');
    const failed = outcomes();
    await runtime.dismiss(); await runtime.flush();
    expect(outcomes()).toEqual(failed);
    expect(outcomes()[0]?.payload).toMatchObject({ outcomeId: result.outcomeId, success: false, error: 'native denied' });
    expect(runtime.getSnapshot().metrics).toMatchObject({ launches: 1, dismissals: 0, misfires_per_100: 100 });
  });

  it('logs an unexecuted decision dismissal once and treats a newer decision independently', async () => {
    const { runtime, outcomes } = fixture();
    runtime.setInput('first'); await advance(80);
    const first = runtime.getSnapshot().decision!.id;
    await runtime.dismiss(); await runtime.dismiss(); await runtime.flush();
    expect(outcomes()).toHaveLength(1);
    expect(outcomes()[0]).toMatchObject({ request_id: first, payload: { dismissed: true, accepted: false, success: false } });
    runtime.setInput('second'); await advance(80);
    const second = runtime.getSnapshot().decision!.id;
    await runtime.dismiss(); await runtime.flush();
    expect(outcomes()).toHaveLength(2);
    expect(outcomes()[1]).toMatchObject({ request_id: second, payload: { dismissed: true, accepted: false } });
    expect(runtime.getSnapshot().metrics).toMatchObject({ launches: 0, dismissals: 2 });
  });

  it('does not reject an already executed old decision while a new input is debouncing', async () => {
    const { runtime, outcomes } = fixture();
    runtime.setInput('first'); await advance(80);
    await runtime.execute('web_search', { primed: true, hold_ms: 0, confirmed: false });
    const completed = outcomes();
    runtime.setInput('second'); await runtime.dismiss(); await advance(80); await runtime.flush();
    expect(outcomes()).toEqual(completed);
    expect(runtime.getSnapshot().status).toBe('idle');
  });

  it.each(['success', 'failure'] as const)('waits for an in-flight native %s instead of recording dismissal', async outcome => {
    const native = nativeFixture();
    native.execute = async actionId => {
      await new Promise(resolve => setTimeout(resolve, 50));
      if (outcome === 'failure') throw new Error('native denied');
      return { status: 'executed', actionId, message: 'ok' };
    };
    const { runtime, outcomes } = fixture(native);
    runtime.setInput('first'); await advance(80);
    const requestId = runtime.getSnapshot().decision!.id;
    const executing = runtime.execute('web_search', { primed: true, hold_ms: 0, confirmed: false });
    await runtime.dismiss(); await runtime.dismiss(); await runtime.flush();
    expect(outcomes()).toHaveLength(0);
    expect(runtime.getSnapshot().status).toBe('idle');
    await advance(50); const result = await executing; await runtime.flush();
    expect(result.status).toBe(outcome === 'success' ? 'executed' : 'error');
    expect(outcomes()).toHaveLength(1);
    expect(outcomes()[0]).toMatchObject({ request_id: requestId, payload: { success: outcome === 'success', accepted: outcome === 'success' } });
    expect(outcomes()[0]?.payload).not.toHaveProperty('dismissed');
    await runtime.dismiss(); await runtime.flush();
    expect(outcomes()).toHaveLength(1);
  });

  it('logs one dismissal if an in-flight native call only returns canceled confirmation', async () => {
    const native = nativeFixture(); const original = native.execute;
    native.execute = async (...args) => { await new Promise(resolve => setTimeout(resolve, 50)); return original(...args); };
    const { runtime, outcomes } = fixture(native);
    runtime.setInput('pwd'); await advance(80);
    const requestId = runtime.getSnapshot().decision!.id;
    const executing = runtime.execute('run_shell', { primed: false, hold_ms: 150, confirmed: false });
    await runtime.dismiss(); await runtime.dismiss(); await runtime.flush();
    expect(outcomes()).toHaveLength(0);
    await advance(50); expect((await executing).status).toBe('error'); await runtime.flush();
    expect(outcomes()).toHaveLength(1);
    expect(outcomes()[0]).toMatchObject({ request_id: requestId, payload: { action: 'web_search', dismissed: true, accepted: false, success: false } });
    expect((await runtime.execute('run_shell', { primed: false, hold_ms: 0, confirmed: true })).status).toBe('error');
    await runtime.dismiss(); await runtime.flush();
    expect(outcomes()).toHaveLength(1);
  });

  it('records dismissal and cancels execution before a pending file rerank dispatches to native', async () => {
    const state = stateFixture([action('web_search'), action('open_file', 'file', 'xdg-open'), action('other', 'none', 'none')]);
    state.candidates = [{ path: '/home/test/one.txt', name: 'one.txt', score: 1 }, { path: '/home/test/two.txt', name: 'two.txt', score: .9 }];
    const native = nativeFixture(state); native.execute = vi.fn(native.execute);
    const log = new RingLog();
    const bridge: BrowserBridge = { log, connect: async () => {}, flush: async () => {}, close: () => {}, status: () => ({ state: 'connected', pending: 0 }), onError: () => () => {}, onStatus: () => () => {} };
    const runtime = make({ native, bridge, backend: backendFixture(async request => {
      if (request.questions.file) await new Promise(resolve => setTimeout(resolve, 50));
      return response(request, 'web_search', .94, 'f1');
    }) });
    runtime.setInput('open the other file'); await advance(80);
    const executing = runtime.execute('open_file', { primed: false, hold_ms: 150, confirmed: false });
    await runtime.dismiss(); await advance(50);
    expect((await executing).status).toBe('error');
    expect(native.execute).not.toHaveBeenCalled();
    await runtime.dismiss(); await runtime.flush();
    const outcomes = log.events().flatMap(event => event.type === 'outcome' ? [event.data] : []);
    expect(outcomes).toHaveLength(1);
    expect(outcomes[0]?.payload).toMatchObject({ action: 'web_search', dismissed: true, accepted: false });
  });

  it('keeps a newer dismissal independent when an older native call finishes later', async () => {
    const native = nativeFixture();
    native.execute = async actionId => { await new Promise(resolve => setTimeout(resolve, 200)); return { status: 'executed', actionId, message: 'ok' }; };
    const { runtime, outcomes } = fixture(native);
    runtime.setInput('first'); await advance(80);
    const first = runtime.getSnapshot().decision!.id;
    const executing = runtime.execute('web_search', { primed: true, hold_ms: 0, confirmed: false });
    await runtime.dismiss(); runtime.setInput('second'); await advance(80);
    const second = runtime.getSnapshot().decision!.id;
    await runtime.dismiss(); await runtime.flush();
    expect(outcomes()).toHaveLength(1);
    expect(outcomes()[0]).toMatchObject({ request_id: second, payload: { dismissed: true } });
    await advance(120); await executing; await runtime.dismiss(); await runtime.flush();
    expect(outcomes()).toHaveLength(2);
    expect(outcomes()[1]).toMatchObject({ request_id: first, payload: { success: true, accepted: true } });
  });
});

describe('desktop model readiness', () => {
  it('allows a measured 94 second remote cold load with the 180 second override while keeping the active deadline', async () => {
    const requests: WireRequest[] = [];
    const backend = backendFixture(async request => {
      requests.push(request);
      if (requests.length === 1) await new Promise(resolve => setTimeout(resolve, 94000));
      if (JSON.parse(request.state).input === 'slow active') await new Promise(resolve => setTimeout(resolve, 2000));
      return response(request);
    });
    const native = nativeFixture(); native.execute = vi.fn(native.execute);
    const runtime = make({ native, backend, warmupTimeoutMs: 180000 });
    const opening = runtime.open(); await advance(1);
    runtime.setInput('latest input'); await advance(60001);
    expect(runtime.getSnapshot()).toMatchObject({ model: { status: 'warming', timeoutMs: 180000 } });
    expect(requests).toHaveLength(1);
    expect(JSON.parse(requests[0]!.state)).toMatchObject({ input: '', clipboard: { kind: 'empty' }, window: null });
    expect(JSON.parse(requests[0]!.state).clipboard).not.toHaveProperty('head');
    await advance(34000); await opening;
    expect(runtime.getSnapshot()).toMatchObject({ status: 'ready', input: 'latest input' });
    expect(native.execute).not.toHaveBeenCalled();
    runtime.setInput('slow active'); await advance(1581);
    expect(runtime.getSnapshot().errors.at(-1)).toMatchObject({ code: 'timeout', timeoutMs: 1500 });
  });

  it('deduplicates private-free cold warmup, keeps it alive across typing, and resumes only the latest input', async () => {
    const requests: WireRequest[] = []; const signals: AbortSignal[] = [];
    const backend = backendFixture(async request => {
      requests.push(request);
      if (requests.length === 1) await new Promise(resolve => setTimeout(resolve, 20000));
      return response(request);
    });
    const original = backend.decide;
    backend.decide = (request, options) => { signals.push(options.signal); return original(request, options); };
    const state = stateFixture(); state.recent = [{ path: '/home/test/private.txt', name: 'private.txt', score: 1 }];
    const native = nativeFixture(state); native.execute = vi.fn(native.execute);
    const runtime = make({ native, backend });
    const opening = runtime.open(); await advance(1);
    runtime.setInput('old'); await advance(90); runtime.setInput('new'); await advance(90);
    expect(runtime.getSnapshot()).toMatchObject({ status: 'warming', model: { status: 'warming', timeoutMs: 60000 } });
    expect(requests).toHaveLength(1);
    const warm = JSON.parse(requests[0]!.state);
    expect(warm).toMatchObject({ input: '', window: null, clipboard: { kind: 'empty' }, recent: [], candidates: [] });
    expect(warm.clipboard).not.toHaveProperty('head');
    expect(requests[0]!.state).not.toContain('private.txt');
    expect(signals[0]?.aborted).toBe(false);
    await advance(20000); await opening;
    expect(requests).toHaveLength(2);
    expect(JSON.parse(requests[1]!.state).input).toBe('new');
    expect(runtime.getSnapshot()).toMatchObject({ input: 'new', status: 'ready', model: { status: 'ready' } });
    expect(native.execute).not.toHaveBeenCalled();
  });

  it('aborts readiness on close and reports retryable WebKit timeout without treating supersession as an error', async () => {
    let aborted = false;
    const backend = backendFixture(request => response(request));
    backend.decide = (_req, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => { aborted = true; reject(new DOMException('Fetch is aborted', 'AbortError')); });
    });
    const runtime = make({ native: nativeFixture(), backend });
    const opening = runtime.open(); await advance(1); const closing = runtime.close(); await advance(1); await closing; await opening;
    expect(aborted).toBe(true); expect(runtime.getSnapshot().errors).toEqual([]);

    const warm = make({ native: nativeFixture(), timeoutMs: 300, backend: backendFixture(async request => {
      if (JSON.parse(request.state).input === 'slow') await new Promise(resolve => setTimeout(resolve, 500));
      return response(request);
    }) });
    await warm.open(); warm.setInput('slow'); await advance(381);
    expect(warm.getSnapshot().errors.at(-1)).toMatchObject({ code: 'timeout', retryable: true, timeoutMs: 300 });
    expect(warm.getSnapshot().metrics.timedOut).toBe(1);
  });
});

describe('readiness recovery and dismissal', () => {
  it('retains a single cold load across dismiss and reopen without publishing hidden or old input', async () => {
    const requests: WireRequest[] = [];
    const runtime = make({ native: nativeFixture(), backend: backendFixture(async request => {
      requests.push(request);
      if (requests.length === 1) await new Promise(resolve => setTimeout(resolve, 16000));
      return response(request);
    }) });
    const first = runtime.open(); await advance(1);
    runtime.setInput('hidden'); await advance(90); await runtime.dismiss();
    const second = runtime.open(); runtime.setInput('latest after reopen'); await advance(90);
    expect(requests).toHaveLength(1);
    await advance(16000); await Promise.all([first, second]);
    expect(requests).toHaveLength(2);
    expect(JSON.parse(requests[1]!.state).input).toBe('latest after reopen');
    expect(runtime.getSnapshot()).toMatchObject({ input: 'latest after reopen', status: 'ready' });
  });

  it('classifies generic WebKit aborts using their deadline reason and deduplicates repeated current timeouts', async () => {
    const backend = backendFixture(request => response(request));
    backend.decide = (request, { signal }) => JSON.parse(request.state).input === 'slow'
      ? new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('Fetch is aborted', 'AbortError'))))
      : Promise.resolve(response(request));
    const runtime = make({ native: nativeFixture(), backend, timeoutMs: 20 });
    await runtime.open();
    for (let i = 0; i < 2; i++) { runtime.setInput('slow'); await advance(101); }
    expect(runtime.getSnapshot().errors).toHaveLength(1);
    expect(runtime.getSnapshot().model.error).toMatchObject({ code: 'timeout', timeoutMs: 20, retryable: true });
    expect(runtime.getSnapshot().metrics.timedOut).toBe(2);
    runtime.setInput('slow'); await advance(90); runtime.setInput('replacement'); await advance(90);
    expect(runtime.getSnapshot()).toMatchObject({ status: 'ready', model: { status: 'ready' } });
    expect(runtime.getSnapshot().errors).toHaveLength(1);
  });

  it('exposes safe startup HTTP errors and retries the current input explicitly', async () => {
    let broken = true; const requests: WireRequest[] = [];
    const runtime = make({ native: nativeFixture(), backend: backendFixture(request => {
      requests.push(request); if (broken) throw new Error('http://localhost:11434: HTTP 503: private server body');
      return response(request);
    }) });
    await runtime.open();
    expect(runtime.getSnapshot().model).toMatchObject({ status: 'error', error: { code: 'http', httpStatus: 503, retryable: true } });
    expect(JSON.stringify(runtime.getSnapshot().errors)).not.toContain('private server body');
    runtime.setInput('current'); await advance(90); expect(requests).toHaveLength(1);
    expect(runtime.getSnapshot()).toMatchObject({ status: 'stale', model: { status: 'error' } });
    broken = false; await runtime.retry();
    expect(runtime.getSnapshot()).toMatchObject({ input: 'current', status: 'ready', model: { status: 'ready' } });
    expect(JSON.parse(requests.at(-1)!.state).input).toBe('current');
  });
});

describe('read-only builtin previews', () => {
  it('previews validated calc arguments without executing, discards old replies and preserves current preview on policy updates', async () => {
    const state = stateFixture([action('calc', 'expr', 'builtin', 'calc'), action('convert', 'input', 'builtin', 'convert'), action('other', 'none', 'none')]);
    const native = nativeFixture(state); native.execute = vi.fn(native.execute);
    native.preview = vi.fn(async (actionId, args) => {
      if (args.expr === '2+2') await new Promise(resolve => setTimeout(resolve, 200));
      return { actionId, text: args.expr === '2+2' ? '4' : '42' };
    });
    const policy = new Policy();
    const runtime = make({ native, policy, backend: backendFixture(request => response(request, 'calc')) });
    runtime.setInput('2+2'); await advance(80);
    expect(runtime.getSnapshot().candidates.find(c => c.action.id === 'calc')?.preview).toMatchObject({ status: 'pending' });
    runtime.setInput('7*6'); await advance(80);
    expect(runtime.getSnapshot().candidates.find(c => c.action.id === 'calc')?.preview).toEqual({ status: 'ready', text: '42' });
    await advance(200); policy.update({ bands: { act: .99 } });
    expect(runtime.getSnapshot().candidates.find(c => c.action.id === 'calc')?.preview).toEqual({ status: 'ready', text: '42' });
    expect(native.preview).toHaveBeenCalledTimes(2);
    expect(native.execute).not.toHaveBeenCalled();
    expect(runtime.getSnapshot().lastResult).toBeUndefined();
    expect(runtime.getSnapshot().metrics.launches).toBe(0);
  });

  it('surfaces native evaluation errors as preview errors while retaining the decision', async () => {
    const native = nativeFixture(stateFixture([action('calc', 'expr', 'builtin', 'calc'), action('other', 'none', 'none')]));
    native.preview = () => { throw new Error('result is not finite'); };
    const runtime = make({ native, backend: backendFixture(request => response(request, 'calc')) });
    runtime.setInput('1/0'); await advance(80);
    expect(runtime.getSnapshot().status).toBe('ready');
    expect(runtime.getSnapshot().candidates[0]?.preview).toEqual({ status: 'error', error: 'result is not finite' });
    expect(runtime.getSnapshot().errors).toEqual([]);
  });
});

it('keeps optional collector context warnings separate from runtime errors and refreshes them with state', async () => {
  const state = stateFixture();
  state.window = null; state.clipboard = { kind: 'private', head: '' };
  state.warnings = ['active window unavailable'];
  const runtime = make({ native: nativeFixture(state) });
  await runtime.open();
  expect(runtime.getSnapshot()).toMatchObject({ status: 'ready', warnings: ['active window unavailable'], model: { status: 'ready' } });
  expect(runtime.getSnapshot().errors).toEqual([]);
  state.warnings = [];
  runtime.setInput('search something'); await advance(80);
  expect(runtime.getSnapshot().warnings).toEqual([]);
});

describe('explicit installed app execution', () => {
  function appsFixture() {
    const state = stateFixture([action('open_app', 'app', 'app'), action('web_search'), action('other', 'none', 'none')]);
    state.apps = [{ id: 'brave-browser.desktop', name: 'Brave', genericName: 'Web Browser' }];
    const native = nativeFixture(state);
    native.getApps = vi.fn(async () => structuredClone(state.apps));
    native.executeApp = vi.fn(async () => ({ status: 'executed' as const, actionId: 'open_app', message: 'Opened application' }));
    return { state, native };
  }
  it.each(['slow', 'offline'] as const)('selects and executes the installed ID during %s model readiness without hold', async mode => {
    const { native } = appsFixture();
    const runtime = make({ native, backend: backendFixture(async request => {
      if (mode === 'offline') throw new Error('network failed');
      await new Promise(resolve => setTimeout(resolve, 1000)); return response(request);
    }) });
    const opening = runtime.open(); await advance(1);
    runtime.setInput('brave browser');
    expect(runtime.getSnapshot().appCandidates[0]?.app.id).toBe('brave-browser.desktop');
    expect((await runtime.executeApp('brave-browser.desktop')).status).toBe('executed');
    expect(native.executeApp).toHaveBeenCalledExactlyOnceWith('brave-browser.desktop');
    expect(runtime.getSnapshot().localAppMetrics).toEqual({ launches: 1, failures: 0 });
    expect(runtime.getSnapshot().metrics.launches).toBe(0);
    await advance(1000); await opening;
    expect(runtime.getSnapshot().status).toBe('idle');
  });
  it('refuses stale matches, concurrent/repeat dispatch, suspended runtimes and closed runtimes', async () => {
    const { native } = appsFixture();
    native.executeApp = vi.fn(async () => { await new Promise(resolve => setTimeout(resolve, 50)); return { status: 'executed' as const, actionId: 'open_app', message: 'Opened' }; });
    const runtime = make({ native }); await runtime.open();
    runtime.setInput('br'); runtime.setInput('kill brave');
    expect((await runtime.executeApp('brave-browser.desktop')).status).toBe('error');
    runtime.setInput('br');
    expect((await runtime.executeApp('/usr/bin/brave')).status).toBe('error');
    const first = runtime.executeApp('brave-browser.desktop');
    expect((await runtime.executeApp('brave-browser.desktop')).status).toBe('error');
    await advance(50); expect((await first).status).toBe('executed');
    expect((await runtime.executeApp('brave-browser.desktop')).status).toBe('error');
    await runtime.dismiss(); expect((await runtime.executeApp('brave-browser.desktop')).status).toBe('error');
    runtime.setInput('br'); await runtime.close(); expect((await runtime.executeApp('brave-browser.desktop')).status).toBe('error');
    expect(native.executeApp).toHaveBeenCalledTimes(1);
  });
  it('rejects a missing, guarded or rebound catalogue executor and preserves shell confirmation', async () => {
    for (const kind of ['guarded', 'shell', 'builtin', 'missing']) {
      const { native, state } = appsFixture();
      if (kind === 'missing') state.catalogue.actions = state.catalogue.actions.filter(a => a.id !== 'open_app');
      else if (kind === 'guarded') state.catalogue.actions[0]!.requiresConfirmation = true;
      else state.catalogue.actions[0]!.exec.kind = kind as 'shell' | 'builtin';
      const runtime = make({ native }); await runtime.open(); runtime.setInput('br');
      expect((await runtime.executeApp('brave-browser.desktop')).status, kind).toBe('error');
      expect(native.executeApp).not.toHaveBeenCalled();
    }
  });
  it('does not forge model outcomes or overwrite terminal outcomes when an app is opened and dismissed', async () => {
    const { native } = appsFixture(); const log = new RingLog();
    const bridge: BrowserBridge = { log, connect: async () => {}, flush: async () => {}, close: () => {}, status: () => ({ state: 'connected', pending: 0 }), onError: () => () => {}, onStatus: () => () => {} };
    const runtime = make({ native, bridge }); await runtime.open(); runtime.setInput('br'); await advance(80);
    const modelBefore = runtime.getSnapshot().metrics;
    await runtime.executeApp('brave-browser.desktop'); await runtime.dismiss(); await runtime.dismiss(); await runtime.flush();
    expect(log.events().filter(event => event.type === 'outcome')).toEqual([]);
    expect(runtime.getSnapshot().metrics).toEqual(modelBefore);
    expect(runtime.getSnapshot().localLastLaunch).toMatchObject({ appId: 'brave-browser.desktop', status: 'executed' });
  });
  it('refreshes removed app choices and returns safe failure feedback without recording acceptance', async () => {
    const { native, state } = appsFixture(); const runtime = make({ native }); await runtime.open(); runtime.setInput('br');
    state.apps = []; await runtime.open();
    expect(runtime.getSnapshot().appCandidates).toEqual([]);
    expect((await runtime.executeApp('brave-browser.desktop')).status).toBe('error');
    state.apps = [{ id: 'brave-browser.desktop', name: 'Brave' }]; await runtime.open(); runtime.setInput('br');
    native.executeApp = async () => { throw new Error('application changed: /private/path'); };
    const result = await runtime.executeApp('brave-browser.desktop');
    expect(result.message).toContain('Brave is no longer installed'); expect(result.message).not.toContain('/private/path');
    expect(runtime.getSnapshot().localAppMetrics).toEqual({ launches: 0, failures: 1 });
  });
});

describe('early explicit app selection', () => {
  it.each(['unchanged', 'changed'] as const)('captures the app while first native context is still pending: %s input', async mode => {
    const state = stateFixture([action('open_app', 'app', 'app'), action('other', 'none', 'none')]);
    state.apps = [{ id: 'brave-browser.desktop', name: 'Brave' }];
    const native = nativeFixture(state);
    native.getState = async () => { await new Promise(resolve => setTimeout(resolve, 50)); return structuredClone(state); };
    native.getApps = async () => structuredClone(state.apps);
    native.executeApp = vi.fn(async () => ({ status: 'executed' as const, actionId: 'open_app', message: 'Opened' }));
    const runtime = make({ native }); const opening = runtime.open(); await advance(1);
    runtime.setInput('br'); expect(runtime.getSnapshot().appCandidates).toHaveLength(1);
    const execution = runtime.executeApp('brave-browser.desktop');
    expect((await runtime.executeApp('brave-browser.desktop')).status).toBe('error');
    if (mode === 'changed') runtime.setInput('kill brave');
    await advance(49); const result = await execution; await opening;
    expect(result.status).toBe(mode === 'changed' ? 'error' : 'executed');
    expect(native.executeApp).toHaveBeenCalledTimes(mode === 'changed' ? 0 : 1);
  });
});

describe('error deduplication across recovery', () => {
  it.each([false, true])('counts distinct timeout occurrences with intervening recovery=%s', async recover => {
    const runtime = make({ native: nativeFixture(), timeoutMs: 100, backend: backendFixture(async request => {
      if (JSON.parse(request.state).input.startsWith('fail')) await new Promise(resolve => setTimeout(resolve, 150));
      return response(request);
    }) });
    await runtime.open();
    runtime.setInput('fail one'); await advance(80 + 101);
    expect(runtime.getSnapshot().errors.filter(error => error.code === 'timeout')).toHaveLength(1);
    if (recover) { runtime.setInput('success'); await advance(80); expect(runtime.getSnapshot().status).toBe('ready'); }
    runtime.setInput('fail two'); await advance(80 + 101);
    expect(runtime.getSnapshot().errors.filter(error => error.code === 'timeout')).toHaveLength(recover ? 2 : 1);
  });
});
