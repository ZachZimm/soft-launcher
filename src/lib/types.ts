import type { Answer, Backend, Band, ChoiceAnswer, ChoiceQuestion, Decision, LogSink, NoulAnswer, NoulQuestion, Policy, ScoreAnswer, ScoreQuestion } from 'soft-if';

export type { Band } from 'soft-if';
export interface Action {
  id: string;
  description: string;
  exec: { kind: 'xdg-open' | 'builtin' | 'app' | 'shell' | 'hermes' | 'codex' | 'bonsai' | 'none'; name?: string; arg?: string; template?: string };
  needsText: boolean;
  requiresConfirmation: boolean;
}
export interface Catalogue { generation: number; actions: Action[] }
export interface FileCandidate { path: string; name: string; score: number; modifiedAt?: number; isDirectory?: boolean }
export interface App { id: string; name: string; genericName?: string; keywords?: string[]; icon?: string }
/** Code-derived installed targets. These are not model probabilities or policy bands. */
export interface InstalledAppCandidate { app: App; match: 'exact' | 'prefix' | 'terms'; score: number; matchedField?: 'name' | 'genericName' | 'keywords' | 'id' }
export interface ExecArgs {
  input?: string; file?: string; appId?: string; url?: string; expr?: string; text?: string;
  destination?: string; workspace?: number; processName?: string; host?: string;
  seconds?: number; value?: number; fromUnit?: string; toUnit?: string;
}
export interface NativeState {
  catalogue: Catalogue;
  window: { class: string; title: string } | null;
  clipboard: { kind: 'empty' | 'text' | 'url' | 'image' | 'private'; head: string };
  recent: FileCandidate[]; candidates: FileCandidate[]; apps: App[];
  time: string; warnings: string[]; filesIndexed: number;
}
export interface ExecuteResult {
  status: 'executed' | 'confirmationRequired'; actionId: string; message: string;
  text?: string; confirmationToken?: string; expiresInMs?: number;
}
export interface Preview { actionId: string; text: string }
export interface NativeProposal { proposalId: string; toml: string; expiresInMs: number }
export interface NativeBridge {
  getApps?(): Promise<App[]>;
  executeApp?(appId: string): Promise<ExecuteResult>;
  getState(input: string): Promise<NativeState>;
  execute(actionId: string, args: ExecArgs, confirmationToken?: string): Promise<ExecuteResult>;
  preview?(actionId: string, args: ExecArgs): Promise<Preview>;
  reload(): Promise<Catalogue>;
  proposeTeaching(inputs: string[]): Promise<NativeProposal>;
  saveTeaching(proposalId: string, reviewedToml: string): Promise<Catalogue>;
  hideWindow?(): Promise<void>;
}
export interface LauncherQuestions {
  [key: string]: ChoiceQuestion | NoulQuestion | ScoreQuestion;
  action: ChoiceQuestion;
  is_file_search: NoulQuestion; is_calc: NoulQuestion; is_url: NoulQuestion;
  wants_paste: NoulQuestion; is_question: NoulQuestion; is_gibberish: NoulQuestion;
  urgency: ScoreQuestion;
}
export type LauncherDecision = Decision<LauncherQuestions> & {
  action: ChoiceAnswer; is_file_search: NoulAnswer; is_calc: NoulAnswer;
  is_url: NoulAnswer; wants_paste: NoulAnswer; is_question: NoulAnswer;
  is_gibberish: NoulAnswer; urgency: ScoreAnswer;
};
export interface Candidate {
  action: Action; probability: number; confidence: number; band: Band;
  args: ExecArgs; argumentError?: string; argumentPending?: boolean; fileConfidence?: number;
  preview?: { status: 'pending' | 'ready' | 'error'; text?: string; error?: string };
}
export interface BridgeStatus { state: 'idle' | 'connecting' | 'connected' | 'error' | 'closed'; pending: number; error?: string }
export interface BrowserBridge {
  log: LogSink;
  connect(): Promise<void>; flush(): Promise<void>; close(): void;
  status(): BridgeStatus;
  onStatus(listener: (status: BridgeStatus) => void): () => void;
  onError(listener: (error: Error) => void): () => void;
}
export interface TeachingProposal extends NativeProposal { id: string }
export interface TeachingSnapshot {
  available: boolean; otherStreak: number; inputs: string[];
  proposal?: TeachingProposal; pending: boolean; error?: string;
}
export interface CoreError {
  source: string; message: string; at: number;
  code?: 'timeout' | 'network' | 'http' | 'invalid-response' | 'cancelled' | 'unknown';
  retryable?: boolean; timeoutMs?: number; httpStatus?: number;
}
export interface ModelStatus {
  status: 'idle' | 'warming' | 'ready' | 'error' | 'closed';
  timeoutMs: number; since?: number; error?: CoreError;
}
export interface MetricsSnapshot {
  decisions: number; latency_p50_ms: number; latency_p95_ms: number;
  timedOut: number; superseded: number; other_rate: number;
  launches: number; dismissals: number; primed_acceptance: number | null;
  misfires_per_100: number; ece: number | null;
}
export interface RuntimeSnapshot {
  tick: number; input: string;
  status: 'idle' | 'warming' | 'debouncing' | 'deciding' | 'ready' | 'stale' | 'closed';
  stale: boolean; candidates: Candidate[]; decision?: LauncherDecision;
  appCandidates: InstalledAppCandidate[];
  localAppMetrics: { launches: number; failures: number };
  localLastLaunch?: { appId: string; name: string; status: 'executed' | 'error'; at: number; launchId: string };
  signals?: { is_file_search: number; is_calc: number; is_url: number; wants_paste: number; is_question: number; is_gibberish: number; urgency: number };
  teaching: TeachingSnapshot; bridge: BridgeStatus; errors: CoreError[];
  /** Optional context that collectors could not provide; these do not block decisions. */
  warnings: string[];
  model: ModelStatus;
  metrics: MetricsSnapshot; catalogue: Catalogue; debounceMs: number;
  lastResult?: { actionId: string; text: string; outcomeId: string };
}
export interface ExecutionOptions { primed: boolean; hold_ms: number; confirmed: boolean }
export interface ExecutionResponse {
  status: 'executed' | 'confirmationRequired' | 'error'; actionId: string;
  message: string; text?: string; args: ExecArgs; expiresInMs?: number; outcomeId?: string;
}
export interface RuntimeOptions {
  native: NativeBridge; backend?: Backend; bridge?: BrowserBridge | null;
  bridgeUrl?: string; policy?: Policy; debounceMs?: number; timeoutMs?: number; warmupTimeoutMs?: number;
  now?: () => number;
}
export interface LauncherRuntime {
  subscribe(listener: (snapshot: RuntimeSnapshot) => void): () => void;
  getSnapshot(): RuntimeSnapshot;
  open(): Promise<void>; retry(): Promise<void>; setInput(input: string): void; setDebounce(ms: number): void;
  execute(actionId: string, options: ExecutionOptions): Promise<ExecutionResponse>;
  executeApp(appId: string): Promise<ExecutionResponse>;
  dismiss(): Promise<void>; markUndone(outcomeId: string): boolean;
  cancelConfirmation(): void;
  proposeTeaching(): Promise<TeachingProposal>;
  saveTeaching(proposalId: string, reviewedToml: string): Promise<Catalogue>;
  reload(): Promise<Catalogue>; reportNativeError(source: string, message: string): void;
  flush(): Promise<void>; close(): Promise<void>;
}

export type DecisionAnswer = Answer;
