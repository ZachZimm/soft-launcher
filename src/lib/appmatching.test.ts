import { describe, expect, it } from 'vitest';
import { matchInstalledApps } from './appmatching';
import type { App } from './types';
const apps: App[] = [
  { id: 'brogue.desktop', name: 'Brogue CE' },
  { id: 'broken-arrow.desktop', name: 'Broken Arrow' },
  { id: 'chromium.desktop', name: 'Chromium', genericName: 'Web Browser' },
  { id: 'firefox.desktop', name: 'Firefox', genericName: 'Web Browser' },
  { id: 'brave-browser.desktop', name: 'Brave', genericName: 'Web Browser' },
];
describe('installed application matching', () => {
  it('resolves both public Brave inputs ahead of name and generic browser competitors', () => {
    for (const input of ['br', 'brave browser', 'open brave', '  BRAVE   Browser  ', 'brave-browser.desktop', 'launch brave browser']) {
      expect(matchInstalledApps(input, apps)[0]?.app.id, input).toBe('brave-browser.desktop');
    }
    expect(matchInstalledApps('brave browser', apps).map(item => item.app.id)).toEqual(['brave-browser.desktop']);
  });
  it('requires every term and never treats an unrelated dangerous command as an app', () => {
    for (const input of ['', ' ', 'b', 'kill brave', 'delete brave', 'brave; rm', 'brave https://example.org', '/usr/bin/brave', 'brave --incognito', 'open nonexistent']) expect(matchInstalledApps(input, apps), input).toEqual([]);
    expect(matchInstalledApps('brave; rm', apps)).toEqual([]);
    expect(matchInstalledApps('web browser', apps)).toHaveLength(3);
  });
  it('uses keywords and desktop ID terms and remains deterministic across index order', () => {
    const index = [...apps, { id: 'org.example.Picture_Viewer.desktop', name: 'Photos', keywords: ['Images', 'Photography'] }];
    expect(matchInstalledApps('picture viewer', index)[0]?.app.name).toBe('Photos');
    expect(matchInstalledApps('photography', index)[0]?.matchedField).toBe('keywords');
    expect(matchInstalledApps('br', index)).toEqual(matchInstalledApps('br', [...index].reverse()));
    expect(matchInstalledApps('app', Array.from({ length: 100 }, (_, i) => ({ id: `app-${i}.desktop`, name: `App ${i}` })))).toHaveLength(6);
  });
});

it('matches punctuation in real installed generic names while rejecting executable paths', () => {
  const index: App[] = [{ id: 'com.obsproject.Studio.desktop', name: 'OBS Studio', genericName: 'Streaming/Recording Software' }];
  expect(matchInstalledApps('Streaming/Recording Software', index)[0]?.app.id).toBe('com.obsproject.Studio.desktop');
  for (const query of ['/usr/bin/obs', './obs', '../obs', 'obs /tmp/file']) expect(matchInstalledApps(query, index), query).toEqual([]);
});

it('prefers whole name words for shared prefixes without app-specific priorities', () => {
  const index: App[] = [
    { id: 'obsidian.desktop', name: 'Obsidian', keywords: ['obsidian'] },
    { id: 'com.obsproject.Studio.desktop', name: 'OBS Studio' },
    { id: 'codeblocks.desktop', name: 'Codeblocks' },
    { id: 'com.microsoft.VSCode.desktop', name: 'Visual Studio Code', keywords: ['vscode'] },
    { id: 'org.gnome.DiskUtility.desktop', name: 'Disks', genericName: 'Disk Utility', keywords: ['disk'] },
    { id: 'discord.desktop', name: 'Discord', genericName: 'Internet Messenger' },
    ...apps,
  ];
  expect(matchInstalledApps('obs', index)[0]?.app.name).toBe('OBS Studio');
  expect(matchInstalledApps('code', index)[0]?.app.name).toBe('Visual Studio Code');
  expect(matchInstalledApps('br', index)[0]?.app.name).toBe('Brave');
  expect(matchInstalledApps('dis', index)[0]?.app.name).toBe('Disks');
});
