import type { DecisionConfig } from './backendConfig';
import type { NativeEventHandlers } from './platform';
import type { UiSettings } from './settings';

/** Everything App.svelte needs from its host page (main.ts). */
export interface AppEnv {
  /** Running inside the Tauri overlay with the Rust backend. */
  native: boolean;
  /** Which decision backend the runtime uses. */
  model: 'live' | 'demo';
  backendLabel: string;
  decision: DecisionConfig;
  settings: UiSettings;
  hideWindow: () => Promise<void>;
  /** Native only: whether the window is currently shown. */
  isVisible?: () => Promise<boolean>;
  /** Native only: resize the window to this content height in CSS px. */
  fitWindow?: (height: number) => Promise<void>;
  /** Recreate the runtime with another decision backend. */
  restart: (decision: DecisionConfig) => void;
  /** Subscribe to native events; returns an unsubscribe. */
  listen?: (handlers: NativeEventHandlers) => Promise<() => void>;
}
