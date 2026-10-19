import type { Action, ExecArgs, NativeState } from './types';
import { matchInstalledApps } from './appmatching';

export function extractUrl(input: string): string | undefined {
  let value = input.trim().replace(/^(?:(?:open|visit|browse)(?:\s+to)?|go\s+to)\s+/i, '');
  if (!value || /[\s\u0000-\u001f\u007f\\<>"'`]/.test(value)) return undefined;
  const explicit = /^https?:\/\//i.test(value);
  if (explicit && (!/^https?:\/\/[^/?#]/i.test(value) || /^https?:\/\/[^/?#]*@/i.test(value))) return undefined;
  if (!explicit && /^[^/?#]+\.$/.test(value)) value = value.slice(0, -1);
  // Bare addresses must be the entire argument, with a dotted DNS host or IPv4.
  // Never recover a URL substring from another scheme, a file path or prose.
  if (!explicit && !/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9-]+(?::\d+)?(?:[/?#]|$)/i.test(value)) return undefined;
  try {
    const url = new URL(explicit ? value : `https://${value}`);
    const host = url.hostname;
    const labels = host.replace(/\.$/, '').split('.');
    const validHost = host.startsWith('[') || (host.length <= 253 && labels.every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label)));
    if (!validHost || !/^(https?:)$/.test(url.protocol) || url.username || url.password) return undefined;
    if (!explicit && !/^(?:[a-z]{2,}|xn--[a-z0-9-]+|\d+)$/i.test(labels.at(-1)!)) return undefined;
    return url.href;
  } catch { return undefined; }
}
export function extractExpression(input: string): string | undefined {
  const expr = input.trim().replace(/^(?:calc(?:ulate)?|what\s+is)\s+/i, '').replace(/[=?]\s*$/, '').trim();
  if (!expr || expr.length > 256 || !/^[\d\s.+\-*/%()^]+$/.test(expr) || !/\d/.test(expr)) return undefined;
  let depth = 0;
  for (const char of expr) {
    if (char === '(') depth++;
    if (char === ')' && --depth < 0) return undefined;
  }
  return depth === 0 ? expr : undefined;
}
export function extractDestination(input: string): string | undefined {
  let quote: string | undefined;
  for (let index = 0; index < input.length; index++) {
    const char = input[index]!;
    if (quote) { if (char === quote) quote = undefined; continue; }
    if ((char === '"' || char === "'") && (index === 0 || /\s/.test(input[index - 1]!))) { quote = char; continue; }
    if (!/\s/.test(char)) continue;
    // Find the first delimiter outside the quoted source. Slice the original
    // string, so Unicode case folding cannot change the destination offsets.
    const match = input.slice(index).match(/^\s+(?:to|as|into)\s+([\s\S]+)$/i);
    if (!match) continue;
    const raw = match[1]!.trim();
    if (raw.startsWith('"') || raw.startsWith("'")) return raw.length >= 2 && raw.endsWith(raw[0]!) ? raw.slice(1, -1) || undefined : undefined;
    return raw || undefined;
  }
  return undefined;
}
export function extractArgs(action: Action, input: string, state: NativeState, selectedFile?: string, resultText?: string): { args: ExecArgs; error?: string } {
  if (input.length > 4096 || input.includes('\0')) return { args: {}, error: 'Input exceeds the execution limit or contains NUL' };
  const source = action.exec.arg ?? 'none';
  const args: ExecArgs = {};
  const fail = (error: string) => ({ args: {}, error });
  if (source === 'input') args.input = input;
  else if (source === 'url') {
    const url = extractUrl(input);
    if (!url) return fail('Enter an http or https URL');
    args.url = url;
  } else if (source === 'expr') {
    const expr = extractExpression(input);
    if (!expr) return fail('Enter an arithmetic expression');
    args.expr = expr;
  } else if (source === 'file') {
    const files = /recent/.test(action.id) ? state.recent : state.candidates;
    const file = selectedFile ?? files[0]?.path;
    if (!file || !files.slice(0, 5).some(candidate => candidate.path === file) || file.includes('\0')) return fail('No matching file');
    args.file = file;
  } else if (source === 'clipboard') {
    if (state.clipboard.kind === 'private' || state.clipboard.kind === 'empty' || state.clipboard.kind === 'image') return fail('No available clipboard text');
    // Rust reads the full bounded, privacy-aware clipboard for every clipboard
    // action. The 200-character model-state head is not an execution payload.
  } else if (source === 'app' || source === 'appId' || action.exec.kind === 'app') {
    const app = input.trim() ? matchInstalledApps(input, state.apps, 1)[0]?.app : undefined;
    if (!app) return fail('No matching installed application');
    args.appId = app.id;
  } else if (source === 'text') {
    args.text = action.exec.name === 'copy_result' ? resultText : input;
    if (!args.text) return fail('No executed result is available to copy');
    if (args.text.length > 4096 || args.text.includes('\0')) return fail('Result exceeds the native text argument limit');
  } else if (!['none', 'process', 'workspace', 'seconds', 'host'].includes(source)) return fail(`Unsupported argument source: ${source}`);

  if (action.exec.kind === 'shell') args.input = input;
  const builtin = action.exec.name;
  if (builtin === 'timer' || action.id === 'set_timer') {
    const timers = [...input.matchAll(/(?:^|\s)(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b/gi)];
    if (!timers.length) return fail('Include a timer duration');
    const seconds = timers.reduce((total, timer) => total + Number(timer[1]) * (/^h/i.test(timer[2]!) ? 3600 : /^m/i.test(timer[2]!) ? 60 : 1), 0);
    if (!(Number.isSafeInteger(seconds) && seconds > 0 && seconds <= 86400)) return fail('Timer duration is out of range');
    args.seconds = seconds;
  }
  if (builtin === 'convert') {
    const match = input.trim().match(/^(?:convert\s+)?([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([a-z]+)\s+(?:to|in|into)\s+([a-z]+)\s*$/i);
    if (!match || !Number.isFinite(Number(match[1]))) return fail('Use a value and two units, such as 5 km to mi');
    const groups = [['m','meter','metre','km','cm','mm','in','inch','ft','feet','mi','mile'], ['kg','g','lb','lbs','oz'], ['s','sec','min','h','hr'], ['c','celsius','f','fahrenheit','k','kelvin'], ['b','kb','mb','gb','kib','mib']];
    const from = match[2]!.toLowerCase(), to = match[3]!.toLowerCase();
    if (!groups.some(group => group.includes(from) && group.includes(to))) return fail('Units are unsupported or incompatible');
    args.value = Number(match[1]); args.fromUnit = from; args.toUnit = to;
  }
  if (builtin === 'rename_file' || builtin === 'move_file') {
    const destination = extractDestination(input);
    if (!destination) return fail('Include a destination after to or as');
    if (builtin === 'rename_file' && (/[\\/]/.test(destination) || destination === '.' || destination === '..')) return fail('New name must be a single filename');
    if (builtin === 'move_file' && !destination.startsWith('/')) return fail('Use an absolute destination folder path');
    args.destination = destination;
  }
  if (builtin === 'kill_process' || source === 'process') {
    const name = input.replace(/^(?:kill|stop|terminate)(?:\s+process)?\s+/i, '').trim();
    if (!/^[a-z0-9_][a-z0-9_.-]{0,127}$/i.test(name)) return fail('Enter one exact process name');
    args.processName = name;
  }
  if (builtin === 'workspace' || source === 'workspace') {
    const match = input.trim().match(/^(?:(?:switch|go)(?:\s+to)?\s+)?(?:workspace\s+)?(\d+)$/i);
    const workspace = Number(match?.[1]);
    if (!Number.isInteger(workspace) || workspace < 1 || workspace > 20) return fail('Workspace must be 1 through 20');
    args.workspace = workspace;
  }
  if (builtin === 'ssh' || source === 'host') {
    const host = input.replace(/^(?:ssh|connect)(?:\s+to)?\s+/i, '').trim();
    if (!/^(?:[a-z0-9_][a-z0-9_.-]*@)?[a-z0-9_][a-z0-9_.-]*$/i.test(host) || host.length > 253) return fail('Enter a host, optionally user@host');
    args.host = host;
  }
  if (source === 'input' && !input.trim()) return fail('Enter text for this action');
  return { args };
}
