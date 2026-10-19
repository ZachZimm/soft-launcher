import { describe, expect, it } from 'vitest';
import { createDemoNative } from './native';

describe('demo native agent handoff', () => {
  it('needs a confirmation token bound to the exact typed text, like native', async () => {
    const native = createDemoNative({ latencyMs: 0 });
    expect(native.catalogue().actions.some((a) => a.id === 'draft_reply')).toBe(false);
    const first = await native.execute('send_to_agent', { input: 'list my open PRs' });
    expect(first.status).toBe('confirmationRequired');
    await expect(native.execute('send_to_agent', { input: 'list my open PRs!' }, first.confirmationToken)).rejects.toThrow(/invalid or expired/);
    const again = await native.execute('send_to_agent', { input: 'list my open PRs' });
    const sent = await native.execute('send_to_agent', { input: 'list my open PRs' }, again.confirmationToken);
    expect(sent).toEqual({ status: 'executed', actionId: 'send_to_agent', message: 'Sent to Hermes Desktop' });
  });

  it('reports native error text when asked to fail', async () => {
    const native = createDemoNative({ latencyMs: 0, fail: ['hermes', 'codex'] });
    const first = await native.execute('send_to_agent', { input: 'hello' });
    await expect(native.execute('send_to_agent', { input: 'hello' }, first.confirmationToken)).rejects.toThrow('Hermes Desktop unavailable');
    await expect(native.execute('summarise_clipboard', {})).rejects.toThrow('Codex is unavailable');
  });
});
