import { describe, expect, it } from 'vitest';
import { extractArgs, extractDestination, extractExpression, extractUrl } from './args';
import { action, stateFixture } from './core-test-fixtures';
import { catalogueFixture } from '../../bench/fixtures';

describe('safe argument extraction', () => {
  it('resolves installed desktop name plus generic name without a model command', () => {
    const state = stateFixture();
    // Public metadata read from the installed desktop entry. The display name
    // is Brave, while the user naturally includes its GenericName, Web Browser.
    state.apps = [{ id: 'brave-browser.desktop', name: 'Brave', genericName: 'Web Browser' }];
    for (const input of ['br', 'brave browser', 'open brave', '  BRAVE   Browser  ']) {
      expect(extractArgs(action('open_app', 'app', 'app'), input, state), input).toEqual({ args: { appId: 'brave-browser.desktop' } });
    }
  });
  it('accepts http URLs and rejects scripts, credentials and shell-derived URLs', () => {
    expect(extractUrl('open https://example.org/path?a=2')).toBe('https://example.org/path?a=2');
    expect(extractUrl('www.example.org.')).toBe('https://www.example.org/');
    for (const value of ['javascript:alert(1)', 'file:///etc/passwd', 'https://user:secret@example.org']) expect(extractUrl(value)).toBeUndefined();
  });
  it('normalizes whole domain/path inputs without treating search phrases or file paths as URLs', () => {
    const cases = [
      ['openrouter.ai', 'https://openrouter.ai/'],
      ['  example.org/docs?q=launcher#setup  ', 'https://example.org/docs?q=launcher#setup'],
      ['open docs.example.org:8443/a%20b', 'https://docs.example.org:8443/a%20b'],
      ['visit www.example.org', 'https://www.example.org/'],
      ['http://example.org:8080/path?x=1', 'http://example.org:8080/path?x=1'],
      ['https://example.org/?q=why?', 'https://example.org/?q=why?'],
      ['https://localhost:3000/', 'https://localhost:3000/'],
      ['http://[::1]:8080/path', 'http://[::1]:8080/path'],
    ];
    for (const [input, expected] of cases) expect(extractUrl(input!), input).toBe(expected);
    for (const input of [
      'open router ai', 'search example.org weather', 'example.org with spaces',
      '/tmp/example.org', './example.org', '../example.org', '~/example.org', 'C:\\example.org',
      'javascript:openrouter.ai', 'javascript:https://example.org', 'file://example.org',
      'ftp://example.org', 'data:text/html,https://example.org', '//example.org',
      'https:example.org', 'https:///example.org', 'https://example.org\\evil',
      'https://example.org/\nnext', 'https://example.org/\u0001',
      'user@example.org', 'https://user:secret@example.org', 'https://@example.org', 'example..org',
      '-example.org', 'example-.org', 'example.org:99999', 'example.org:bad',
      'https://-example.org', 'https://example..org', 'https://example.org..', 'https://', 'localhost/path',
    ]) expect(extractUrl(input), input).toBeUndefined();
  });
  it('never evaluates an expression or accepts arbitrary code', () => {
    expect(extractExpression('what is (12 + 5) * 2?')).toBe('(12 + 5) * 2');
    for (const value of ['process.exit()', '1 + (() => 2)()', '(2+1', '2+1)', '']) expect(extractExpression(value)).toBeUndefined();
  });
  it('only takes files from the code-generated candidate set', () => {
    const state = stateFixture();
    state.candidates = [{ path: '/home/test/report.pdf', name: 'report.pdf', score: 1 }];
    expect(extractArgs(action('open_file', 'file'), 'report', state).args.file).toBe('/home/test/report.pdf');
    expect(extractArgs(action('open_file', 'file'), 'report', state, '/etc/passwd').error).toBeTruthy();
  });
  it('parses rename destinations outside quoted sources without changing Unicode offsets', () => {
    const examples = [
      ['rename "old to use.txt" to new.txt', 'new.txt'],
      ["rename 'old into use.txt' as 'new to use.txt'", 'new to use.txt'],
      ['rename old.txt to new to name.txt', 'new to name.txt'],
      ['rename İ.txt to New.txt', 'New.txt'],
      ['rename "İ to use.txt" to "New as name.txt"', 'New as name.txt'],
      ['rename old.txt as new as name.txt', 'new as name.txt'],
      ['move "old to use.txt" into "/home/test/to folder"', '/home/test/to folder'],
      ["rename John's.txt to new.txt", 'new.txt'],
    ];
    for (const [input, expected] of examples) expect(extractDestination(input!), input).toBe(expected);
    expect(extractDestination('rename "old to use.txt to new.txt')).toBeUndefined();
    expect(extractDestination('rename old.txt to "new.txt')).toBeUndefined();
    const state = stateFixture(); state.candidates = [{ path: '/home/test/old to use.txt', name: 'old to use.txt', score: 1 }];
    expect(extractArgs(action('rename_file', 'file'), 'rename "old to use.txt" to new.txt', state).args).toEqual({ file: '/home/test/old to use.txt', destination: 'new.txt' });
  });
  it('handles every structured catalogue argument without model-generated arguments', () => {
    const state = stateFixture();
    state.candidates = [{ path: '/home/test/old.txt', name: 'old.txt', score: 1 }];
    const cases = [
      [action('convert'), 'convert 5 km to mi', { input: 'convert 5 km to mi', value: 5, fromUnit: 'km', toUnit: 'mi' }],
      [action('rename_file', 'file'), 'rename old.txt to "new name.txt"', { file: '/home/test/old.txt', destination: 'new name.txt' }],
      [action('move_to_folder', 'file', 'builtin', 'move_file'), 'move old.txt to /home/test/archive', { file: '/home/test/old.txt', destination: '/home/test/archive' }],
      [action('kill_process', 'process'), 'kill process firefox', { processName: 'firefox' }],
      [action('switch_workspace', 'workspace', 'builtin', 'workspace'), 'switch to workspace 4', { workspace: 4 }],
      [action('ssh_to_host', 'host', 'builtin', 'ssh'), 'ssh user@buildbox.local', { host: 'user@buildbox.local' }],
      [action('set_timer', 'seconds', 'builtin', 'timer'), 'timer 1 hour 2 minutes 3 seconds', { seconds: 3723 }],
      [action('open_app', 'app', 'app'), 'open Firefox', { appId: 'firefox.desktop' }],
      [action('draft_reply', 'clipboard', 'bonsai'), 'reply', {}],
    ] as const;
    for (const [candidate, input, expected] of cases) expect(extractArgs(candidate, input, state), input).toEqual({ args: expected });
    expect(extractArgs(action('copy_result', 'text'), 'copy result', state, undefined, '42')).toEqual({ args: { text: '42' } });
    expect(extractArgs(action('copy_result', 'text'), 'copy result', state).error).toBeTruthy();
  });
  it('rejects missing, ambiguous and out-of-range structured arguments', () => {
    const cases = [
      [action('convert'), '5 kg to km'], [action('kill_process', 'process'), 'kill foo; rm -rf /'],
      [action('switch_workspace', 'workspace', 'builtin', 'workspace'), 'workspace 21'],
      [action('ssh_to_host', 'host', 'builtin', 'ssh'), 'ssh -oProxyCommand=bad'],
      [action('set_timer', 'seconds'), 'timer .5 seconds'],
      [action('open_app', 'app', 'app'), 'open nonexistent'],
    ] as const;
    for (const [candidate, input] of cases) expect(extractArgs(candidate, input, stateFixture()).error, input).toBeTruthy();
    expect(extractArgs(action('draft_reply', 'clipboard'), '', { ...stateFixture(), clipboard: { kind: 'private', head: 'secret' } }).error).toBeTruthy();
  });
  it('produces required native fields for every one of the actual 31 catalogue actions', async () => {
    const actions = await catalogueFixture(); const state = stateFixture(actions);
    state.candidates = [{ path: '/home/test/report.pdf', name: 'report.pdf', score: 1 }]; state.recent = state.candidates;
    const samples: Record<string, [string, string[]]> = {
      open_app: ['open Firefox', ['appId']], open_file: ['report.pdf', ['file']], open_recent: ['recent file', ['file']],
      web_search: ['search for mountains', ['input']], open_url: ['https://example.org', ['url']], calc: ['2+2', ['expr']],
      convert: ['5 km to mi', ['value','fromUnit','toUnit']], copy_result: ['copy result', ['text']], paste_plain: ['paste plain', []],
      send_to_agent: ['ask the agent to check the build log', ['input']], summarise_clipboard: ['summarise clipboard', []],
      rename_file: ['rename report.pdf to report-new.pdf', ['file','destination']], move_to_folder: ['move report.pdf to /home/test/archive', ['file','destination']],
      kill_process: ['kill firefox', ['processName']], toggle_wifi: ['toggle wifi', []], lock_screen: ['lock screen', []], screenshot_region: ['screenshot region', []],
      switch_workspace: ['workspace 3', ['workspace']], open_project: ['report.pdf', ['file']], run_shell: ['printf hello', ['input']],
      define_word: ['define probability', ['input']], translate_clipboard: ['translate clipboard', []], set_timer: ['timer 5 minutes', ['seconds']],
      note_to_inbox: ['note buy milk', ['input']], search_notes: ['search notes milk', ['input']], media_play_pause: ['pause music', []],
      volume_up: ['volume up', []], volume_down: ['volume down', []], ssh_to_host: ['ssh user@buildbox', ['host']], clef_status: ['clef status', []], other: ['something else', []],
    };
    expect(actions).toHaveLength(31); expect(Object.keys(samples)).toHaveLength(31);
    for (const candidate of actions) {
      const [input, fields] = samples[candidate.id]!;
      const result = extractArgs(candidate, input, state, undefined, '42');
      expect(result.error, candidate.id).toBeUndefined();
      for (const field of fields) expect(result.args, candidate.id).toHaveProperty(field);
    }
  });
});
