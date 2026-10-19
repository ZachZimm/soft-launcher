<script lang="ts">
  export interface Outcome {
    kind: 'success' | 'error';
    actionId: string;
    label: string;
    message: string;
    text?: string;
    outcomeId?: string;
    at: number;
    undone?: boolean;
    /** What to do next after an error; defaults to changing the input or the card. */
    next?: string;
  }

  export interface ConfirmView {
    label: string;
    message: string;
    detail: string;
    expiresAt: number;
    armed: boolean;
    /** Replaces "Confirm <label>" as the heading. */
    title?: string;
    /** Where the text comes from and goes, one line each, under the detail. */
    route?: string[];
    /** What the second Enter does; defaults to "run". */
    verb?: string;
  }

  interface Props {
    confirm: ConfirmView | null;
    outcome: Outcome | null;
    hint: string | null;
    /** What is running, e.g. "Sending to Hermes Desktop"; shown with a trailing ellipsis. */
    busyLabel: string | null;
    canUndo: boolean;
    now: number;
    oncopy?: (text: string) => void;
    onundo?: () => void;
    ondismiss?: () => void;
    oncancel?: () => void;
  }
  let { confirm, outcome, hint, busyLabel, canUndo, now, oncopy, onundo, ondismiss, oncancel }: Props = $props();

  const remaining = $derived(confirm ? Math.max(0, Math.ceil((confirm.expiresAt - now) / 1000)) : 0);
</script>

<!-- Confirmation is announced assertively: it gates a dangerous action. -->
<div class="feedback" aria-live="assertive" aria-atomic="true">
  {#if confirm}
    <div class="box confirm" role="alertdialog" aria-label={confirm.title ?? `Confirm ${confirm.label}`}>
      <div class="head">
        <strong>{confirm.title ?? `Confirm ${confirm.label}`}</strong>
        <span class="expires">{remaining}s</span>
      </div>
      <div class="body mono">{confirm.detail || confirm.message}</div>
      {#if confirm.route}
        <ul class="route">
          {#each confirm.route as line (line)}<li>{line}</li>{/each}
        </ul>
      {/if}
      <div class="actions">
        {#if confirm.armed}
          <span>Press <kbd>Enter</kbd> again to {confirm.verb ?? 'run'}, <kbd>Esc</kbd> to cancel</span>
        {:else}
          <span>Release <kbd>Enter</kbd>, then press it again to {confirm.verb ?? 'run'}</span>
        {/if}
        <button type="button" onclick={() => oncancel?.()}>Cancel</button>
      </div>
    </div>
  {/if}
</div>

<div class="feedback" aria-live="polite" aria-atomic="true">
  {#if !confirm && busyLabel}
    <div class="line busy">{busyLabel}…</div>
  {:else if !confirm && outcome}
    <div class="box" class:ok={outcome.kind === 'success'} class:err={outcome.kind === 'error'}>
      <div class="head">
        <strong>{outcome.kind === 'success' ? outcome.label : `${outcome.label} failed`}</strong>
        <span class="msg">{outcome.undone ? 'Marked as a misfire' : outcome.message}</span>
        <button type="button" class="x" aria-label="Dismiss (Escape)" title="Dismiss (Esc)" onclick={() => ondismiss?.()}>×</button>
      </div>
      {#if outcome.text}
        <pre class="body text">{outcome.text}</pre>
      {/if}
      <div class="actions">
        {#if outcome.text}
          <button type="button" onclick={() => oncopy?.(outcome.text!)}>Copy text</button>
        {/if}
        {#if canUndo && !outcome.undone}
          <button type="button" onclick={() => onundo?.()} title="Record that this was the wrong action (Ctrl+Z, within 5 s)">Wrong action <kbd>Ctrl</kbd>+<kbd>Z</kbd></button>
        {/if}
        {#if outcome.kind === 'error'}
          <span class="dim">{outcome.next ?? 'Change the input, or pick another card with Tab.'}</span>
        {/if}
        <span class="dim right"><kbd>Esc</kbd> dismiss</span>
      </div>
    </div>
  {:else if !confirm && hint}
    <div class="line hint">{hint}</div>
  {/if}
</div>

<style>
  .feedback:empty {
    display: none;
  }
  .box {
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--bg-raised);
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .box.ok {
    border-color: var(--ok);
  }
  .box.err {
    border-color: var(--danger);
    background: var(--danger-soft);
  }
  .box.confirm {
    border-color: var(--warn);
    background: var(--warn-soft);
  }
  .head {
    display: flex;
    align-items: baseline;
    gap: 10px;
    min-width: 0;
  }
  .msg {
    color: var(--text-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }
  .expires {
    margin-left: auto;
    font-family: var(--mono);
    color: var(--warn);
  }
  .body {
    margin: 0;
    max-height: 120px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .route {
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: 12px;
    color: var(--text-dim);
  }
  .route li + li {
    color: var(--text-faint);
  }
  .mono,
  .text {
    font-family: var(--mono);
    font-size: 12.5px;
  }
  .actions {
    display: flex;
    gap: 8px;
    align-items: center;
    font-size: 12px;
    color: var(--text-dim);
  }
  .actions button {
    font-size: 12px;
    padding: 2px 8px;
  }
  .confirm .actions button {
    margin-left: auto;
  }
  .dim {
    color: var(--text-faint);
  }
  .right {
    margin-left: auto;
  }
  .x {
    border: none;
    background: none;
    padding: 0 4px;
    font-size: 16px;
    line-height: 1;
    color: var(--text-faint);
  }
  .line {
    font-size: 12px;
    color: var(--text-dim);
    padding: 2px 2px;
  }
  .line.busy {
    color: var(--accent);
  }
</style>
