import type { Action } from '../lib/types';

// Offline demo catalogue. It mirrors the plan's initial set so the browser demo
// exercises every card type. Keywords drive the demo backend only; the live
// launcher reads catalogue.toml through the native contract.

type Exec = Action['exec'];
interface DemoAction extends Action {
  keywords: string[];
}

const a = (id: string, description: string, exec: Exec, keywords: string[], extra: Partial<Action> = {}): DemoAction => ({
  id,
  description,
  exec,
  needsText: false,
  requiresConfirmation: false,
  keywords,
  ...extra,
});

export const DEMO_ACTIONS: DemoAction[] = [
  a('open_app', 'Launch an installed desktop application by name', { kind: 'app', arg: 'app' }, ['open', 'launch', 'start', 'app', 'firefox', 'terminal', 'kitty', 'code', 'spotify']),
  a('open_file', 'Open a file on disk by name', { kind: 'xdg-open', arg: 'file' }, ['file', 'pdf', 'doc', 'open', 'invoice', 'report', 'txt', 'png']),
  a('open_recent', 'Open a recently used or downloaded file', { kind: 'xdg-open', arg: 'file' }, ['recent', 'last', 'just', 'downloaded', 'download', 'yesterday', 'pdf']),
  a('web_search', 'Search the web for the input text', { kind: 'xdg-open', arg: 'input', name: 'web_search' }, ['search', 'google', 'how', 'what', 'who', 'why', 'best', 'the']),
  a('open_url', 'Open a web address in the browser', { kind: 'xdg-open', arg: 'url' }, ['http', 'https', 'www', '.com', '.org', 'url']),
  a('calc', 'Evaluate an arithmetic expression and show the result', { kind: 'builtin', name: 'calc', arg: 'expr' }, ['calc', '+', '*', '/', '=']),
  a('convert', 'Convert a quantity between units', { kind: 'builtin', name: 'convert', arg: 'input' }, ['convert', 'to', 'km', 'miles', 'kg', 'lb', 'celsius', 'fahrenheit']),
  a('copy_result', 'Copy the last result to the clipboard', { kind: 'builtin', name: 'copy_result', arg: 'none' }, ['copy', 'result']),
  a('paste_plain', 'Paste the clipboard as plain text', { kind: 'builtin', name: 'paste_plain', arg: 'clipboard' }, ['paste', 'plain', 'clipboard']),
  a('send_to_agent', 'Send the typed request to the local Hermes agent on this computer, after confirmation', { kind: 'hermes', arg: 'input' }, ['agent', 'hermes', 'ask', 'task', 'research', 'investigate', 'reply', 'draft'], { requiresConfirmation: true }),
  a('summarise_clipboard', 'Summarise clipboard text using Codex', { kind: 'codex', template: 'summarize', arg: 'clipboard' }, ['summarise', 'summarize', 'summary', 'tldr'], { needsText: true }),
  a('rename_file', 'Rename a file on disk', { kind: 'builtin', name: 'rename', arg: 'file' }, ['rename', 'name']),
  a('move_file', 'Move a file to another folder', { kind: 'builtin', name: 'move', arg: 'file' }, ['move', 'folder', 'mv']),
  a('kill_process', 'Terminate a running process by name', { kind: 'builtin', name: 'kill', arg: 'input' }, ['kill', 'stop', 'quit', 'process', 'frozen'], { requiresConfirmation: true }),
  a('toggle_wifi', 'Turn wifi on or off', { kind: 'builtin', name: 'wifi', arg: 'none' }, ['wifi', 'wireless', 'network']),
  a('lock_screen', 'Lock the screen', { kind: 'builtin', name: 'lock', arg: 'none' }, ['lock', 'away']),
  a('screenshot_region', 'Take a screenshot of a selected region', { kind: 'builtin', name: 'screenshot', arg: 'none' }, ['screenshot', 'screen', 'capture', 'grab']),
  a('switch_workspace', 'Switch to a Hyprland workspace by number', { kind: 'builtin', name: 'workspace', arg: 'input' }, ['workspace', 'desk', 'switch']),
  a('open_project', 'Open a project folder in the editor', { kind: 'app', name: 'editor', arg: 'file' }, ['project', 'repo', 'code', 'edit']),
  a('shell', 'Run a shell one-liner after explicit confirmation', { kind: 'shell', arg: 'input' }, ['$', 'run', 'sudo', 'ls', 'rm', 'git', 'systemctl', 'pacman', 'echo'], { requiresConfirmation: true }),
  a('define_word', 'Show the dictionary definition of a word', { kind: 'builtin', name: 'define', arg: 'input' }, ['define', 'meaning', 'definition', 'word']),
  a('translate_clipboard', 'Translate clipboard text into English using Codex', { kind: 'codex', template: 'translate', arg: 'clipboard' }, ['translate', 'english', 'german', 'french'], { needsText: true }),
  a('set_timer', 'Start a countdown timer', { kind: 'builtin', name: 'set_timer', arg: 'input' }, ['timer', 'minutes', 'min', 'seconds', 'remind', 'tea']),
  a('note_to_inbox', 'Append the input as a note to the inbox', { kind: 'builtin', name: 'note', arg: 'input' }, ['note', 'remember', 'todo', 'inbox', 'idea']),
  a('search_notes', 'Search personal notes for the input', { kind: 'builtin', name: 'search_notes', arg: 'input' }, ['notes', 'find', 'where', 'did', 'search']),
  a('media_play_pause', 'Play or pause media playback', { kind: 'builtin', name: 'media', arg: 'none' }, ['play', 'pause', 'music', 'song']),
  a('volume', 'Raise or lower the volume', { kind: 'builtin', name: 'volume', arg: 'input' }, ['volume', 'louder', 'quieter', 'mute', 'sound']),
  a('ssh_host', 'Open an ssh session to a known host', { kind: 'app', name: 'terminal', arg: 'input' }, ['ssh', 'remote', 'server', 'host']),
  a('clef_status', 'Show the configured Clef decision gateway status', { kind: 'builtin', name: 'clef_status', arg: 'none' }, ['clef', 'status', 'model', 'ollama']),
  a('other', 'None of the listed actions fit', { kind: 'none' }, []),
];

export function demoKeywords(id: string): readonly string[] {
  return DEMO_ACTIONS.find((x) => x.id === id)?.keywords ?? [];
}

export function stripKeywords(actions: readonly DemoAction[]): Action[] {
  return actions.map(({ keywords: _k, ...rest }) => rest);
}
