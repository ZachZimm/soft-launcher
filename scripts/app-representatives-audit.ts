// Inspect local installed metadata in memory. Persist only a bounded set of
// public application names/IDs and match results. Never launch or send a model
// request; never retain the full installed list, executable or icon source path.
import { spawnSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { matchInstalledApps } from '../src/lib/appmatching';
import type { App } from '../src/lib/types';
const result = spawnSync('cargo', ['run', '--manifest-path', 'src-tauri/Cargo.toml', '--features', 'desktop', '--example', 'app-index-audit', '--quiet', '--', '--matching-index'], { encoding: 'utf8', maxBuffer: 2 * 1024 * 1024 });
if (result.status !== 0) throw new Error('Read-only native metadata audit failed');
const index = JSON.parse(result.stdout) as App[];
const categories: Record<string, RegExp[]> = {
  browser: [/^Brave$/i, /^Firefox$/i, /^Chromium$/i],
  editor: [/^Visual Studio Code$/i, /^Code$/i, /^Zed$/i, /^Kate$/i, /^Emacs$/i, /^Neovim$/i],
  terminal: [/^foot$/i, /^kitty$/i, /^Alacritty$/i, /^Konsole$/i],
  files: [/^Dolphin$/i, /^Thunar$/i, /^Files$/i, /^Nautilus$/i, /^Nemo$/i],
  chat: [/^Discord$/i, /^Signal$/i, /^Telegram Desktop$/i, /^Element$/i, /^Slack$/i],
  game: [/^Broken Arrow$/i, /^Brogue CE/i, /^Steam$/i, /^Lutris$/i],
  utility: [/^OBS Studio$/i, /^GIMP$/i, /^Blender$/i],
};
const representatives: { category: string; app: App }[] = [];
for (const [category, patterns] of Object.entries(categories)) {
  const max = category === 'browser' ? 3 : category === 'game' ? 2 : 1;
  for (const pattern of patterns) {
    const app = index.find(app => pattern.test(app.name) && !representatives.some(row => row.app.id === app.id));
    if (app) representatives.push({ category, app });
    if (representatives.filter(row => row.category === category).length >= max) break;
  }
}
const missingIcon = index.find(app => !app.icon && representatives.some(row => row.app.id === app.id));
const test = (query: string, app: App) => {
  const matches = matchInstalledApps(query, index);
  return { query, selectedRank: matches.findIndex(candidate => candidate.app.id === app.id) + 1 || null, first: matches[0]?.app.id ?? null, displayed: matches.some(candidate => candidate.app.id === app.id) };
};
const rows = representatives.map(({ category, app }) => {
  const words = app.name.match(/[\p{L}\p{N}]+/gu) ?? [];
  const queries = [app.name, words[0]!.slice(0, Math.min(3, words[0]!.length))];
  if (words.length > 1) queries.push(...words.filter(word => word.length >= 3));
  if (words.length > 1) queries.push(words.map(word => word.slice(0, Math.max(2, Math.min(word.length, 4)))).join(' '));
  if (app.genericName) queries.push(app.genericName);
  if (app.keywords?.[0]) queries.push(app.keywords[0]);
  return { category, id: app.id, name: app.name, genericName: app.genericName ?? null, keywords: app.keywords ?? [], hasIcon: !!app.icon, matches: [...new Set(queries)].map(query => test(query, app)) };
});
for (const row of rows) { if (row.matches[0]?.selectedRank !== 1) throw new Error('Exact installed app name failed: ' + row.name); }
const audit = { readOnly: true, desktopInput: false, launches: 0, inferenceRequests: 0, indexCount: index.length, representativeCount: rows.length, missingIconRepresentative: missingIcon?.id ?? null, rows };
writeFileSync(new URL('../src-tauri/app-representatives-validation.json', import.meta.url), JSON.stringify(audit, null, 2) + '\n');
console.log(JSON.stringify(audit, null, 2));
