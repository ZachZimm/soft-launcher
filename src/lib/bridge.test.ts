import { expect, it, vi } from 'vitest';
import { createNativeBridge } from './bridge';

it('adapts the exact native invoke shapes including confirmation and proposal ID', async () => {
  const invoke = vi.fn(async () => ({}));
  const bridge = createNativeBridge(invoke as never);
  await bridge.getState('hello');
  await bridge.getApps?.();
  await bridge.executeApp?.('installed.desktop');
  await bridge.execute('run_shell', { input: 'pwd' }, 'token');
  await bridge.preview?.('calc', { expr: '2+2' });
  await bridge.reload(); await bridge.proposeTeaching(['one', 'two']);
  await bridge.saveTeaching('proposal', 'reviewed'); await bridge.hideWindow?.();
  expect(invoke.mock.calls).toEqual([
    ['get_state', { input: 'hello' }],
    ['get_apps'],
    ['execute', { request: { actionId: 'open_app', args: { appId: 'installed.desktop' }, expectedApp: true } }],
    ['execute', { request: { actionId: 'run_shell', args: { input: 'pwd' }, confirmationToken: 'token' } }],
    ['preview', { request: { actionId: 'calc', args: { expr: '2+2' } } }],
    ['reload'], ['propose_action', { inputs: ['one', 'two'] }],
    ['save_proposal', { proposalId: 'proposal', reviewedToml: 'reviewed' }], ['hide_window'],
  ]);
});
