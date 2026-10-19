// Read only the native audit's explicitly public metadata fixture. No invocation,
// desktop control, model access or full installed application list is involved.
import { readFileSync, writeFileSync } from 'node:fs';
import { matchInstalledApps } from '../src/lib/appmatching';
import type { App } from '../src/lib/types';
const path = new URL('../src-tauri/app-index-validation.json', import.meta.url);
const audit = JSON.parse(readFileSync(path, 'utf8')) as { publicCompetitors: App[]; matching?: unknown };
const queries = ['br', 'brave browser', 'open brave', '  BRAVE   Browser  '];
audit.matching = Object.fromEntries(queries.map(input => {
  const matches = matchInstalledApps(input, audit.publicCompetitors);
  if (matches[0]?.app.id !== 'brave-browser.desktop') throw new Error(`Public fixture mismatch for ${input}`);
  if (input === 'brave browser' && matches.length !== 1) throw new Error('Full app name must exclude other browsers');
  return [input, matches.map(candidate => ({ appId: candidate.app.id, match: candidate.match, score: candidate.score, matchedField: candidate.matchedField }))];
}));
writeFileSync(path, JSON.stringify(audit, null, 2) + '\n');
console.log(JSON.stringify(audit.matching, null, 2));
