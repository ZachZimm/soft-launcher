import { normaliseTick, type Band, type RecordedTick, type Recording } from './recording';

// Synthetic 20-keystroke recording: typing "the pdf i downloaded". The shape was
// chosen to exercise the row: "other" leads for the first four ticks (the teach
// offer appears on the third), then open_file and open_recent trade the lead with
// sub-margin noise for six ticks before open_recent wins decisively and reaches the
// act band. Without hysteresis that stretch flips the first two cards on nearly
// every keystroke; with the 0.08 / two-tick rule it swaps once.
//
// This is not model output. Replace it with a live export once the local model is
// available (launcher settings, "Copy recording").

type Row = [input: string, other: number, confidence: number, mass: Record<string, number>];

const rows: Row[] = [
  ['t', 0.3, 0.3, { open_app: 0.22, web_search: 0.18, open_file: 0.12, search_notes: 0.1, define_word: 0.08 }],
  ['th', 0.34, 0.33, { web_search: 0.2, open_app: 0.16, define_word: 0.12, open_file: 0.1, search_notes: 0.08 }],
  ['the', 0.38, 0.36, { web_search: 0.19, define_word: 0.15, open_file: 0.12, open_app: 0.08, search_notes: 0.08 }],
  ['the ', 0.36, 0.34, { web_search: 0.18, open_file: 0.16, define_word: 0.12, search_notes: 0.1, open_recent: 0.08 }],
  ['the p', 0.22, 0.27, { web_search: 0.26, open_file: 0.2, open_recent: 0.12, define_word: 0.1, search_notes: 0.1 }],
  ['the pd', 0.12, 0.31, { open_file: 0.3, open_recent: 0.24, web_search: 0.18, search_notes: 0.08, define_word: 0.08 }],
  ['the pdf', 0.08, 0.38, { open_file: 0.36, open_recent: 0.33, web_search: 0.12, search_notes: 0.06, define_word: 0.05 }],
  ['the pdf ', 0.07, 0.39, { open_recent: 0.37, open_file: 0.34, web_search: 0.11, search_notes: 0.06, define_word: 0.05 }],
  ['the pdf i', 0.07, 0.4, { open_file: 0.38, open_recent: 0.35, web_search: 0.1, search_notes: 0.05, define_word: 0.05 }],
  ['the pdf i ', 0.06, 0.41, { open_recent: 0.39, open_file: 0.33, web_search: 0.1, search_notes: 0.06, define_word: 0.06 }],
  ['the pdf i d', 0.05, 0.45, { open_recent: 0.44, open_file: 0.31, web_search: 0.09, search_notes: 0.06, define_word: 0.05 }],
  ['the pdf i do', 0.05, 0.4, { open_file: 0.38, open_recent: 0.36, web_search: 0.09, search_notes: 0.07, define_word: 0.05 }],
  ['the pdf i dow', 0.05, 0.48, { open_recent: 0.46, open_file: 0.3, web_search: 0.08, search_notes: 0.06, define_word: 0.05 }],
  ['the pdf i down', 0.04, 0.55, { open_recent: 0.52, open_file: 0.27, web_search: 0.07, search_notes: 0.05, define_word: 0.05 }],
  ['the pdf i downl', 0.04, 0.62, { open_recent: 0.58, open_file: 0.24, web_search: 0.06, search_notes: 0.04, define_word: 0.04 }],
  ['the pdf i downlo', 0.03, 0.68, { open_recent: 0.63, open_file: 0.21, web_search: 0.05, search_notes: 0.04, define_word: 0.04 }],
  ['the pdf i downloa', 0.03, 0.74, { open_recent: 0.68, open_file: 0.18, web_search: 0.05, search_notes: 0.03, define_word: 0.03 }],
  ['the pdf i download', 0.03, 0.79, { open_recent: 0.72, open_file: 0.15, web_search: 0.04, search_notes: 0.03, define_word: 0.03 }],
  ['the pdf i downloade', 0.02, 0.84, { open_recent: 0.78, open_file: 0.12, web_search: 0.03, search_notes: 0.03, define_word: 0.02 }],
  ['the pdf i downloaded', 0.02, 0.91, { open_recent: 0.84, open_file: 0.09, web_search: 0.02, search_notes: 0.02, define_word: 0.01 }],
];

function band(confidence: number): Band {
  return confidence >= 0.85 ? 'act' : confidence >= 0.6 ? 'confirm' : 'fallback';
}

// Keystroke spacing alternates between quick and slower presses so the timeline
// looks like typing; replay advances by tick, so spacing never affects layout.
const gaps = [0, 110, 95, 140, 180, 90, 105, 160, 120, 150, 100, 95, 130, 110, 90, 85, 100, 95, 120, 140];

export const FIXTURE_20: Recording = {
  name: 'Synthetic: "the pdf i downloaded" (20 keystrokes)',
  source: 'synthetic',
  createdAt: '2026-10-17T00:00:00.000Z',
  ticks: rows.map(([input, other, confidence, mass], i): RecordedTick => {
    const candidates = Object.entries(mass).map(([id, p]) => ({ id, p }));
    const top = candidates.reduce((a, b) => (b.p > a.p ? b : a));
    return normaliseTick({
      input,
      at: gaps.slice(0, i + 1).reduce((s, g) => s + g, 0),
      candidates,
      other,
      confidence,
      band: band(confidence),
      choice: other > top.p ? 'other' : top.id,
    });
  }),
};

/** Human labels for fixture action ids when no catalogue is loaded. */
export const FIXTURE_LABELS: Record<string, string> = {
  open_app: 'Open app',
  open_file: 'Open file',
  open_recent: 'Open recent',
  web_search: 'Web search',
  search_notes: 'Search notes',
  define_word: 'Define word',
  other: 'Other',
};
