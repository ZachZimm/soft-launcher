import { afterEach, describe, expect, it, vi } from 'vitest';
import { DECISION_PRESETS, DEFAULT_DECISION, LOCAL_IS_DISTINCT, describeDecision, loadDecision, migrateDecision, presetLabel, presetOf, saveDecision, sameDecision, warmupTimeoutFor } from './backendConfig';

const KEY = 'soft-launcher.decision.v1';
const MIGRATED = 'soft-launcher.decision.migrated.v2';

function memory(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

describe('decision backend config', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('defaults to Clef through Ollama on this computer without build configuration', () => {
    expect(DEFAULT_DECISION).toEqual({ kind: 'ollama', url: 'http://127.0.0.1:11434', model: 'clef-flash-q8:latest' });
    expect(loadDecision(memory())).toEqual(DEFAULT_DECISION);
    expect(DECISION_PRESETS.local).toEqual(DEFAULT_DECISION);
    expect(LOCAL_IS_DISTINCT).toBe(false);
    expect(presetOf(DEFAULT_DECISION)).toBe('default');
    expect(presetLabel('default')).toBe('Clef on this computer');
    expect(describeDecision(DEFAULT_DECISION)).toBe('clef-flash-q8:latest @ 127.0.0.1:11434 (this computer)');
  });

  it('takes the default server, model and label from VITE_DECISION_* at build time', async () => {
    vi.stubEnv('VITE_DECISION_KIND', 'ollama');
    vi.stubEnv('VITE_DECISION_URL', 'http://decision.example.test:11434');
    vi.stubEnv('VITE_DECISION_MODEL', 'clef-flash-q4:latest');
    vi.stubEnv('VITE_DECISION_LABEL', 'build server');
    vi.resetModules();
    const m = await import('./backendConfig');
    expect(m.DEFAULT_DECISION).toEqual({ kind: 'ollama', url: 'http://decision.example.test:11434', model: 'clef-flash-q4:latest' });
    expect(m.LOCAL_IS_DISTINCT).toBe(true);
    expect(m.presetOf(m.DECISION_PRESETS.local)).toBe('local');
    expect(m.presetLabel('default')).toBe('Clef on build server');
    expect(m.describeDecision(m.DEFAULT_DECISION)).toBe('clef-flash-q4:latest @ decision.example.test:11434 (build server)');
    expect(m.warmupTimeoutFor(m.DEFAULT_DECISION)).toBe(180_000);

    vi.stubEnv('VITE_DECISION_LABEL', '');
    vi.resetModules();
    expect((await import('./backendConfig')).presetLabel('default')).toBe('Clef on configured server');
  });

  it('with a configured remote default, migrates a legacy local profile once and then keeps a deliberate local choice', async () => {
    vi.stubEnv('VITE_DECISION_URL', 'http://decision.example.test:11434');
    vi.resetModules();
    const m = await import('./backendConfig');
    const s = memory({ [KEY]: JSON.stringify({ kind: 'ollama', url: 'http://localhost:11434', model: 'clef-flash-q8:latest' }) });
    expect(m.migrateDecision(s)).toBe('migrated');
    expect(m.loadDecision(s)).toEqual(m.DEFAULT_DECISION);
    m.saveDecision({ ...m.DECISION_PRESETS.local }, s);
    expect(m.migrateDecision(s)).toBe('none');
    expect(m.loadDecision(s)).toEqual(m.DECISION_PRESETS.local);
  });

  it('gives a remote server a longer cold-load budget than this computer', () => {
    expect(warmupTimeoutFor({ ...DECISION_PRESETS.local, url: 'http://decision.example.test:11434' })).toBe(180_000);
    expect(warmupTimeoutFor(DECISION_PRESETS.local)).toBeUndefined();
    expect(warmupTimeoutFor({ ...DECISION_PRESETS.local, url: 'http://localhost:11434' })).toBeUndefined();
  });

  it('treats the /v1/systemone suffix as the same endpoint', () => {
    expect(sameDecision({ ...DEFAULT_DECISION, url: 'http://decision.example.test:11434/v1/systemone' }, { ...DEFAULT_DECISION, url: 'http://decision.example.test:11434' })).toBe(true);
    expect(presetOf({ kind: 'ollama', url: 'http://127.0.0.1:11434/v1/systemone', model: 'clef-flash-q8:latest' })).toBe('default');
    expect(presetOf({ kind: 'ollama', url: 'http://127.0.0.1:11434', model: 'other:latest' })).toBeNull();
  });

  it('moves a stored legacy on-this-computer default to the build default once', () => {
    for (const url of ['http://127.0.0.1:11434', 'http://localhost:11434', 'http://127.0.0.1:11434/v1/systemone']) {
      const s = memory({ [KEY]: JSON.stringify({ kind: 'ollama', url, model: 'clef-flash-q8:latest' }) });
      expect(migrateDecision(s)).toBe('migrated');
      expect(s.data.has(KEY)).toBe(false);
      expect(loadDecision(s)).toEqual(DEFAULT_DECISION);
      expect(migrateDecision(s)).toBe('none');
    }
  });

  it('keeps custom profiles and a later deliberate switch back to this computer', () => {
    const custom = { kind: 'llamacpp', url: 'http://decision.example.test:8080', model: 'clef-flash-q8:latest' };
    const s = memory({ [KEY]: JSON.stringify(custom) });
    expect(migrateDecision(s)).toBe('kept');
    expect(loadDecision(s)).toEqual(custom);

    const otherModel = memory({ [KEY]: JSON.stringify({ kind: 'ollama', url: 'http://127.0.0.1:11434', model: 'clef-flash-q4:latest' }) });
    expect(migrateDecision(otherModel)).toBe('kept');
    expect(loadDecision(otherModel).model).toBe('clef-flash-q4:latest');

    const fresh = memory();
    expect(migrateDecision(fresh)).toBe('none');
    expect(fresh.data.get(MIGRATED)).toBe('1');
    saveDecision({ ...DECISION_PRESETS.local }, fresh);
    expect(migrateDecision(fresh)).toBe('none');
    expect(loadDecision(fresh)).toEqual(DECISION_PRESETS.local);
    saveDecision({ ...DEFAULT_DECISION }, fresh);
    expect(fresh.data.has(KEY)).toBe(false);
  });
});
