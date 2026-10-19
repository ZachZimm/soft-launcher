import { createBrowserBridge, Policy } from 'soft-if';
import type { BrowserBridge, NativeBridge } from './types';

export type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
export function createNativeBridge(invoke: Invoke): NativeBridge {
  return {
    getApps: () => invoke('get_apps'),
    executeApp: appId => invoke('execute', { request: { actionId: 'open_app', args: { appId }, expectedApp: true } }),
    getState: input => invoke('get_state', { input }),
    execute: (actionId, args, confirmationToken) => invoke('execute', { request: { actionId, args, ...(confirmationToken ? { confirmationToken } : {}) } }),
    preview: (actionId, args) => invoke('preview', { request: { actionId, args } }),
    reload: () => invoke('reload'),
    proposeTeaching: inputs => invoke('propose_action', { inputs }),
    saveTeaching: (proposalId, reviewedToml) => invoke('save_proposal', { proposalId, reviewedToml }),
    hideWindow: () => invoke('hide_window'),
  };
}
export function createIntegrationBridge(policy: Policy, url = 'http://localhost:7331'): BrowserBridge {
  return createBrowserBridge({ url, policy });
}
