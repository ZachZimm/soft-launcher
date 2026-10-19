import { llamacpp, ollama, type Backend } from 'soft-if';

// Decision backend selection for the webview. Defaults come from build-time Vite
// env vars (VITE_DECISION_KIND / VITE_DECISION_URL / VITE_DECISION_MODEL /
// VITE_DECISION_LABEL; see .env.example) and can be overridden at runtime from
// the settings panel, stored in localStorage. No API keys exist on this path:
// Clef and llama.cpp System One endpoints are keyless. Free text (Codex) and
// agent handoff (Hermes) are handled by the Rust side.
//
// Without env vars the default is Clef through Ollama on this computer. The Tauri
// CSP allows only loopback decision hosts; any other host needs an exact-origin
// CSP override at build time (src-tauri/README.md).

export type DecisionKind = 'ollama' | 'llamacpp';

export interface DecisionConfig {
  kind: DecisionKind;
  url: string;
  model: string;
}

const KEY = 'soft-launcher.decision.v1';
/** Set once the stored profile has been checked against the legacy local default. */
const MIGRATED_KEY = 'soft-launcher.decision.migrated.v2';

const CLEF_MODEL = 'clef-flash-q8:latest';

/** soft-if appends /v1/systemone, so a URL with or without it names the same endpoint. */
const endpoint = (url: string): string => url.replace(/\/v1\/systemone$/, '');

export function sameDecision(a: DecisionConfig, b: DecisionConfig): boolean {
  return a.kind === b.kind && endpoint(a.url) === endpoint(b.url) && (a.kind === 'llamacpp' || a.model === b.model);
}

const LOCAL_DECISION: Readonly<DecisionConfig> = Object.freeze({ kind: 'ollama', url: 'http://127.0.0.1:11434', model: CLEF_MODEL });

export const DEFAULT_DECISION: Readonly<DecisionConfig> = Object.freeze({
  kind: import.meta.env.VITE_DECISION_KIND === 'llamacpp' ? 'llamacpp' : 'ollama',
  url: import.meta.env.VITE_DECISION_URL?.trim() || LOCAL_DECISION.url,
  model: import.meta.env.VITE_DECISION_MODEL?.trim() || CLEF_MODEL,
});

/** Named profiles offered in settings: the build default and Ollama on this computer. */
export const DECISION_PRESETS = Object.freeze({
  default: DEFAULT_DECISION,
  local: LOCAL_DECISION,
});

/** Whether "this computer" is a separate profile from the build default. */
export const LOCAL_IS_DISTINCT = !sameDecision(DEFAULT_DECISION, LOCAL_DECISION);

const isLoopback = (url: string): boolean => {
  try {
    return /^(127\.\d+\.\d+\.\d+|localhost|\[::1\])$/i.test(new URL(url).hostname);
  } catch {
    return false;
  }
};

/** Short name for the build default, e.g. "this computer" or VITE_DECISION_LABEL. */
export const DEFAULT_LABEL: string =
  import.meta.env.VITE_DECISION_LABEL?.trim() || (LOCAL_IS_DISTINCT ? 'configured server' : 'this computer');

/** Settings label for a profile, or null for a custom one. */
export function presetLabel(name: keyof typeof DECISION_PRESETS | null): string | null {
  if (name === 'default') return `Clef on ${DEFAULT_LABEL}`;
  if (name === 'local') return 'Clef on this computer';
  return null;
}

/** Which named profile a config is, if any. */
export function presetOf(config: DecisionConfig): keyof typeof DECISION_PRESETS | null {
  if (sameDecision(config, DECISION_PRESETS.default)) return 'default';
  if (sameDecision(config, DECISION_PRESETS.local)) return 'local';
  return null;
}

/**
 * Earlier builds defaulted to Clef through Ollama on this computer. A profile
 * saved with exactly that value (normally only by an explicit Apply) follows the
 * build default once; any other saved profile is the user's choice and stays.
 */
const LEGACY_LOCAL_DEFAULTS: readonly DecisionConfig[] = [
  { kind: 'ollama', url: 'http://127.0.0.1:11434', model: CLEF_MODEL },
  { kind: 'ollama', url: 'http://localhost:11434', model: CLEF_MODEL },
];

export type MigrationResult = 'none' | 'migrated' | 'kept';

/** One-time move of a stored legacy-local-default profile to the build default. Idempotent. */
export function migrateDecision(storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | undefined = globalThis.localStorage): MigrationResult {
  try {
    if (!storage || storage.getItem(MIGRATED_KEY)) return 'none';
    const text = storage.getItem(KEY);
    let result: MigrationResult = 'none';
    if (text) {
      let raw: unknown;
      try {
        raw = JSON.parse(text);
      } catch {
        raw = undefined;
      }
      const stored = sanitiseDecision(raw);
      if (raw && LEGACY_LOCAL_DEFAULTS.some((old) => sameDecision(stored, old))) {
        storage.removeItem(KEY);
        result = 'migrated';
      } else result = 'kept';
    }
    storage.setItem(MIGRATED_KEY, '1');
    return result;
  } catch {
    return 'none';
  }
}

export function sanitiseDecision(raw: unknown): DecisionConfig {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const kind: DecisionKind = r.kind === 'llamacpp' ? 'llamacpp' : r.kind === 'ollama' ? 'ollama' : DEFAULT_DECISION.kind;
  let url = DEFAULT_DECISION.url;
  if (typeof r.url === 'string') {
    try {
      const u = new URL(r.url.trim());
      if ((u.protocol === 'http:' || u.protocol === 'https:') && !u.username && !u.password) url = u.origin + u.pathname.replace(/\/+$/, '');
    } catch {
      // keep default
    }
  }
  const model = typeof r.model === 'string' && /^[\w.:/-]{1,128}$/.test(r.model.trim()) ? r.model.trim() : DEFAULT_DECISION.model;
  return { kind, url, model };
}

export function loadDecision(storage: Pick<Storage, 'getItem'> | undefined = globalThis.localStorage): DecisionConfig {
  try {
    const text = storage?.getItem(KEY);
    return sanitiseDecision(text ? JSON.parse(text) : DEFAULT_DECISION);
  } catch {
    return { ...DEFAULT_DECISION };
  }
}

export function saveDecision(config: DecisionConfig, storage: Pick<Storage, 'setItem' | 'removeItem'> | undefined = globalThis.localStorage): void {
  try {
    if (sameDecision(config, DEFAULT_DECISION)) storage?.removeItem(KEY);
    else storage?.setItem(KEY, JSON.stringify(config));
  } catch {
    // not persisted
  }
}

/**
 * Readiness budget for the first decision. A remote server may load Clef from
 * network storage; a measured cold load took 94 s, past the runtime’s 60 s
 * default, so allow 180 s for any non-loopback host.
 */
export function warmupTimeoutFor(config: DecisionConfig): number | undefined {
  try {
    new URL(config.url);
  } catch {
    return undefined;
  }
  return isLoopback(config.url) ? undefined : 180_000;
}

export function createDecisionBackend(config: DecisionConfig): Backend {
  return config.kind === 'llamacpp' ? llamacpp({ url: config.url, name: 'llamacpp' }) : ollama({ url: config.url, model: config.model, name: 'ollama' });
}

export function describeDecision(config: DecisionConfig): string {
  const host = (() => {
    try {
      return new URL(config.url).host;
    } catch {
      return config.url;
    }
  })();
  const preset = presetOf(config);
  const where = preset === 'default' ? ` (${DEFAULT_LABEL})` : preset === 'local' ? ' (this computer)' : '';
  return config.kind === 'llamacpp' ? `llama.cpp @ ${host}` : `${config.model} @ ${host}${where}`;
}
