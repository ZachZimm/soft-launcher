<script lang="ts">
  import DistributionRow, { type CardInfo } from './DistributionRow.svelte';
  import OtherBar from './OtherBar.svelte';
  import { DEFAULT_LAYOUT, LAYOUT_LIMITS, countFlicker, countReorders, type LayoutParams } from '../lib/layout';
  import { FIXTURE_20, FIXTURE_LABELS } from '../demo/fixture';
  import { parseRecording, type Recording } from '../demo/recording';
  import { digest, replay, type ReplayFrame } from '../demo/replay';
  import { actionLabel, pct } from '../ui/format';

  // Fixed row width so frames are identical on every screen.
  const ROW = 720;

  let recording = $state<Recording>(FIXTURE_20);
  let t = $state(0);
  let playing = $state(false);
  let showNumbers = $state(true);
  let swapMargin = $state(DEFAULT_LAYOUT.swapMargin);
  let swapTicks = $state(DEFAULT_LAYOUT.swapTicks);
  let easeMs = $state(DEFAULT_LAYOUT.easeMs);
  let minWidth = $state(DEFAULT_LAYOUT.minWidth);
  let speed = $state(1);
  let pasted = $state('');
  let loadError = $state<string | null>(null);

  const base = $derived<LayoutParams>({ ...DEFAULT_LAYOUT, rowWidth: ROW, swapMargin, swapTicks, easeMs, minWidth });

  interface Variant {
    key: string;
    title: string;
    note: string;
    params: LayoutParams;
  }
  const variants = $derived<Variant[]>([
    { key: 'sqrt', title: '√p width, hysteresis', note: 'Launcher default', params: { ...base, widthMode: 'sqrt' } },
    { key: 'linear', title: 'p width with floor, hysteresis', note: 'Open question §11', params: { ...base, widthMode: 'linear' } },
    { key: 'raw', title: '√p width, no hysteresis', note: 'Baseline: reorder on every tick', params: { ...base, widthMode: 'sqrt', swapMargin: 0, swapTicks: 1 } },
  ]);

  const runs = $derived(
    variants.map((v) => {
      const frames = replay(recording, v.params);
      const orders = frames.map((f) => f.row.state.order);
      return {
        ...v,
        frames,
        reorders: countReorders(orders),
        flicker: countFlicker(orders),
        digest: digest(frames),
        deterministic: digest(replay(recording, v.params)) === digest(frames),
      };
    }),
  );

  const n = $derived(recording.ticks.length);
  const current = $derived(runs[0]!.frames[Math.min(t, n - 1)]!);

  function label(id: string) {
    return FIXTURE_LABELS[id] ?? actionLabel(id);
  }
  function infoFor(frame: ReplayFrame) {
    return (id: string): CardInfo => ({
      label: label(id),
      detail: frame.row.cards[0]?.id === id ? `confidence ${pct(frame.confidence)} · ${frame.source.band}` : '',
      needsConfirm: false,
      confidence: frame.confidence,
    });
  }

  // Playback follows the recorded keystroke spacing, scaled by speed.
  $effect(() => {
    if (!playing) return;
    if (t >= n - 1) {
      playing = false;
      return;
    }
    const gap = Math.max(30, (recording.ticks[t + 1]!.at - recording.ticks[t]!.at) / speed);
    const timer = setTimeout(() => (t = Math.min(n - 1, t + 1)), gap);
    return () => clearTimeout(timer);
  });

  function play() {
    if (t >= n - 1) t = 0;
    playing = !playing;
  }

  function load() {
    loadError = null;
    try {
      recording = parseRecording(JSON.parse(pasted));
      t = 0;
      playing = false;
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
    }
  }

  function onKey(e: KeyboardEvent) {
    const el = e.target as HTMLElement;
    if (el.closest('textarea, input[type="text"], input[type="number"]')) return;
    if (e.key === 'ArrowRight' && !el.matches('input[type="range"]')) {
      e.preventDefault();
      playing = false;
      t = Math.min(n - 1, t + 1);
    } else if (e.key === 'ArrowLeft' && !el.matches('input[type="range"]')) {
      e.preventDefault();
      playing = false;
      t = Math.max(0, t - 1);
    } else if (e.key === ' ' && !el.matches('button, input')) {
      e.preventDefault();
      play();
    } else if (e.key === 'Home') {
      t = 0;
    } else if (e.key === 'End') {
      t = n - 1;
    }
  }
</script>

<svelte:window onkeydown={onKey} />

<main class="page" style="--ease-ms: {easeMs}ms">
  <header>
    <h1>Distribution replay</h1>
    <p class="sub">
      {recording.name} · {recording.source === 'synthetic' ? 'synthetic fixture, not model output' : 'recorded from the live launcher'} ·
      deterministic: same recording and settings give the same frames.
      <a href="./index.html">Launcher</a>
    </p>
  </header>

  <section class="transport" aria-label="Playback">
    <button type="button" onclick={() => ((playing = false), (t = Math.max(0, t - 1)))} aria-label="Previous keystroke (Left arrow)">◀</button>
    <button type="button" class="primary" onclick={play} aria-label={playing ? 'Pause (Space)' : 'Play (Space)'}>{playing ? 'Pause' : 'Play'}</button>
    <button type="button" onclick={() => ((playing = false), (t = Math.min(n - 1, t + 1)))} aria-label="Next keystroke (Right arrow)">▶</button>
    <label class="scrub">
      <span class="visually-hidden">Keystroke</span>
      <input type="range" min="0" max={n - 1} step="1" bind:value={t} oninput={() => (playing = false)} />
    </label>
    <span class="tick mono">{t + 1}/{n}</span>
    <label class="speed">speed
      <select bind:value={speed}>
        <option value={0.25}>¼×</option>
        <option value={0.5}>½×</option>
        <option value={1}>1×</option>
        <option value={2}>2×</option>
      </select>
    </label>
  </section>

  <section class="input-echo" aria-live="polite">
    <span class="caret mono">{current.input}<span class="cursor" aria-hidden="true"></span></span>
    <span class="facts">
      other {pct(current.other)}{current.otherStreak ? ` · leads ${current.otherStreak} tick${current.otherStreak === 1 ? '' : 's'}` : ''}
      {#if current.offerTeach}<span class="pill other">teach offer</span>{/if}
      {#if current.primedId}<span class="pill primed">primed: {label(current.primedId)}</span>{/if}
    </span>
  </section>

  {#each runs as run (run.key)}
    {@const frame = run.frames[Math.min(t, n - 1)]!}
    <section class="variant" aria-label={run.title}>
      <div class="vhead">
        <h2>{run.title}</h2>
        <span class="note">{run.note}</span>
        <span class="stats mono" title="Order changes over the recording / flips that reverted within three ticks">
          reorders {run.reorders} · flicker <strong class:bad={run.flicker > 0}>{run.flicker}</strong> · {run.digest}{run.deterministic ? ' ✓' : ' ✗'}
        </span>
      </div>
      <div class="rowbox" style="width: {ROW}px">
        <DistributionRow
          cards={frame.row.cards}
          info={infoFor(frame)}
          targetId={frame.row.cards[0]?.id ?? null}
          explicit={false}
          primedId={frame.primedId}
          holdId={null}
          confirmId={null}
          {showNumbers}
          {easeMs}
          dimmed={false}
          loading={false}
          emptyMessage="No candidates"
          idPrefix="replay-{run.key}"
          label={run.title}
        />
        <OtherBar mass={frame.other} leading={frame.otherStreak > 0} offer={frame.offerTeach} hiddenCount={frame.row.hidden.length} {showNumbers} {easeMs} />
      </div>
      <ol class="timeline" aria-label="Leader per keystroke">
        {#each run.frames as f, i (i)}
          {@const lead = f.row.cards[0]?.id}
          {@const prev = i > 0 ? run.frames[i - 1]!.row.cards[0]?.id : lead}
          <li>
            <button type="button" class:on={i === t} class:flip={lead !== prev} title="{i + 1}: “{f.input}” → {lead ? label(lead) : '—'}" onclick={() => ((playing = false), (t = i))}>
              {lead ? label(lead).split(' ').map((w) => w[0]).join('') : '·'}
            </button>
          </li>
        {/each}
      </ol>
    </section>
  {/each}

  <section class="tunables" aria-label="Tunables">
    <h2>Tunables</h2>
    <div class="grid">
      <label for="r-margin">Swap margin</label>
      <span class="range"><input id="r-margin" type="range" min={LAYOUT_LIMITS.swapMargin.min} max={LAYOUT_LIMITS.swapMargin.max} step={LAYOUT_LIMITS.swapMargin.step} bind:value={swapMargin} /><output>{swapMargin.toFixed(2)}</output></span>
      <label for="r-ticks">Swap after</label>
      <span class="range"><input id="r-ticks" type="range" min={LAYOUT_LIMITS.swapTicks.min} max={LAYOUT_LIMITS.swapTicks.max} step="1" bind:value={swapTicks} /><output>{swapTicks} ticks</output></span>
      <label for="r-ease">Easing</label>
      <span class="range"><input id="r-ease" type="range" min={LAYOUT_LIMITS.easeMs.min} max={LAYOUT_LIMITS.easeMs.max} step={LAYOUT_LIMITS.easeMs.step} bind:value={easeMs} /><output>{easeMs} ms</output></span>
      <label for="r-min">Min width</label>
      <span class="range"><input id="r-min" type="range" min={LAYOUT_LIMITS.minWidth.min} max={LAYOUT_LIMITS.minWidth.max} step={LAYOUT_LIMITS.minWidth.step} bind:value={minWidth} /><output>{minWidth} px</output></span>
      <label for="r-num">Probabilities</label>
      <label class="check"><input id="r-num" type="checkbox" bind:checked={showNumbers} /> show numbers</label>
    </div>
    <button
      type="button"
      onclick={() => {
        swapMargin = DEFAULT_LAYOUT.swapMargin;
        swapTicks = DEFAULT_LAYOUT.swapTicks;
        easeMs = DEFAULT_LAYOUT.easeMs;
        minWidth = DEFAULT_LAYOUT.minWidth;
      }}>Reset to launcher defaults</button
    >
  </section>

  <details class="load">
    <summary>Load another recording</summary>
    <p class="sub">Paste JSON from the launcher (Settings → Copy recording).</p>
    <textarea bind:value={pasted} rows="6" spellcheck="false" aria-label="Recording JSON"></textarea>
    <div class="actions">
      <button type="button" class="primary" onclick={load} disabled={!pasted.trim()}>Load</button>
      <button type="button" onclick={() => ((recording = FIXTURE_20), (t = 0), (loadError = null))}>Back to the fixture</button>
      {#if loadError}<span class="err" role="alert">{loadError}</span>{/if}
    </div>
  </details>
</main>

<style>
  .page {
    max-width: 800px;
    margin: 0 auto;
    padding: 24px 16px 48px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  h1 {
    margin: 0;
    font-size: 18px;
  }
  h2 {
    margin: 0;
    font-size: 13px;
  }
  .sub {
    margin: 4px 0 0;
    color: var(--text-dim);
    font-size: 12.5px;
  }
  a {
    color: var(--accent);
  }
  .mono {
    font-family: var(--mono);
  }
  .transport {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .scrub {
    flex: 1;
    display: flex;
  }
  .scrub input {
    width: 100%;
  }
  .tick {
    width: 48px;
    text-align: right;
    color: var(--text-dim);
  }
  .speed {
    font-size: 12px;
    color: var(--text-dim);
    display: flex;
    gap: 4px;
    align-items: center;
  }
  select {
    background: var(--bg-input);
    border: 1px solid var(--line-strong);
    border-radius: var(--radius-sm);
  }
  .input-echo {
    display: flex;
    align-items: center;
    gap: 16px;
    background: var(--bg-input);
    border: 1px solid var(--line-strong);
    border-radius: var(--radius);
    padding: 8px 12px;
  }
  .caret {
    font-size: 16px;
    white-space: pre;
  }
  .cursor {
    display: inline-block;
    width: 2px;
    height: 1.1em;
    vertical-align: -0.15em;
    background: var(--accent);
    margin-left: 1px;
  }
  .facts {
    margin-left: auto;
    font-size: 12px;
    color: var(--text-dim);
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .pill {
    font-size: 11px;
    padding: 1px 6px;
    border-radius: 999px;
    border: 1px solid;
  }
  .pill.other {
    color: var(--other);
  }
  .pill.primed {
    color: var(--accent);
  }
  .variant {
    background: var(--bg);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 10px 12px 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .vhead {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }
  .note {
    font-size: 12px;
    color: var(--text-faint);
  }
  .stats {
    margin-left: auto;
    font-size: 11.5px;
    color: var(--text-dim);
  }
  .stats .bad {
    color: var(--danger);
  }
  .rowbox {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-width: 100%;
  }
  .timeline {
    display: flex;
    gap: 3px;
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .timeline button {
    width: 32px;
    padding: 2px 0;
    font-size: 10.5px;
    font-family: var(--mono);
    color: var(--text-faint);
    background: var(--bg-input);
    border-color: var(--line);
  }
  .timeline button.flip {
    border-color: var(--warn);
    color: var(--warn);
  }
  .timeline button.on {
    border-color: var(--accent);
    color: var(--text);
  }
  .tunables {
    display: flex;
    flex-direction: column;
    gap: 8px;
    font-size: 12.5px;
  }
  .grid {
    display: grid;
    grid-template-columns: 110px 1fr;
    gap: 6px 12px;
    align-items: center;
  }
  .grid > label {
    color: var(--text-dim);
  }
  .range {
    display: flex;
    gap: 10px;
    align-items: center;
  }
  .range input {
    flex: 1;
  }
  output {
    width: 64px;
    font-family: var(--mono);
    font-size: 12px;
    color: var(--text-dim);
  }
  .check {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .tunables > button {
    align-self: flex-start;
  }
  .load textarea {
    width: 100%;
    font-family: var(--mono);
    font-size: 12px;
    background: var(--bg-input);
    border: 1px solid var(--line-strong);
    border-radius: var(--radius-sm);
    padding: 8px;
  }
  .load summary {
    cursor: pointer;
    color: var(--text-dim);
  }
  .actions {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-top: 6px;
  }
  .err {
    color: var(--danger);
    font-size: 12px;
  }
</style>
