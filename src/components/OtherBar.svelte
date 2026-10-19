<script lang="ts">
  import { pct } from '../ui/format';

  interface Props {
    /** Probability mass on "other". */
    mass: number;
    /** "other" currently leads all actions. */
    leading: boolean;
    /** The teach offer is available (other led for three ticks). */
    offer: boolean;
    hiddenCount: number;
    showNumbers: boolean;
    easeMs: number;
    onteach?: () => void;
  }
  let { mass, leading, offer, hiddenCount, showNumbers, easeMs, onteach }: Props = $props();
</script>

<div class="other" style="--ease: {easeMs}ms">
  <div
    class="track"
    role="meter"
    aria-label="Probability that no listed action fits"
    aria-valuemin="0"
    aria-valuemax="100"
    aria-valuenow={Math.round(mass * 100)}
    title="Mass on “other”: {pct(mass)}"
  >
    <span class="fill" class:leading style="transform: scaleX({Math.max(0, Math.min(1, mass))})"></span>
  </div>
  <div class="meta">
    <span class="label" class:leading>
      {leading ? 'No action fits well' : 'other'}{showNumbers ? ` ${pct(mass)}` : ''}
    </span>
    {#if hiddenCount > 0}<span class="more">+{hiddenCount} less likely</span>{/if}
    {#if offer}
      <button type="button" class="teach" onclick={() => onteach?.()}>Teach a new action… <kbd>Alt</kbd>+<kbd>T</kbd></button>
    {/if}
  </div>
</div>

<style>
  .other {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .track {
    height: 3px;
    border-radius: 2px;
    background: var(--line);
    overflow: hidden;
  }
  .fill {
    display: block;
    height: 100%;
    width: 100%;
    background: var(--text-faint);
    transform-origin: left;
    transition: transform var(--ease) ease-out, background-color var(--ease) ease-out;
  }
  .fill.leading {
    background: var(--other);
  }
  .meta {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 22px;
    font-size: 12px;
    color: var(--text-faint);
  }
  .label.leading {
    color: var(--other);
  }
  .more {
    margin-left: auto;
  }
  .teach {
    font-size: 12px;
    padding: 1px 8px;
    border-color: var(--other);
    color: var(--text);
  }
  .more + .teach {
    margin-left: 0;
  }
  .label + .teach {
    margin-left: auto;
  }
</style>
