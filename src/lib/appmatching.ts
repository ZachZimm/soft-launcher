import type { App, InstalledAppCandidate } from './types';

export const MAX_INSTALLED_APPS = 256;
export const MAX_APP_CHOICES = 6;
const normalize = (value: string): string => value.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');

/** Local matching only. Every query term must be explained by installed metadata. */
export function matchInstalledApps(input: string, apps: readonly App[], limit = MAX_APP_CHOICES): InstalledAppCandidate[] {
  if (input.length > 256 || /[\0\\$;&|<>`"'=!?():]/.test(input)) return [];
  if (/(?:^|[\s.])\/|\/\/|~/.test(input)) return [];
  const query = normalize(input.trim().replace(/\.desktop$/i, '')).replace(/^(?:open|launch|start)\s+/, '');
  if (query.length < 2) return [];
  const terms = query.split(' ').filter(Boolean);
  if (terms.length > 12) return [];
  const seen = new Set<string>();
  const matches: InstalledAppCandidate[] = [];
  for (const app of apps.slice(0, MAX_INSTALLED_APPS)) {
    if (!app.id || !app.name || app.id.length > 256 || app.name.length > 100 || seen.has(app.id)) continue;
    seen.add(app.id);
    const name = normalize(app.name);
    const id = normalize(app.id.replace(/\.desktop$/i, ''));
    const generic = normalize((app.genericName ?? '').slice(0, 160));
    const keywords = (app.keywords ?? []).slice(0, 16).map(value => normalize(value.slice(0, 80)));
    const fields = { name, id, genericName: generic, keywords: keywords.join(' ') };
    const hits = Object.entries(fields).filter(([, value]) => terms.some(term => value.split(' ').some(word => word.startsWith(term))));
    const words = Object.values(fields).join(' ').split(' ');
    if (!terms.every(term => words.some(word => word.startsWith(term)))) continue;
    const allIn = (value: string) => terms.every(term => value.split(' ').some(word => word.startsWith(term)));
    let match: InstalledAppCandidate['match'] = 'terms';
    let score: number;
    let matchedField: NonNullable<InstalledAppCandidate['matchedField']>;
    if (query === name) { match = 'exact'; score = 5000; matchedField = 'name'; }
    else if (allIn(name) && terms.some(term => name.split(' ').includes(term))) { match = name.startsWith(query) ? 'prefix' : 'terms'; score = 4500; matchedField = 'name'; }
    else if (name.startsWith(query)) { match = 'prefix'; score = 4000; matchedField = 'name'; }
    else if (allIn(name)) { score = 3000; matchedField = 'name'; }
    else if (allIn(id)) { score = 2000; matchedField = 'id'; }
    else { score = 1000; matchedField = allIn(generic) ? 'genericName' : allIn(keywords.join(' ')) ? 'keywords' : hits[0]![0] as typeof matchedField; }
    score += hits.length * 10;
    matches.push({ app, match, score, matchedField });
  }
  return matches.sort((a, b) => b.score - a.score || a.app.name.length - b.app.name.length || a.app.name.localeCompare(b.app.name) || a.app.id.localeCompare(b.app.id)).slice(0, Math.max(0, Math.min(MAX_INSTALLED_APPS, limit)));
}
