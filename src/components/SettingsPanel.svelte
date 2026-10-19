<script lang="ts">
  import { untrack } from 'svelte';
  import { LAYOUT_LIMITS } from '../lib/layout';
  import { DEBOUNCE_LIMITS, DEFAULT_SETTINGS, type UiSettings } from '../ui/settings';
  import { DECISION_PRESETS, LOCAL_IS_DISTINCT, presetLabel, presetOf, type DecisionConfig } from '../ui/backendConfig';

  interface Props {
    settings: UiSettings;
    decision: DecisionConfig;
    /** False in the offline demo, where the toy backend replaces the model. */
    decisionEditable: boolean;
    recordingTicks: number;
    onchange: (s: UiSettings) => void;
    ondecision: (d: DecisionConfig) => void;
    oncopyrecording: () => void;
    onclose: () => void;
  }
  let { settings, decision, decisionEditable, recordingTicks, onchange, ondecision, oncopyrecording, onclose }: Props = $props();

  let first: HTMLSelectElement | undefined = $state();
  // Editable copies; applying restarts the runtime with them.
  let kind = $state(untrack(() => decision.kind));
  let url = $state(untrack(() => decision.url));
  let model = $state(untrack(() => decision.model));
  const editing = $derived(presetOf({ kind, url: url.trim(), model: model.trim() }));
  const usePreset = (name: keyof typeof DECISION_PRESETS) => ({ kind, url, model } = { ...DECISION_PRESETS[name] });

  $effect(() => {
    first?.focus();
  });

  const set = <K extends keyof UiSettings>(key: K, value: UiSettings[K]) => onchange({ ...settings, [key]: value });
  const num = (e: Event) => Number((e.currentTarget as HTMLInputElement).value);
</script>

<div
  class="settings"
  aria-label="Launcher settings"
  role="dialog"
  tabindex="-1"
  onkeydown={(e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      onclose();
    }
  }}
>
  <header>
    <h2>Settings</h2>
    <button type="button" class="x" onclick={onclose} aria-label="Close settings (Escape)">×</button>
  </header>

  <div class="grid">
    <label for="s-width">Card width</label>
    <select id="s-width" bind:this={first} value={settings.widthMode} onchange={(e) => set('widthMode', (e.currentTarget as HTMLSelectElement).value === 'linear' ? 'linear' : 'sqrt')}>
      <option value="sqrt">√p (small options stay visible)</option>
      <option value="linear">p with a minimum width</option>
    </select>

    <label for="s-margin">Swap margin</label>
    <span class="range">
      <input id="s-margin" type="range" min={LAYOUT_LIMITS.swapMargin.min} max={LAYOUT_LIMITS.swapMargin.max} step={LAYOUT_LIMITS.swapMargin.step} value={settings.swapMargin} oninput={(e) => set('swapMargin', num(e))} />
      <output>{settings.swapMargin.toFixed(2)}</output>
    </span>

    <label for="s-ticks">Swap after</label>
    <span class="range">
      <input id="s-ticks" type="range" min={LAYOUT_LIMITS.swapTicks.min} max={LAYOUT_LIMITS.swapTicks.max} step="1" value={settings.swapTicks} oninput={(e) => set('swapTicks', num(e))} />
      <output>{settings.swapTicks} tick{settings.swapTicks === 1 ? '' : 's'}</output>
    </span>

    <label for="s-ease">Easing</label>
    <span class="range">
      <input id="s-ease" type="range" min={LAYOUT_LIMITS.easeMs.min} max={LAYOUT_LIMITS.easeMs.max} step={LAYOUT_LIMITS.easeMs.step} value={settings.easeMs} oninput={(e) => set('easeMs', num(e))} />
      <output>{settings.easeMs} ms</output>
    </span>

    <label for="s-debounce">Debounce</label>
    <span class="range">
      <input id="s-debounce" type="range" min={DEBOUNCE_LIMITS.min} max={DEBOUNCE_LIMITS.max} step={DEBOUNCE_LIMITS.step} value={settings.debounceMs} oninput={(e) => set('debounceMs', num(e))} />
      <output>{settings.debounceMs} ms</output>
    </span>

    <label for="s-cards">Max cards</label>
    <span class="range">
      <input id="s-cards" type="range" min={LAYOUT_LIMITS.maxCards.min} max={LAYOUT_LIMITS.maxCards.max} step="1" value={settings.maxCards} oninput={(e) => set('maxCards', num(e))} />
      <output>{settings.maxCards}</output>
    </span>

    <label for="s-numbers">Probabilities</label>
    <label class="check"><input id="s-numbers" type="checkbox" checked={settings.showNumbers} onchange={(e) => set('showNumbers', (e.currentTarget as HTMLInputElement).checked)} /> show numbers on cards</label>
  </div>

  <div class="actions">
    <button type="button" onclick={() => onchange({ ...DEFAULT_SETTINGS })}>Reset to defaults</button>
    <button type="button" onclick={oncopyrecording} disabled={recordingTicks === 0} title="Copy the last rendered ticks as JSON for the replay page">Copy recording ({recordingTicks})</button>
    <a href="./replay.html" target="_blank" rel="noopener">Open replay page</a>
  </div>

  <fieldset disabled={!decisionEditable}>
    <legend>Decision backend{decisionEditable ? '' : ' (demo uses an offline toy model)'}</legend>
    <div class="grid">
      <span class="label">Profile</span>
      <span class="presets" role="group" aria-label="Decision backend profile">
        <button type="button" aria-pressed={editing === 'default'} onclick={() => usePreset('default')}>{presetLabel('default')} (default)</button>
        {#if LOCAL_IS_DISTINCT}<button type="button" aria-pressed={editing === 'local'} onclick={() => usePreset('local')}>{presetLabel('local')}</button>{/if}
        {#if !editing}<span class="dim">custom</span>{/if}
      </span>
      <label for="s-kind">Server</label>
      <select id="s-kind" bind:value={kind}>
        <option value="ollama">Ollama System One</option>
        <option value="llamacpp">llama.cpp System One</option>
      </select>
      <label for="s-url">URL</label>
      <input id="s-url" type="url" bind:value={url} spellcheck="false" />
      <label for="s-model">Model</label>
      <input id="s-model" type="text" bind:value={model} spellcheck="false" disabled={kind === 'llamacpp'} />
    </div>
    <div class="actions">
      <button type="button" class="primary" onclick={() => ondecision({ kind, url, model })}>Apply and reconnect</button>
      <span class="dim">In use: {presetLabel(presetOf(decision)) ?? 'custom'}. Other hosts also need the Tauri CSP to allow them.</span>
    </div>
  </fieldset>
</div>

<style>
  .settings {
    border-top: 1px solid var(--line);
    padding-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    font-size: 12.5px;
  }
  header {
    display: flex;
    align-items: center;
  }
  h2 {
    margin: 0;
    font-size: 13px;
  }
  .x {
    margin-left: auto;
    border: none;
    background: none;
    font-size: 16px;
    color: var(--text-faint);
  }
  .grid {
    display: grid;
    grid-template-columns: 110px 1fr;
    gap: 6px 12px;
    align-items: center;
  }
  .grid > label,
  .grid > .label {
    color: var(--text-dim);
  }
  .presets {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .presets button {
    font-size: 12px;
    padding: 2px 8px;
  }
  .presets button[aria-pressed='true'] {
    border-color: var(--accent);
    color: var(--accent);
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
  select,
  input[type='url'],
  input[type='text'] {
    background: var(--bg-input);
    border: 1px solid var(--line-strong);
    border-radius: var(--radius-sm);
    padding: 3px 6px;
  }
  .check {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .actions {
    display: flex;
    gap: 10px;
    align-items: center;
  }
  a {
    color: var(--accent);
  }
  fieldset {
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    padding: 8px 10px 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  legend {
    color: var(--text-dim);
    padding: 0 4px;
  }
  .dim {
    color: var(--text-faint);
    font-size: 11.5px;
  }
</style>
