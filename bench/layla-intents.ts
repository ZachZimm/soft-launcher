// Synthetic, hand-labelled launcher intents for the Laya experiment.
// `expected` is the single best catalogue action; `accept` widens the set of
// defensible answers for ambiguous inputs. Noul labels are derived in
// `nouls()` only where the expected answer is unambiguous.
export type Category = 'app' | 'file' | 'calc' | 'convert' | 'web' | 'text' | 'system' | 'media' | 'misc' | 'other' | 'ambiguous';
export interface Intent { input: string; category: Category; expected: string; accept?: string[]; question?: boolean; gibberish?: boolean; original?: boolean }

export const intents: Intent[] = [
  // The twenty inputs from bench/live.ts (Clef's existing measurement).
  { input: 'open firefox', category: 'app', expected: 'open_app', original: true },
  { input: 'search for local hiking routes', category: 'web', expected: 'web_search', original: true },
  { input: '12 * 17 + 4', category: 'calc', expected: 'calc', original: true },
  { input: 'open https://example.org', category: 'web', expected: 'open_url', original: true },
  { input: 'set a timer for 5 minutes', category: 'misc', expected: 'set_timer', original: true },
  { input: 'switch to workspace 3', category: 'system', expected: 'switch_workspace', original: true },
  { input: 'define probability', category: 'web', expected: 'define_word', original: true },
  { input: 'draft a reply', category: 'text', expected: 'draft_reply', original: true },
  { input: 'play music', category: 'media', expected: 'media_play_pause', original: true },
  { input: 'ssh user@buildbox', category: 'misc', expected: 'ssh_to_host', original: true },
  { input: 'lock the screen', category: 'system', expected: 'lock_screen', original: true },
  { input: 'increase volume', category: 'media', expected: 'volume_up', original: true },
  { input: 'note buy coffee', category: 'misc', expected: 'note_to_inbox', original: true },
  { input: 'copy result', category: 'text', expected: 'copy_result', original: true },
  { input: 'summarise clipboard', category: 'text', expected: 'summarise_clipboard', original: true },
  { input: 'what is the weather', category: 'ambiguous', expected: 'web_search', accept: ['other'], question: true, original: true },
  { input: 'zzqxwv nonsense', category: 'other', expected: 'other', gibberish: true, original: true },
  { input: 'open project editor', category: 'app', expected: 'open_project', accept: ['open_app'], original: true },
  { input: 'find meeting notes', category: 'ambiguous', expected: 'open_recent', accept: ['open_file', 'search_notes'], original: true },
  { input: 'take a screenshot', category: 'system', expected: 'screenshot_region', original: true },
  // Apps
  { input: 'launch spotify', category: 'app', expected: 'open_app' },
  { input: 'gimp', category: 'app', expected: 'open_app' },
  { input: 'open the terminal', category: 'app', expected: 'open_app' },
  { input: 'start thunderbird', category: 'app', expected: 'open_app' },
  { input: 'open soft-launcher project in vscode', category: 'app', expected: 'open_project' },
  // Files
  { input: 'open taxes-2025.pdf', category: 'file', expected: 'open_file' },
  { input: 'open the quarterly budget worksheet', category: 'file', expected: 'open_recent', accept: ['open_file'] },
  { input: 'reopen the file I had open earlier', category: 'file', expected: 'open_recent' },
  { input: 'rename report.pdf to final-report.pdf', category: 'file', expected: 'rename_file' },
  { input: 'move invoice.pdf into the archive folder', category: 'file', expected: 'move_to_folder' },
  { input: 'find my resume docx', category: 'file', expected: 'open_file' },
  // Calculation
  { input: '2+2', category: 'calc', expected: 'calc' },
  { input: '(3.5 + 4) / 2', category: 'calc', expected: 'calc' },
  { input: '1024 / 8', category: 'calc', expected: 'calc' },
  { input: 'what is 15% of 240', category: 'calc', expected: 'calc', question: true },
  { input: '7 times 8', category: 'calc', expected: 'calc' },
  // Unit conversion
  { input: '5 miles in km', category: 'convert', expected: 'convert' },
  { input: 'convert 70 f to c', category: 'convert', expected: 'convert' },
  { input: '3 cups in ml', category: 'convert', expected: 'convert' },
  { input: '2 GB to MB', category: 'convert', expected: 'convert' },
  { input: 'how many ounces in a pound', category: 'convert', expected: 'convert', question: true },
  // Web
  { input: 'github.com', category: 'web', expected: 'open_url' },
  { input: 'best ramen near me', category: 'web', expected: 'web_search' },
  { input: 'what does ephemeral mean', category: 'web', expected: 'define_word', question: true },
  { input: 'search rust borrow checker errors', category: 'web', expected: 'web_search' },
  // Clipboard and text
  { input: 'translate this to english', category: 'text', expected: 'translate_clipboard' },
  { input: 'paste as plain text', category: 'text', expected: 'paste_plain' },
  { input: 'tldr of what I copied', category: 'text', expected: 'summarise_clipboard' },
  { input: 'write a quick response to this email', category: 'text', expected: 'draft_reply' },
  { input: 'copy that answer', category: 'text', expected: 'copy_result' },
  // System
  { input: 'kill firefox', category: 'system', expected: 'kill_process' },
  { input: 'turn off wifi', category: 'system', expected: 'toggle_wifi' },
  { input: 'is clef running', category: 'system', expected: 'clef_status', question: true },
  { input: 'go to workspace 2', category: 'system', expected: 'switch_workspace' },
  { input: 'grab a region of the screen', category: 'system', expected: 'screenshot_region' },
  { input: 'run ls -la ~/Downloads', category: 'system', expected: 'run_shell' },
  // Media
  { input: 'pause', category: 'media', expected: 'media_play_pause' },
  { input: 'turn it down', category: 'media', expected: 'volume_down' },
  { input: 'louder', category: 'media', expected: 'volume_up' },
  // Misc
  { input: 'timer 10m', category: 'misc', expected: 'set_timer' },
  { input: 'remind me in 20 minutes to check the oven', category: 'misc', expected: 'set_timer', accept: ['note_to_inbox'] },
  { input: 'remember to call the dentist', category: 'misc', expected: 'note_to_inbox' },
  { input: 'search my notes for kubernetes', category: 'misc', expected: 'search_notes' },
  { input: 'ssh into the build server', category: 'misc', expected: 'ssh_to_host' },
  // No catalogue action fits
  { input: 'asdfgh jkl', category: 'other', expected: 'other', gibberish: true },
  { input: 'qqq vvv xkcd zzz', category: 'other', expected: 'other', gibberish: true },
  { input: 'order a pizza', category: 'other', expected: 'other', accept: ['web_search'] },
  { input: 'send an email to bob', category: 'other', expected: 'other' },
  { input: 'book a flight to tokyo', category: 'other', expected: 'other', accept: ['web_search'] },
  { input: 'reboot the computer', category: 'other', expected: 'other', accept: ['run_shell'] },
  // Deliberately ambiguous short inputs
  { input: 'notes', category: 'ambiguous', expected: 'search_notes', accept: ['note_to_inbox', 'open_file', 'open_recent'] },
  { input: 'python', category: 'ambiguous', expected: 'open_app', accept: ['web_search', 'define_word', 'open_project'] },
  { input: 'code', category: 'ambiguous', expected: 'open_app', accept: ['open_project'] },
  { input: 'mute', category: 'ambiguous', expected: 'volume_down', accept: ['media_play_pause', 'other'] },
  { input: 'buildbox', category: 'ambiguous', expected: 'ssh_to_host', accept: ['open_project', 'other'] },
  { input: 'agenda', category: 'ambiguous', expected: 'open_recent', accept: ['open_file', 'search_notes'] },
  { input: 'convert', category: 'ambiguous', expected: 'convert', accept: ['other'] },
  { input: 'reply', category: 'ambiguous', expected: 'draft_reply' },
];

export const acceptable = (intent: Intent): Set<string> => new Set([intent.expected, ...(intent.accept ?? [])]);

// Expected noul labels for unambiguous intents; `undefined` means unlabelled.
export function nouls(intent: Intent): Record<string, boolean | undefined> {
  if (intent.category === 'ambiguous') return { is_question: intent.question ?? undefined };
  const a = intent.expected;
  return {
    is_calc: a === 'calc' || a === 'convert',
    is_url: a === 'open_url',
    is_file_search: a === 'open_file' || a === 'open_recent' ? true : ['calc', 'convert', 'web', 'media', 'system', 'other'].includes(intent.category) ? false : undefined,
    wants_paste: a === 'paste_plain',
    is_gibberish: intent.gibberish === true,
    is_question: intent.question === true,
  };
}
