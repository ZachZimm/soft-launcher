<script lang="ts">
  import type { CardLayout } from '../lib/layout';
  import { pct, type ModelNotice } from '../ui/format';

  export interface CardInfo {
    label: string;
    detail: string;
    error?: string;
    needsConfirm: boolean;
    confidence: number;
    /** Read-only computed result (calc, convert), shown before anything runs. */
    result?: { status: 'pending' | 'ready' | 'error'; given: string; text: string };
  }

  interface Props {
    cards: CardLayout[];
    info: (id: string) => CardInfo;
    /** Card Enter acts on (selected or leader). */
    targetId: string | null;
    /** True when the user explicitly selected a card with Tab or the pointer. */
    explicit: boolean;
    primedId: string | null;
    holdId: string | null;
    confirmId: string | null;
    showNumbers: boolean;
    easeMs: number;
    dimmed: boolean;
    loading: boolean;
    emptyMessage: string;
    /** Model loading or failure, shown instead of the empty message. */
    notice?: ModelNotice | null;
    onretry?: () => void;
    idPrefix: string;
    label: string;
    onpress?: (id: string, e: PointerEvent) => void;
    onrelease?: (e: PointerEvent) => void;
    /** Measured row width in px, bound by the parent to size the layout. */
    width?: number;
  }

  let {
    cards,
    info,
    targetId,
    explicit,
    primedId,
    holdId,
    confirmId,
    showNumbers,
    easeMs,
    dimmed,
    loading,
    emptyMessage,
    notice = null,
    onretry,
    idPrefix,
    label,
    onpress,
    onrelease,
    width = $bindable(0),
  }: Props = $props();
</script>

<div
  class="row"
  class:dimmed
  role="listbox"
  id="{idPrefix}-row"
  aria-label={label}
  aria-busy={loading}
  bind:clientWidth={width}
  style="--ease: {easeMs}ms"
>
  {#if cards.length === 0 && notice}
    <div class="notice {notice.tone}" role="status">
      <span class="notice-icon" aria-hidden="true">{notice.tone === 'busy' ? '' : '!'}</span>
      <span class="notice-text">
        <strong>{notice.title}</strong>
        {#if notice.detail}<span class="notice-detail">{notice.detail}</span>{/if}
      </span>
      {#if notice.retry}
        <button type="button" class="retry" onclick={() => onretry?.()} title="Retry now (Ctrl+R)">Retry <kbd>Ctrl</kbd>+<kbd>R</kbd></button>
      {/if}
    </div>
  {:else if cards.length === 0}
    <div class="empty" class:loading>
      {#if loading}
        <span class="skeleton" style="width: 38%"></span>
        <span class="skeleton" style="width: 26%"></span>
        <span class="skeleton" style="width: 18%"></span>
      {/if}
      <span class="empty-text">{emptyMessage}</span>
    </div>
  {/if}
  {#each cards as card (card.id)}
    {@const i = info(card.id)}
    <div
      class="card"
      class:leader={card.leader}
      class:target={targetId === card.id}
      class:explicit={explicit && targetId === card.id}
      class:primed={primedId === card.id}
      class:holding={holdId === card.id}
      class:confirming={confirmId === card.id}
      class:invalid={!!i.error}
      role="option"
      id="{idPrefix}-card-{card.id}"
      aria-selected={targetId === card.id}
      aria-label="{i.label}{i.result?.status === 'ready' ? `, ${i.result.given} = ${i.result.text}` : i.detail ? `, ${i.detail}` : ''}{primedId === card.id ? ', primed: Enter runs it' : ''}{i.needsConfirm ? ', needs confirmation' : ''}{i.error ? `, ${i.error}` : ''}"
      tabindex="-1"
      style="transform: translateX({card.x}px); width: {card.width}px; opacity: {card.opacity};"
      onpointerdown={(e) => onpress?.(card.id, e)}
      onpointerup={(e) => onrelease?.(e)}
      onpointercancel={(e) => onrelease?.(e)}
    >
      <div class="title">
        <span class="name">{i.label}</span>
      </div>
      {#if i.result && !i.error}
        <div class="detail result-line {i.result.status}" title={i.result.status === 'ready' ? `${i.result.given} = ${i.result.text}` : i.result.text || i.detail}>
          <span class="given">{i.result.given}</span>
          <span class="eq" aria-hidden="true">=</span>
          {#if i.result.status === 'ready'}
            <strong class="answer">{i.result.text}</strong>
          {:else if i.result.status === 'pending'}
            <span class="answer-pending">…</span>
          {:else}
            <span class="answer-error">{i.result.text}</span>
          {/if}
        </div>
      {:else}
        <div class="detail" class:error={!!i.error} title={i.error ?? i.detail}>{i.error ?? i.detail}</div>
      {/if}
      <div class="foot">
        {#if i.error}
          {#if targetId === card.id}<span class="cue">can’t run</span>{/if}
        {:else if primedId === card.id}
          <span class="cue primed-cue"><kbd>↵</kbd> runs</span>
        {:else if targetId === card.id}
          <span class="cue">hold <kbd>↵</kbd></span>
        {/if}
        {#if i.needsConfirm}<span class="tag warn" title="Runs only after an explicit second Enter">confirm</span>{/if}
        {#if showNumbers}
          <span class="num" title="Probability{card.leader ? ' / model confidence' : ''}">{pct(card.p)}{card.leader ? ` · c ${pct(i.confidence)}` : ''}</span>
        {/if}
      </div>
      {#if holdId === card.id}<span class="hold-bar" aria-hidden="true"></span>{/if}
    </div>
  {/each}
</div>

<style>
  .row {
    position: relative;
    height: 78px;
    width: 100%;
    transition: opacity var(--ease) ease-out;
  }
  .row.dimmed {
    opacity: 0.55;
  }
  .card {
    position: absolute;
    top: 0;
    left: 0;
    height: 78px;
    padding: 8px 10px 6px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--bg-card);
    display: flex;
    flex-direction: column;
    gap: 2px;
    overflow: hidden;
    user-select: none;
    cursor: default;
    transition:
      transform var(--ease) ease-out,
      width var(--ease) ease-out,
      opacity var(--ease) ease-out,
      border-color var(--ease) ease-out,
      box-shadow var(--ease) ease-out;
  }
  .card.leader {
    background: var(--bg-card-lead);
  }
  .card.target {
    border-color: var(--line-strong);
  }
  .card.explicit {
    border: 1px dashed var(--accent);
  }
  .card.primed {
    border: 1px solid var(--accent);
    box-shadow: 0 0 0 1px var(--accent) inset;
  }
  .card.confirming {
    border: 1px solid var(--warn);
    box-shadow: 0 0 0 1px var(--warn) inset;
  }
  .card.invalid .name {
    color: var(--text-dim);
  }
  .title {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }
  .name {
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tag {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 0 4px;
    border-radius: 3px;
    flex: none;
  }
  .tag.warn {
    color: var(--warn);
    background: var(--warn-soft);
  }
  .detail {
    color: var(--text-dim);
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-height: 16px;
  }
  .result-line {
    display: flex;
    align-items: baseline;
    gap: 6px;
    font-size: 13px;
  }
  .given {
    font-family: var(--mono);
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 0 1 auto;
    min-width: 0;
  }
  .eq {
    color: var(--text-faint);
    flex: none;
  }
  .answer {
    font-family: var(--mono);
    font-size: 16px;
    font-weight: 700;
    color: var(--accent);
    flex: none;
    max-width: 70%;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .answer-pending {
    color: var(--text-faint);
  }
  .answer-error {
    color: var(--text-faint);
    font-style: italic;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .detail.error {
    color: var(--text-faint);
    font-style: italic;
  }
  .foot {
    margin-top: auto;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--text-faint);
    min-height: 16px;
    white-space: nowrap;
  }
  .cue {
    display: inline-flex;
    gap: 4px;
    align-items: center;
  }
  .primed-cue {
    color: var(--accent);
  }
  .num {
    margin-left: auto;
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
  }
  .hold-bar {
    position: absolute;
    left: 0;
    bottom: 0;
    height: 3px;
    width: 100%;
    background: var(--accent);
    transform-origin: left;
    animation: hold 150ms linear forwards;
  }
  @keyframes hold {
    from {
      transform: scaleX(0);
    }
    to {
      transform: scaleX(1);
    }
  }
  .empty {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 4px;
    color: var(--text-faint);
    font-size: 13px;
  }
  .empty.loading .empty-text {
    position: absolute;
    left: 12px;
    bottom: 6px;
    font-size: 12px;
  }
  .skeleton {
    height: 78px;
    border-radius: var(--radius);
    background: linear-gradient(90deg, var(--bg-card), var(--line), var(--bg-card));
    background-size: 200% 100%;
    animation: shimmer 1.4s linear infinite;
    opacity: 0.6;
  }
  .notice {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 14px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--bg-raised);
  }
  .notice.error {
    border-color: var(--danger);
    background: var(--danger-soft);
  }
  .notice-icon {
    flex: none;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    font-weight: 700;
    font-size: 13px;
  }
  .busy .notice-icon {
    border: 2px solid var(--line-strong);
    border-top-color: var(--accent);
    animation: spin 0.9s linear infinite;
  }
  .error .notice-icon {
    color: var(--bg);
    background: var(--danger);
  }
  .notice-text {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .notice-text strong {
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .notice-detail {
    color: var(--text-dim);
    font-size: 12px;
  }
  .retry {
    margin-left: auto;
    flex: none;
    display: inline-flex;
    gap: 4px;
    align-items: center;
    border-color: var(--danger);
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  @keyframes shimmer {
    from {
      background-position: 200% 0;
    }
    to {
      background-position: -200% 0;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .card,
    .row {
      transition: none;
    }
    .skeleton,
    .busy .notice-icon {
      animation: none;
    }
  }
</style>
