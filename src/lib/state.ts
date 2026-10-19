import type { App, NativeState } from './types';
import { matchInstalledApps } from './appmatching';

// Conservative UTF-8 estimate. State stays strictly below the 1,500-token budget.
export const STATE_TOKEN_LIMIT = 1500;
export const estimateTokens = (value: string): number => Math.ceil(new TextEncoder().encode(value).length / 3);
const cap = (value: string, length: number): string => Array.from(value).slice(0, length).join('');
const basename = (value: string): string => value.split(/[\\/]/).pop() ?? '';
const PRIVATE_APP = /keepass|1password|bitwarden|lastpass|password|bank|vault/i;
function hash(value: string): string {
  // Stable opaque label. This is redaction, not a password hash.
  let result = 2166136261;
  for (const char of value) result = Math.imul(result ^ char.charCodeAt(0), 16777619);
  return (result >>> 0).toString(16).padStart(8, '0');
}
export const fileKey = (index: number): string => `f${index}`;
export interface DecisionState {
  input: string; window: { class: string; title: string } | null;
  clipboard: { kind: NativeState['clipboard']['kind']; head: string };
  recent: string[]; time: string;
  candidates: { key: string; name: string; location: string; score: number; recent: boolean; modifiedAt?: number; modified?: string; recencyRank?: number; isDirectory?: boolean }[];
}
export type ActionDecisionState = Omit<DecisionState, 'window' | 'clipboard'> & {
  window: { class: string } | null;
  clipboard: { kind: NativeState['clipboard']['kind'] };
  installedAppMatch?: boolean;
};

/** Typed intent selects the action. Ambient prose must not become a new task. */
export function buildActionState(input: string, snapshot: NativeState, knownApps?: readonly App[]): { state: ActionDecisionState; serialized: string; estimatedTokens: number } {
  const privateWindow = !!snapshot.window && PRIVATE_APP.test(`${snapshot.window.class} ${snapshot.window.title}`);
  // Remove prose before budgeting so unrelated ambient text cannot truncate the
  // typed request. Preserve private-window detection from the original snapshot.
  const built = buildBoundedState(input, {
    ...snapshot,
    window: snapshot.window ? { class: snapshot.window.class, title: '' } : null,
    clipboard: { kind: privateWindow ? 'private' : snapshot.clipboard.kind, head: '' },
  }, STATE_TOKEN_LIMIT - 16);
  const state: ActionDecisionState = {
    ...built.state,
    window: built.state.window ? { class: built.state.window.class } : null,
    clipboard: { kind: built.state.clipboard.kind },
    ...(knownApps ? { installedAppMatch: matchInstalledApps(input, knownApps, 1).length > 0 } : {}),
  };
  const serialized = JSON.stringify(state);
  return { state, serialized, estimatedTokens: estimateTokens(serialized) };
}

export function buildState(input: string, snapshot: NativeState): { state: DecisionState; serialized: string; estimatedTokens: number } {
  return buildBoundedState(input, snapshot, STATE_TOKEN_LIMIT);
}
function buildBoundedState(input: string, snapshot: NativeState, tokenLimit: number): { state: DecisionState; serialized: string; estimatedTokens: number } {
  const privateWindow = !!snapshot.window && PRIVATE_APP.test(`${snapshot.window.class} ${snapshot.window.title}`);
  const newest = snapshot.candidates.slice(0, 5).filter(file => Number.isFinite(file.modifiedAt)).sort((a, b) => b.modifiedAt! - a.modifiedAt!);
  const state: DecisionState = {
    input: cap(input, 700),
    window: snapshot.window ? {
      class: cap(snapshot.window.class, 80),
      title: privateWindow ? `[private:${hash(snapshot.window.title)}]` : cap(snapshot.window.title, 160),
    } : null,
    clipboard: {
      kind: privateWindow ? 'private' : snapshot.clipboard.kind,
      head: privateWindow || snapshot.clipboard.kind === 'private' ? '' : cap(snapshot.clipboard.head, 200),
    },
    recent: snapshot.recent.slice(0, 5).map(file => cap(basename(file.name || file.path), 80)),
    time: cap(snapshot.time, 80),
    candidates: snapshot.candidates.slice(0, 5).map((file, i) => ({
      key: fileKey(i), name: cap(basename(file.name || file.path), 100),
      location: /(?:^|[\\/])Downloads[\\/]/i.test(file.path) ? 'Downloads' : 'home',
      score: Number.isFinite(file.score) ? file.score : 0,
      recent: snapshot.recent.some(recent => recent.path === file.path),
      ...(typeof file.isDirectory === 'boolean' ? { isDirectory: file.isDirectory } : {}),
      ...(Number.isFinite(file.modifiedAt) && Math.abs(file.modifiedAt!) <= 8.64e15 ? { modifiedAt: file.modifiedAt, modified: new Date(file.modifiedAt!).toISOString(), recencyRank: newest.findIndex(item => item.path === file.path) + 1 } : {}),
    })),
  };
  let serialized = JSON.stringify(state);
  // Handles multi-byte pathological input without assuming chars == tokens.
  while (estimateTokens(serialized) >= tokenLimit) {
    if (state.input.length > 64) state.input = cap(state.input, Math.floor(Array.from(state.input).length * 0.75));
    else if (state.clipboard.head.length) state.clipboard.head = cap(state.clipboard.head, Math.floor(Array.from(state.clipboard.head).length / 2));
    else if (state.window && state.window.title.length > 32) state.window.title = cap(state.window.title, 32);
    else if (state.recent.length) state.recent.pop();
    else if (state.candidates.length) state.candidates.pop();
    else break;
    serialized = JSON.stringify(state);
  }
  return { state, serialized, estimatedTokens: estimateTokens(serialized) };
}
