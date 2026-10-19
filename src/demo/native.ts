import type { Catalogue, ExecArgs, ExecuteResult, FileCandidate, NativeBridge, NativeProposal, NativeState, Preview } from '../lib/types';
import { DEMO_ACTIONS, stripKeywords } from './catalogue';
import { DEMO_APPS } from './apps';

// Browser-only stand-in for the Rust backend. Nothing here touches the machine:
// every action is a dry run that describes what the native executor would do.
// It keeps the same safety shape as the real backend, so shell and other
// confirmation actions return a short-lived single-use token first.

const HOUR = 3_600_000;
const BASE = Date.UTC(2026, 9, 17, 18, 40);

const FILES: FileCandidate[] = [
  { path: '/home/synthetic/Downloads/invoice-2026-10.pdf', name: 'invoice-2026-10.pdf', score: 0, modifiedAt: BASE - 0.2 * HOUR },
  { path: '/home/synthetic/Downloads/hyprland-wiki.pdf', name: 'hyprland-wiki.pdf', score: 0, modifiedAt: BASE - 26 * HOUR },
  { path: '/home/synthetic/Documents/invoice-2026-09.pdf', name: 'invoice-2026-09.pdf', score: 0, modifiedAt: BASE - 720 * HOUR },
  { path: '/home/synthetic/notes/inbox.md', name: 'inbox.md', score: 0, modifiedAt: BASE - 2 * HOUR },
  { path: '/home/synthetic/projects/launcher/PLAN.md', name: 'PLAN.md', score: 0, modifiedAt: BASE - 5 * HOUR },
  { path: '/home/synthetic/Pictures/screenshot-2026-10-17.png', name: 'screenshot-2026-10-17.png', score: 0, modifiedAt: BASE - 1 * HOUR },
  { path: '/home/synthetic/Documents/report-q3.odt', name: 'report-q3.odt', score: 0, modifiedAt: BASE - 200 * HOUR },
  { path: '/home/synthetic/projects/model-server/README.md', name: 'README.md', score: 0, modifiedAt: BASE - 30 * HOUR },
];

/** Subsequence fuzzy score in [0, 1]; enough to rank the demo file list. */
export function fuzzy(query: string, target: string): number {
  const q = query.toLowerCase().replace(/\s+/g, '');
  const t = target.toLowerCase();
  if (!q) return 0;
  let ti = 0;
  let hits = 0;
  let run = 0;
  let best = 0;
  for (const ch of q) {
    const found = t.indexOf(ch, ti);
    if (found < 0) continue;
    hits++;
    run = found === ti ? run + 1 : 1;
    best = Math.max(best, run);
    ti = found + 1;
  }
  return (hits / q.length) * 0.7 + (best / q.length) * 0.3;
}

function candidatesFor(input: string): FileCandidate[] {
  const words = input.toLowerCase().split(/\s+/).filter((w) => w.length > 1 && !['the', 'i', 'my', 'a', 'open', 'just'].includes(w));
  if (words.length === 0) return [];
  return FILES.map((f) => ({ ...f, score: Math.max(...words.map((w) => fuzzy(w, f.name))) }))
    .filter((f) => f.score >= 0.6)
    .sort((x, y) => y.score - x.score || (y.modifiedAt ?? 0) - (x.modifiedAt ?? 0))
    .slice(0, 5);
}

function describe(actionId: string, args: ExecArgs): string {
  const show = (v: string) => `“${v.length > 60 ? `${v.slice(0, 57)}…` : v}”`;
  if (args.file) return `open ${args.file.replace('/home/synthetic', '~')}`;
  if (args.url) return `open ${args.url}`;
  if (args.expr) return `evaluate ${args.expr}`;
  if (args.appId) return `launch ${DEMO_APPS.find((app) => app.id === args.appId)?.name ?? args.appId}`;
  if (args.seconds) return `start a ${args.seconds}s timer`;
  if (args.text) return `use clipboard text ${show(args.text)}`;
  if (args.input) return `${actionId.replace(/_/g, ' ')} ${show(args.input)}`;
  return actionId.replace(/_/g, ' ');
}

/** Demo-only recursive-descent evaluator for + - * / % ^ and parentheses. No eval. */
export function safeCalc(expr: string): string | undefined {
  const src = expr.replace(/\s+/g, '');
  let i = 0;
  const peek = () => src[i];
  const atom = (): number => {
    if (peek() === '(') {
      i++;
      const v = sum();
      if (src[i++] !== ')') throw new Error('paren');
      return v;
    }
    if (peek() === '-') {
      i++;
      return -atom();
    }
    const m = /^\d+(?:\.\d+)?|^\.\d+/.exec(src.slice(i));
    if (!m) throw new Error('number');
    i += m[0].length;
    return Number(m[0]);
  };
  const power = (): number => {
    const base = atom();
    if (peek() === '^') {
      i++;
      return base ** power();
    }
    return base;
  };
  const product = (): number => {
    let v = power();
    while (peek() === '*' || peek() === '/' || peek() === '%') {
      const op = src[i++];
      const r = power();
      v = op === '*' ? v * r : op === '/' ? v / r : v % r;
    }
    return v;
  };
  const sum = (): number => {
    let v = product();
    while (peek() === '+' || peek() === '-') {
      const op = src[i++];
      const r = product();
      v = op === '+' ? v + r : v - r;
    }
    return v;
  };
  try {
    const value = sum();
    if (i !== src.length || !Number.isFinite(value)) return undefined;
    return String(Math.round(value * 1e10) / 1e10);
  } catch {
    return undefined;
  }
}

export interface DemoNativeOptions {
  /** Artificial native latency in ms, so loading states are visible. */
  latencyMs?: number;
  /** Executor kinds that fail with native's error text, to show error states (`?fail=hermes,codex`). */
  fail?: readonly string[];
}

export function createDemoNative(options: DemoNativeOptions = {}): NativeBridge & { catalogue(): Catalogue } {
  let catalogue: Catalogue = { generation: 1, actions: stripKeywords(DEMO_ACTIONS) };
  const tokens = new Map<string, { actionId: string; args: string; generation: number; expiresAt: number }>();
  const proposals = new Map<string, NativeProposal & { expiresAt: number }>();
  const delay = () => new Promise<void>((r) => setTimeout(r, options.latencyMs ?? 15));
  let seq = 0;

  return {
    catalogue: () => catalogue,
    async getState(input) {
      await delay();
      const state: NativeState = {
        catalogue,
        window: { class: 'kitty', title: 'synthetic@desktop: ~/projects' },
        clipboard: { kind: 'text', head: 'Can we move Thursday’s review to Friday afternoon? The draft is not ready.' },
        recent: [...FILES].sort((x, y) => (y.modifiedAt ?? 0) - (x.modifiedAt ?? 0)).slice(0, 5),
        candidates: candidatesFor(input),
        // Like native get_state: names and IDs only; icons come from getApps.
        apps: DEMO_APPS.map(({ id, name }) => ({ id, name })),
        time: 'Sat 11:40, morning',
        warnings: [],
        filesIndexed: FILES.length,
      };
      return state;
    },
    async getApps() {
      await delay();
      return structuredClone(DEMO_APPS);
    },
    async execute(actionId, args, confirmationToken) {
      await delay();
      const action = catalogue.actions.find((x) => x.id === actionId);
      if (!action) throw new Error(`Unknown action ${actionId}`);
      const key = JSON.stringify(args);
      if (action.requiresConfirmation || action.exec.kind === 'shell' || action.exec.kind === 'hermes') {
        if (!confirmationToken) {
          const token = `demo-${++seq}`;
          tokens.set(token, { actionId, args: key, generation: catalogue.generation, expiresAt: Date.now() + 15_000 });
          return {
            status: 'confirmationRequired',
            actionId,
            message: `Confirm: ${describe(actionId, args)}`,
            confirmationToken: token,
            expiresInMs: 15_000,
          } satisfies ExecuteResult;
        }
        const held = tokens.get(confirmationToken);
        tokens.delete(confirmationToken);
        if (!held || held.actionId !== actionId || held.args !== key || held.generation !== catalogue.generation || held.expiresAt < Date.now()) {
          throw new Error('Confirmation token is invalid or expired');
        }
      }
      if (action.exec.kind === 'hermes') {
        // Native hands the typed request to Hermes Desktop and acks once Desktop accepted a new conversation.
        if (options.fail?.includes('hermes')) throw new Error('Hermes Desktop unavailable');
        return { status: 'executed', actionId, message: 'Sent to Hermes Desktop' };
      }
      if (action.exec.kind === 'codex') {
        if (options.fail?.includes('codex')) throw new Error('Codex is unavailable');
        return {
          status: 'executed',
          actionId,
          message: 'Demo text (Codex is not called in the browser demo)',
          text:
            action.exec.template === 'translate'
              ? 'Can we move Thursday’s review to Friday afternoon? The draft is not ready.'
              : 'Asks to move Thursday’s review to Friday afternoon because the draft is not ready.',
        };
      }
      if (action.id === 'calc' && args.expr) {
        const value = safeCalc(args.expr);
        if (value === undefined) throw new Error('Could not evaluate the expression');
        return { status: 'executed', actionId, message: `${args.expr} = ${value}`, text: value };
      }
      return { status: 'executed', actionId, message: `Demo dry run: would ${describe(actionId, args)}` };
    },
    /** Read-only card result, like native preview: calc and convert only, never executes. */
    async preview(actionId, args): Promise<Preview> {
      await delay();
      if (actionId === 'calc' && args.expr) {
        const value = safeCalc(args.expr);
        if (value === undefined) throw new Error('Could not evaluate the expression');
        return { actionId, text: value };
      }
      if (actionId === 'convert' && args.value !== undefined && args.fromUnit && args.toUnit) {
        return { actionId, text: demoConvert(args.value, args.fromUnit, args.toUnit) };
      }
      throw new Error('No preview for this action');
    },
    async reload() {
      await delay();
      catalogue = { ...catalogue, generation: catalogue.generation + 1 };
      return catalogue;
    },
    async proposeTeaching(inputs) {
      await delay();
      if (inputs.length === 0) throw new Error('No unmatched inputs to learn from yet');
      const id = `proposal-${++seq}`;
      const sample = inputs.slice(-3).map((s) => s.replace(/"/g, "'"));
      const toml = [
        '[[action]]',
        'id = "open_bookmarks"',
        `description = "Open a saved bookmark by name, for inputs like: ${sample.join('; ')}"`,
        'exec = { kind = "xdg-open", arg = "input" }',
        'needs_text = false',
        '',
      ].join('\n');
      const proposal = { proposalId: id, toml, expiresInMs: 600_000, expiresAt: Date.now() + 600_000 };
      proposals.set(id, proposal);
      return { proposalId: id, toml, expiresInMs: proposal.expiresInMs };
    },
    async saveTeaching(proposalId, reviewedToml) {
      await delay();
      const proposal = proposals.get(proposalId);
      if (!proposal || proposal.expiresAt < Date.now()) throw new Error('Proposal expired; draft it again');
      const id = reviewedToml.match(/^\s*id\s*=\s*"([a-z][a-z0-9_]{1,47})"\s*$/m)?.[1];
      const description = reviewedToml.match(/^\s*description\s*=\s*"([^"\n]{4,300})"\s*$/m)?.[1];
      if (!/^\s*\[\[action\]\]/m.test(reviewedToml) || !id || !description) throw new Error('Reviewed TOML needs [[action]], an id and a description');
      if (catalogue.actions.some((x) => x.id === id)) throw new Error(`Action id "${id}" already exists`);
      proposals.delete(proposalId);
      const actions = [...catalogue.actions];
      actions.splice(actions.length - 1, 0, { id, description, exec: { kind: 'xdg-open', arg: 'input' }, needsText: false, requiresConfirmation: false });
      catalogue = { generation: catalogue.generation + 1, actions };
      return catalogue;
    },
    async hideWindow() {
      // The browser demo has no window to hide.
    },
  };
}

/** Demo unit table: [dimension, scale to base, offset]. Mirrors the native evaluator's shape. */
const UNITS: Record<string, [string, number, number]> = {
  m: ['len', 1, 0], meter: ['len', 1, 0], metre: ['len', 1, 0], km: ['len', 1000, 0], cm: ['len', 0.01, 0], mm: ['len', 0.001, 0],
  in: ['len', 0.0254, 0], inch: ['len', 0.0254, 0], ft: ['len', 0.3048, 0], feet: ['len', 0.3048, 0], mi: ['len', 1609.344, 0], mile: ['len', 1609.344, 0],
  kg: ['mass', 1, 0], g: ['mass', 0.001, 0], lb: ['mass', 0.45359237, 0], lbs: ['mass', 0.45359237, 0], oz: ['mass', 0.028349523125, 0],
  s: ['time', 1, 0], sec: ['time', 1, 0], min: ['time', 60, 0], h: ['time', 3600, 0], hr: ['time', 3600, 0],
  c: ['temp', 1, 273.15], celsius: ['temp', 1, 273.15], k: ['temp', 1, 0], kelvin: ['temp', 1, 0],
  f: ['temp', 5 / 9, 273.15 - (32 * 5) / 9], fahrenheit: ['temp', 5 / 9, 273.15 - (32 * 5) / 9],
};

function demoConvert(value: number, from: string, to: string): string {
  const a = UNITS[from.toLowerCase()];
  const b = UNITS[to.toLowerCase()];
  if (!a || !b || a[0] !== b[0]) throw new Error('incompatible units or invalid value');
  return `${((value * a[1] + a[2] - b[2]) / b[1]).toFixed(6)} ${to}`;
}
