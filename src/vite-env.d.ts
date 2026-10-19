/// <reference types="vite/client" />

// Build-time decision backend defaults; see .env.example. All optional.
interface ImportMetaEnv {
  /** `ollama` (default) or `llamacpp`. */
  readonly VITE_DECISION_KIND?: string;
  /** Decision server origin, default `http://127.0.0.1:11434`. Non-loopback hosts need a CSP override. */
  readonly VITE_DECISION_URL?: string;
  /** Ollama model name, default `clef-flash-q8:latest`. */
  readonly VITE_DECISION_MODEL?: string;
  /** Short name for the default profile in settings, e.g. `my server`. */
  readonly VITE_DECISION_LABEL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
