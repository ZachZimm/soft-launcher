<script lang="ts">
  import type { InstalledAppCandidate } from '../lib/types';
  import { appKey } from '../ui/interaction';

  interface Props {
    apps: InstalledAppCandidate[];
    /** Selection key Enter acts on (`app:<id>`), or an action card id. */
    targetId: string | null;
    /** True when the user explicitly selected a card with Tab, arrows or the pointer. */
    explicit: boolean;
    /** Why apps cannot open right now; cards stay visible but say so. */
    unavailable?: string | null;
    /** Key of the app being opened. */
    openingKey: string | null;
    idPrefix: string;
    onpress?: (key: string, e: PointerEvent) => void;
    onrelease?: (key: string, e: PointerEvent) => void;
  }
  let { apps, targetId, explicit, unavailable = null, openingKey, idPrefix, onpress, onrelease }: Props = $props();

  // Same tones as the theme; a name always gets the same colour.
  const TONES = ['#a8b665', '#7daea3', '#d8a657', '#d3869b', '#89b482', '#e78a4e'];
  function tone(id: string): string {
    let h = 0;
    for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return TONES[h % TONES.length]!;
  }
  /** Only bounded PNG data from native is drawn; anything else falls back to the monogram. */
  const iconOf = (c: InstalledAppCandidate): string | null => (c.app.icon?.startsWith('data:image/png;base64,') ? c.app.icon : null);
  const initial = (name: string): string => (name.trim()[0] ?? '?').toLocaleUpperCase();
  /** Names shown more than once (two "SteamVR" entries): those cards say which entry they are. */
  const repeated = $derived.by(() => {
    const seen = new Map<string, number>();
    for (const c of apps) seen.set(c.app.name.toLocaleLowerCase(), (seen.get(c.app.name.toLocaleLowerCase()) ?? 0) + 1);
    return seen;
  });
  /** Second line: the generic name ("Web Browser"), or the desktop entry when names collide. */
  function subtitle(c: InstalledAppCandidate): string {
    if ((repeated.get(c.app.name.toLocaleLowerCase()) ?? 0) > 1) return c.app.id.replace(/\.desktop$/i, '');
    if (c.app.genericName && c.app.genericName.toLocaleLowerCase() !== c.app.name.toLocaleLowerCase()) return c.app.genericName;
    return 'Application';
  }
  let broken = $state<Record<string, boolean>>({});

  /** The card under a released pointer, so a drag off the card does not open it. */
  function release(key: string, e: PointerEvent) {
    const el = e.currentTarget as HTMLElement;
    const r = el.getBoundingClientRect();
    const inside = e.type === 'pointerup' && e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
    onrelease?.(inside ? key : '', e);
  }
</script>

<!-- At least four columns: with few matches each card is wider, so names read in full. -->
<div class="apps" role="listbox" id="{idPrefix}-apps" aria-label="Installed apps" style="--cols: {Math.max(4, apps.length)}">
  {#each apps as c (c.app.id)}
    {@const key = appKey(c.app.id)}
    {@const isTarget = targetId === key}
    {@const icon = broken[c.app.id] ? null : iconOf(c)}
    <div
      class="app"
      class:target={isTarget}
      class:explicit={explicit && isTarget}
      class:opening={openingKey === key}
      class:unavailable={!!unavailable}
      role="option"
      id="{idPrefix}-card-{key}"
      aria-selected={isTarget}
      aria-label="{c.app.name}, {subtitle(c)}{unavailable ? `, ${unavailable}` : isTarget ? ', Enter opens it' : ''}"
      tabindex="-1"
      title={unavailable ?? `${c.app.name} — ${subtitle(c)}`}
      onpointerdown={(e) => onpress?.(key, e)}
      onpointerup={(e) => release(key, e)}
      onpointercancel={(e) => release(key, e)}
    >
      <span class="icon" style="--tone: {tone(c.app.id)}">
        {#if icon}
          <img src={icon} alt="" width="40" height="40" draggable="false" onerror={() => (broken = { ...broken, [c.app.id]: true })} />
        {:else}
          <span class="monogram" aria-hidden="true">{initial(c.app.name)}</span>
        {/if}
      </span>
      <span class="name">{c.app.name}</span>
      {#if openingKey === key}
        <span class="cue opening-cue">Opening…</span>
      {:else if isTarget && !unavailable}
        <span class="cue"><kbd>↵</kbd> open</span>
      {:else}
        <span class="sub">{subtitle(c)}</span>
      {/if}
    </div>
  {/each}
</div>

<style>
  .apps {
    display: grid;
    grid-template-columns: repeat(var(--cols), minmax(0, 1fr));
    gap: 8px;
  }
  .app {
    position: relative;
    height: 114px;
    padding: 9px 6px 7px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--bg-card);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    min-width: 0;
    user-select: none;
    cursor: default;
    transition:
      border-color var(--ease-ms) ease-out,
      background-color var(--ease-ms) ease-out;
  }
  .app:hover {
    border-color: var(--line-strong);
  }
  .app.target {
    background: var(--bg-card-lead);
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent) inset;
  }
  .app.explicit {
    border-style: solid;
  }
  .app.unavailable {
    opacity: 0.6;
  }
  .app.unavailable.target {
    border-color: var(--line-strong);
    box-shadow: none;
  }
  .icon {
    flex: none;
    width: 40px;
    height: 40px;
    display: grid;
    place-items: center;
  }
  .icon img {
    width: 40px;
    height: 40px;
    object-fit: contain;
    -webkit-user-drag: none;
  }
  .monogram {
    width: 36px;
    height: 36px;
    border-radius: 9px;
    display: grid;
    place-items: center;
    font-weight: 700;
    font-size: 18px;
    color: var(--accent-ink);
    background: var(--tone);
  }
  /* Up to two lines, so long names ("Visual Studio Code") read in full. */
  .name {
    max-width: 100%;
    font-weight: 600;
    font-size: 13px;
    line-height: 1.2;
    text-align: center;
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    overflow: hidden;
    overflow-wrap: anywhere;
  }
  /* Pinned to the card bottom, whether the name takes one line or two. */
  .sub,
  .cue {
    margin-top: auto;
    max-width: 100%;
    font-size: 11px;
    color: var(--text-faint);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .cue {
    color: var(--accent);
    display: inline-flex;
    gap: 4px;
    align-items: center;
  }
  .opening-cue {
    color: var(--text-dim);
  }
  @media (prefers-reduced-motion: reduce) {
    .app {
      transition: none;
    }
  }
</style>
