import { fake } from 'soft-if';
import type { Answer, Backend, WireRequest, WireResponse } from 'soft-if';
import type { Action, NativeBridge, NativeState } from './types';

export const action = (id: string, arg = 'input', kind: Action['exec']['kind'] = 'builtin', name = id): Action => ({
  id, description: `Action ${id}`, exec: { kind, name, arg }, needsText: kind === 'bonsai', requiresConfirmation: kind === 'shell',
});
export function stateFixture(actions: Action[] = [action('web_search'), action('run_shell', 'input', 'shell'), action('other', 'none', 'none')]): NativeState {
  return {
    catalogue: { generation: 1, actions }, window: { class: 'terminal', title: 'work' },
    clipboard: { kind: 'text', head: 'hello clipboard' }, recent: [], candidates: [],
    apps: [{ id: 'firefox.desktop', name: 'Firefox' }], time: 'Sat 13:00, afternoon', warnings: [], filesIndexed: 0,
  };
}
export function response(request: WireRequest, leader = 'web_search', confidence = .94, fileKey = 'f0'): WireResponse {
  const answers: Record<string, Answer> = {};
  for (const [key, question] of Object.entries(request.questions)) {
    if (question.type === 'noul') {
      const p = key === 'is_file_search' && leader === 'open_file' ? .95 : .05;
      answers[key] = { type: 'noul', p, confidence: Math.max(p, 1 - p), probabilities: { true: p, false: 1 - p } };
    } else if (question.type === 'score') answers[key] = { type: 'score', score: 1, confidence: .8, probabilities: { 0: .1, 1: .8, 2: .1 } };
    else {
      const keys = Object.keys(question.criteria);
      const selected = key === 'file' ? fileKey : keys.includes(leader) ? leader : keys[0]!;
      answers[key] = { type: 'choice', choice: selected, confidence, probabilities: Object.fromEntries(keys.map(key => [key, key === selected ? .7 : .3 / (keys.length - 1)])) };
    }
  }
  return { answers, backend: 'fixture' };
}
export function backendFixture(respond?: (request: WireRequest) => WireResponse | Promise<WireResponse>): Backend {
  return fake({ respond: respond ?? (request => response(request)) });
}
export function nativeFixture(state = stateFixture()): NativeBridge {
  return {
    getState: async () => structuredClone(state),
    execute: async (actionId, _args, token) => actionId === 'run_shell' && !token
      ? { status: 'confirmationRequired', actionId, message: 'Confirm command', confirmationToken: 'secret-token', expiresInMs: 15000 }
      : { status: 'executed', actionId, message: 'Executed' },
    reload: async () => structuredClone(state.catalogue),
    proposeTeaching: async () => ({ proposalId: 'proposal-1', toml: '[[action]]\nid="new_action"', expiresInMs: 600000 }),
    saveTeaching: async () => structuredClone(state.catalogue),
  };
}
