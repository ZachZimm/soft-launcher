import type { Action, CoreError, ExecArgs, ModelStatus } from '../lib/types';

const SPECIAL: Record<string, string> = { calc: 'Calculate', ssh_host: 'SSH to host', clef_status: 'Clef status', open_url: 'Open URL' };

/** Short card title from an action id: "open_recent" -> "Open recent". */
export function actionLabel(id: string): string {
  if (SPECIAL[id]) return SPECIAL[id]!;
  const words = id.replace(/[_-]+/g, ' ').trim();
  return words ? words[0]!.toUpperCase() + words.slice(1) : id;
}

const basename = (p: string) => p.split('/').filter(Boolean).pop() ?? p;

function duration(seconds: number): string {
  if (seconds % 3600 === 0) return `${seconds / 3600} h`;
  if (seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds} s`;
}

/** Native's cap for text handed to Hermes Desktop, in UTF-8 bytes (args.ts caps UTF-16 units). */
export const AGENT_INPUT_MAX_BYTES = 4096;

export const utf8Length = (text: string): number => new TextEncoder().encode(text).length;

/** Hands the typed input to local Hermes Desktop as a new conversation. Always behind native confirmation, never primed. */
export const isAgentAction = (action: Action): boolean => action.exec.kind === 'hermes';

/** Cards that must not run on a single Enter: native asks for a separate confirmation first. */
export const isGuarded = (action: Action): boolean => action.requiresConfirmation || action.exec.kind === 'shell' || isAgentAction(action);

/** Why an agent card cannot send this input, if it cannot. */
export function agentInputProblem(action: Action, args: ExecArgs): string | undefined {
  if (!isAgentAction(action) || args.input === undefined) return undefined;
  return utf8Length(args.input) > AGENT_INPUT_MAX_BYTES ? `Too long for Hermes Desktop (over ${AGENT_INPUT_MAX_BYTES} bytes)` : undefined;
}

const clip = (text: string, max: number): string => {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
};

/** Where send_to_agent's text goes, in user words. */
export const AGENT_DESTINATION = 'Hermes Desktop on this computer';

/** The pending line while an action runs, without the trailing ellipsis. */
export function busyText(action: Action | undefined, id: string): string {
  if (action && isAgentAction(action)) return 'Sending to Hermes Desktop';
  if (action?.exec.kind === 'codex') return 'Asking Codex';
  return `Running ${actionLabel(id)}`;
}

/** One-line description of the code-extracted arguments, for the card body. */
export function argSummary(action: Action, args: ExecArgs): string {
  if (isAgentAction(action)) return args.input?.trim() ? `“${clip(args.input, 36)}” → Hermes Desktop` : '';
  if (action.exec.kind === 'codex') return action.exec.arg === 'clipboard' ? 'clipboard text → Codex' : args.input?.trim() ? `“${clip(args.input, 40)}” → Codex` : '';
  if (args.file) return basename(args.file);
  if (args.url) {
    try {
      const u = new URL(args.url);
      return u.host + (u.pathname === '/' ? '' : u.pathname);
    } catch {
      return args.url;
    }
  }
  if (args.expr) return args.expr;
  if (args.value !== undefined && args.fromUnit && args.toUnit) return `${args.value} ${args.fromUnit} → ${args.toUnit}`;
  if (args.appId) return args.appId.replace(/\.desktop$/, '');
  if (args.seconds) return duration(args.seconds);
  if (args.text) return action.id === 'copy_result' ? `“${args.text.slice(0, 40)}”` : 'clipboard text';
  if (action.exec.kind === 'shell' && args.input) return args.input.trimStart().startsWith('$') ? args.input.trim() : `$ ${args.input.trim()}`;
  return '';
}

export const pct = (p: number): string => (p >= 0.995 ? '100%' : p < 0.005 ? '<1%' : `${Math.round(p * 100)}%`);

const seconds = (ms: number): string => (ms < 10_000 ? `${Math.round(ms / 100) / 10} s` : `${Math.round(ms / 1000)} s`);

/** The error's code, inferring one from the raw message for errors that predate codes. */
export function errorCode(error: CoreError): NonNullable<CoreError['code']> {
  if (error.code) return error.code;
  const m = error.message;
  if (/abort|timed? ?out|deadline/i.test(m)) return 'timeout';
  if (/failed to fetch|networkerror|load failed|connection refused|econnrefused/i.test(m)) return 'network';
  if (/\bHTTP\s?\d{3}\b|status \d{3}/i.test(m)) return 'http';
  return 'unknown';
}

/** One readable sentence for an error. Raw messages stay in tooltips only. */
export function describeError(error: CoreError, host: string): string {
  const what = error.source === 'decision' || error.source === 'model' ? 'The decision model' : sourceLabel(error.source);
  switch (errorCode(error)) {
    case 'timeout':
      return `${what} didn’t answer${error.timeoutMs ? ` within ${seconds(error.timeoutMs)}` : ' in time'}`;
    case 'network':
      // Persistence errors come from soft-if logging through the bridge, not the decision host.
      return error.source === 'bridge' || error.source === 'persistence'
        ? `Can’t reach the soft-if logger at 127.0.0.1:7331`
        : `Can’t reach ${what.toLowerCase()} at ${host}. Is it running?`;
    case 'http':
      return `${what} returned HTTP ${error.httpStatus ?? 'error'}`;
    case 'invalid-response':
      return `${what} sent an answer the launcher couldn’t read`;
    case 'cancelled':
      return `${what} request was cancelled`;
    default:
      return `${what}: ${error.message}`;
  }
}

function sourceLabel(source: string): string {
  if (source === 'bridge') return 'Logging';
  if (source === 'window') return 'Window placement';
  if (source === 'execution') return 'The action';
  return source ? source[0]!.toUpperCase() + source.slice(1) : 'Launcher';
}

const isLocalHost = (host: string): boolean => /^(127\.\d+\.\d+\.\d+|localhost|\[::1\])(:\d+)?$/i.test(host);

export interface ModelNotice {
  tone: 'busy' | 'error';
  title: string;
  detail?: string;
  retry: boolean;
}

/** What to show in place of cards while the model is loading or unreachable. */
export function modelNotice(model: ModelStatus, host: string, now: number): ModelNotice | null {
  if (model.status === 'warming') {
    const waited = model.since ? Math.max(0, now - model.since) : 0;
    return {
      tone: 'busy',
      title: `Loading the decision model${waited >= 1000 ? ` · ${Math.floor(waited / 1000)} s` : '…'}`,
      detail: isLocalHost(host) ? 'The first answer after the model unloads can take about 20 s.' : 'A cold load on a remote server can take about a minute and a half.',
      retry: false,
    };
  }
  if (model.status === 'error' && model.error && errorCode(model.error) !== 'cancelled') {
    const code = errorCode(model.error);
    return {
      tone: 'error',
      title: describeError({ ...model.error, source: 'decision' }, host),
      detail:
        code === 'network'
          ? isLocalHost(host)
            ? 'Start Ollama, then retry.'
            : `Check that the decision server at ${host} is up, then retry. Ctrl+comma switches backends.`
          : code === 'timeout'
            ? 'It may still be loading. Retry, or keep typing.'
            : undefined,
      retry: model.error.retryable !== false,
    };
  }
  return null;
}

/** Native results print fixed decimals ("1000.000000 m"); show them the way a person would ("1000 m"). */
export function tidyResult(text: string): string {
  return text.trim().replace(/^(-?\d+)\.(\d*?)0+(?=\s|$)/, (_, int: string, frac: string) => (frac ? `${int}.${frac}` : int));
}
