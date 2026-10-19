<script lang="ts">
  import type { BridgeStatus, CoreError, RuntimeSnapshot } from '../lib/types';
  import { describeError } from '../ui/format';

  interface Props {
    status: RuntimeSnapshot['status'];
    stale: boolean;
    hasCandidates: boolean;
    backendLabel: string;
    /** Decision server host, for readable error copy. */
    host: string;
    p50: number;
    p95: number;
    decisions: number;
    bridge: BridgeStatus | null;
    error: CoreError | null;
    errorCount: number;
    /** What Enter does to the current target. */
    enterHint?: 'open' | 'run' | 'search' | 'hold' | 'confirm' | 'none';
    onsettings?: () => void;
    onclearerror?: () => void;
  }
  let { status, stale, hasCandidates, backendLabel, host, p50, p95, decisions, bridge, error, errorCount, enterHint = 'none', onsettings, onclearerror }: Props = $props();
  const ENTER: Record<NonNullable<Props['enterHint']>, string> = { open: 'open', run: 'run', search: 'search', hold: 'hold to run', confirm: 'confirm', none: '' };

  const state = $derived.by(() => {
    if (status === 'warming') return { text: 'warming model', cls: 'busy' };
    if (status === 'debouncing' || status === 'deciding') return { text: 'deciding', cls: 'busy' };
    if (status === 'stale' || (stale && status === 'ready')) return { text: hasCandidates ? 'stale: showing last answer' : 'no answer', cls: 'stale' };
    if (status === 'ready') return { text: 'ready', cls: 'ok' };
    if (status === 'closed') return { text: 'closed', cls: 'stale' };
    return { text: 'idle', cls: '' };
  });

  const bridgeText = $derived.by(() => {
    if (!bridge) return 'logging off';
    if (bridge.state === 'connected') return bridge.pending ? `log ${bridge.pending} pending` : 'logging';
    if (bridge.state === 'connecting') return 'log connecting';
    if (bridge.state === 'error') return `log offline${bridge.pending ? ` (${bridge.pending} queued)` : ''}`;
    return `log ${bridge.state}`;
  });
  /** Logging only earns a place in the strip when it is not simply working. */
  const bridgeProblem = $derived(!!bridge && (bridge.state === 'error' || bridge.state === 'closed'));
  const details = $derived(
    [`Decision backend: ${backendLabel}`, decisions > 0 ? `Latency p50 ${Math.round(p50)} / p95 ${Math.round(p95)} ms over ${decisions} decisions` : '', `Logging: ${bridgeText}`].filter(Boolean).join('\n'),
  );
</script>

<!-- Healthy details (backend, latency, logging) live in the tooltip; the strip itself
     shows the state, any current problem and the keys. -->
<footer class="strip">
  <span class="state {state.cls}" role="status" aria-live="polite" title={details}><span class="dot" aria-hidden="true"></span>{state.text}</span>
  {#if bridgeProblem}
    <span class="sep" aria-hidden="true">·</span>
    <span class="warn" title={bridge?.error ?? 'soft-if debugger bridge on 127.0.0.1:7331'}>{bridgeText}</span>
  {/if}
  {#if error}
    <span class="error" title="{error.source}: {error.message}">
      <span class="sep" aria-hidden="true">·</span>
      ⚠ {describeError(error, host)}{errorCount > 1 ? ` (+${errorCount - 1})` : ''}
      <button type="button" class="x" aria-label="Hide error" onclick={() => onclearerror?.()}>×</button>
    </span>
  {/if}
  <span class="right">
    {#if !error}{#if ENTER[enterHint]}<kbd>↵</kbd> {ENTER[enterHint]} · {/if}<kbd>Tab</kbd> next · <kbd>Esc</kbd> back{/if}
    <button type="button" class="gear" onclick={() => onsettings?.()} title="Settings (Ctrl+,)" aria-label="Settings (Ctrl+comma)">⚙</button>
  </span>
</footer>

<style>
  .strip {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11.5px;
    color: var(--text-faint);
    white-space: nowrap;
    min-width: 0;
    overflow: hidden;
  }
  .state {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    color: var(--text-dim);
  }
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--text-faint);
  }
  .ok .dot {
    background: var(--ok);
  }
  .busy .dot {
    background: var(--accent);
    animation: pulse 0.9s ease-in-out infinite;
  }
  .stale {
    color: var(--warn);
  }
  .stale .dot {
    background: var(--warn);
  }
  .warn {
    color: var(--warn);
  }
  /* An error takes the room it needs; the key hints give way. */
  .error {
    color: var(--danger);
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
    flex: 1 1 auto;
  }
  .right {
    margin-left: auto;
    display: inline-flex;
    gap: 6px;
    align-items: center;
  }
  .gear,
  .x {
    border: none;
    background: none;
    padding: 0 4px;
    color: var(--text-faint);
    font-size: 14px;
    line-height: 1;
  }
  .gear:hover,
  .x:hover {
    color: var(--text);
  }
  @keyframes pulse {
    50% {
      opacity: 0.35;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .busy .dot {
      animation: none;
    }
  }
</style>
