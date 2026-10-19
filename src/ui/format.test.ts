import { describe, expect, it } from 'vitest';
import { actionLabel, agentInputProblem, argSummary, busyText, describeError, errorCode, isGuarded, modelNotice, pct, tidyResult } from './format';
import type { Action } from '../lib/types';

const act = (over: Partial<Action> = {}): Action => ({ id: 'x', description: 'd', exec: { kind: 'builtin' }, needsText: false, requiresConfirmation: false, ...over });

describe('format', () => {
  it('labels action ids', () => {
    expect(actionLabel('open_recent')).toBe('Open recent');
    expect(actionLabel('calc')).toBe('Calculate');
  });

  it('summarises arguments for cards', () => {
    expect(argSummary(act(), { file: '/home/test/Downloads/a.pdf' })).toBe('a.pdf');
    expect(argSummary(act(), { url: 'https://example.com/' })).toBe('example.com');
    expect(argSummary(act(), { seconds: 300 })).toBe('5 min');
    expect(argSummary(act({ exec: { kind: 'shell' } }), { input: 'ls -la' })).toBe('$ ls -la');
    expect(argSummary(act({ exec: { kind: 'shell' } }), { input: '$ ls -la' })).toBe('$ ls -la');
    expect(argSummary(act(), {})).toBe('');
  });

  it('makes the agent and Codex text source legible', () => {
    const agent = act({ id: 'send_to_agent', exec: { kind: 'hermes', arg: 'input' } });
    const codex = act({ id: 'summarise_clipboard', exec: { kind: 'codex', arg: 'clipboard', template: 'summarize' } });
    expect(actionLabel('send_to_agent')).toBe('Send to agent');
    expect(argSummary(agent, { input: '  check the  build log ' })).toBe('“check the build log” → Hermes Desktop');
    expect(argSummary(agent, { input: 'x'.repeat(80) })).toMatch(/^“x{35}…” → Hermes Desktop$/);
    expect(argSummary(codex, {})).toBe('clipboard text → Codex');
    expect(busyText(agent, agent.id)).toBe('Sending to Hermes Desktop');
    expect(busyText(codex, codex.id)).toBe('Asking Codex');
    expect(busyText(act({ id: 'calc' }), 'calc')).toBe('Running Calculate');
  });

  it('always guards the agent handoff and checks its byte cap', () => {
    const agent = act({ id: 'send_to_agent', exec: { kind: 'hermes', arg: 'input' } });
    expect(isGuarded(agent)).toBe(true);
    expect(isGuarded(act({ exec: { kind: 'shell' } }))).toBe(true);
    expect(isGuarded(act())).toBe(false);
    expect(agentInputProblem(agent, { input: 'a'.repeat(4096) })).toBeUndefined();
    // 2,000 three-byte characters pass the 4,096-unit length cap but not native's byte cap.
    expect(agentInputProblem(agent, { input: '日'.repeat(2000) })).toBe('Too long for Hermes Desktop (over 4096 bytes)');
    expect(agentInputProblem(act(), { input: '日'.repeat(2000) })).toBeUndefined();
  });

  it('formats probabilities', () => {
    expect(pct(0.004)).toBe('<1%');
    expect(pct(0.456)).toBe('46%');
    expect(pct(1)).toBe('100%');
  });

  it('turns model failures into readable copy, never the raw abort text', () => {
    const at = 0;
    const raw = { source: 'decision', message: 'Fetch is aborted', at };
    expect(errorCode(raw)).toBe('timeout');
    expect(describeError(raw, '127.0.0.1:11434')).toBe('The decision model didn’t answer in time');
    expect(describeError({ ...raw, code: 'timeout', timeoutMs: 1500 }, 'h')).toBe('The decision model didn’t answer within 1.5 s');
    expect(describeError({ source: 'decision', message: 'Failed to fetch', at }, '127.0.0.1:11434')).toBe('Can’t reach the decision model at 127.0.0.1:11434. Is it running?');
    expect(describeError({ source: 'decision', message: 'x', at, code: 'http', httpStatus: 503 }, 'h')).toBe('The decision model returned HTTP 503');
    for (const err of [raw, { source: 'decision', message: 'Failed to fetch', at }]) expect(describeError(err, 'h')).not.toMatch(/abort|Failed to fetch/);
  });

  it('names the logger endpoint, never the decision host, when logging is unreachable', () => {
    for (const source of ['persistence', 'bridge']) {
      for (const err of [{ source, message: 'Failed to fetch', at: 0 }, { source, message: 'refused', at: 0, code: 'network' as const }]) {
        const text = describeError(err, 'decision.example.test:11434');
        expect(text).toBe('Can’t reach the soft-if logger at 127.0.0.1:7331');
        expect(text).not.toContain('decision.example.test');
      }
    }
  });

  it('describes loading and failed model states with a retry offer', () => {
    expect(modelNotice({ status: 'ready', timeoutMs: 1500 }, 'h', 0)).toBeNull();
    const warming = modelNotice({ status: 'warming', timeoutMs: 1500, since: 1000 }, 'h', 13_400);
    expect(warming).toMatchObject({ tone: 'busy', title: 'Loading the decision model · 12 s', retry: false });
    const failed = modelNotice({ status: 'error', timeoutMs: 1500, error: { source: 'decision', message: 'refused', at: 0, code: 'network', retryable: true } }, '127.0.0.1:11434', 0);
    expect(failed).toMatchObject({ tone: 'error', retry: true, detail: 'Start Ollama, then retry.' });
    const remote = modelNotice({ status: 'error', timeoutMs: 1500, error: { source: 'decision', message: 'refused', at: 0, code: 'network', retryable: true } }, 'decision.example.test:11434', 0);
    expect(remote?.detail).toBe('Check that the decision server at decision.example.test:11434 is up, then retry. Ctrl+comma switches backends.');
    expect(modelNotice({ status: 'error', timeoutMs: 1500, error: { source: 'decision', message: '', at: 0, code: 'cancelled' } }, 'h', 0)).toBeNull();
  });

  it('tidies native result text for cards', () => {
    expect(tidyResult('4')).toBe('4');
    expect(tidyResult('1000.000000 m')).toBe('1000 m');
    expect(tidyResult('3.106856 mi')).toBe('3.106856 mi');
    expect(tidyResult('2.500000 kg')).toBe('2.5 kg');
    expect(tidyResult('-40.000000 f')).toBe('-40 f');
    expect(tidyResult('0.5')).toBe('0.5');
  });
});
