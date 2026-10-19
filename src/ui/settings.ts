import { DEFAULT_LAYOUT, LAYOUT_LIMITS, type LayoutParams, type WidthMode } from '../lib/layout';

// User-tunable UI settings, persisted in localStorage. Values are clamped on
// load so a hand-edited or stale entry can never break the layout.

export interface UiSettings {
  widthMode: WidthMode;
  swapMargin: number;
  swapTicks: number;
  easeMs: number;
  minWidth: number;
  maxCards: number;
  debounceMs: number;
  showNumbers: boolean;
}

export const DEFAULT_SETTINGS: Readonly<UiSettings> = Object.freeze({
  widthMode: DEFAULT_LAYOUT.widthMode,
  swapMargin: DEFAULT_LAYOUT.swapMargin,
  swapTicks: DEFAULT_LAYOUT.swapTicks,
  easeMs: DEFAULT_LAYOUT.easeMs,
  minWidth: DEFAULT_LAYOUT.minWidth,
  maxCards: DEFAULT_LAYOUT.maxCards,
  debounceMs: 80,
  showNumbers: false,
});

export const DEBOUNCE_LIMITS = Object.freeze({ min: 40, max: 150, step: 5 });

const KEY = 'soft-launcher.ui.v1';

const clamp = (x: unknown, lo: number, hi: number, fallback: number): number =>
  typeof x === 'number' && Number.isFinite(x) ? Math.min(hi, Math.max(lo, x)) : fallback;

export function sanitiseSettings(raw: unknown): UiSettings {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const d = DEFAULT_SETTINGS;
  const L = LAYOUT_LIMITS;
  return {
    widthMode: r.widthMode === 'linear' ? 'linear' : r.widthMode === 'sqrt' ? 'sqrt' : d.widthMode,
    swapMargin: clamp(r.swapMargin, L.swapMargin.min, L.swapMargin.max, d.swapMargin),
    swapTicks: Math.round(clamp(r.swapTicks, L.swapTicks.min, L.swapTicks.max, d.swapTicks)),
    easeMs: Math.round(clamp(r.easeMs, L.easeMs.min, L.easeMs.max, d.easeMs)),
    minWidth: Math.round(clamp(r.minWidth, L.minWidth.min, L.minWidth.max, d.minWidth)),
    maxCards: Math.round(clamp(r.maxCards, L.maxCards.min, L.maxCards.max, d.maxCards)),
    debounceMs: Math.round(clamp(r.debounceMs, DEBOUNCE_LIMITS.min, DEBOUNCE_LIMITS.max, d.debounceMs)),
    showNumbers: typeof r.showNumbers === 'boolean' ? r.showNumbers : d.showNumbers,
  };
}

export function loadSettings(storage: Pick<Storage, 'getItem'> | undefined = globalThis.localStorage): UiSettings {
  try {
    const text = storage?.getItem(KEY);
    return sanitiseSettings(text ? JSON.parse(text) : {});
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(settings: UiSettings, storage: Pick<Storage, 'setItem'> | undefined = globalThis.localStorage): void {
  try {
    storage?.setItem(KEY, JSON.stringify(settings));
  } catch {
    // Private mode or quota: settings simply do not persist.
  }
}

export function layoutParams(settings: UiSettings, rowWidth: number): LayoutParams {
  return {
    ...DEFAULT_LAYOUT,
    widthMode: settings.widthMode,
    swapMargin: settings.swapMargin,
    swapTicks: settings.swapTicks,
    easeMs: settings.easeMs,
    minWidth: settings.minWidth,
    maxCards: settings.maxCards,
    rowWidth: Math.max(1, rowWidth),
  };
}
