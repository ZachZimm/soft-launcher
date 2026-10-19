<script lang="ts">
  import type { TeachingSnapshot } from '../lib/types';

  interface Props {
    open: boolean;
    teaching: TeachingSnapshot;
    onclose: () => void;
    onpropose: () => Promise<void>;
    onsave: (proposalId: string, toml: string) => Promise<string>;
  }
  let { open, teaching, onclose, onpropose, onsave }: Props = $props();

  let dialog: HTMLDialogElement | undefined = $state();
  let draft = $state('');
  let draftFor = $state<string | null>(null);
  let reviewed = $state(false);
  let saving = $state(false);
  let saved = $state<string | null>(null);
  let error = $state<string | null>(null);

  const proposal = $derived(teaching.proposal);

  $effect(() => {
    if (!dialog) return;
    if (open && !dialog.open) {
      saved = null;
      error = null;
      dialog.showModal();
    } else if (!open && dialog.open) dialog.close();
  });

  // Load each new proposal into the editor once; later edits are the user's.
  $effect(() => {
    if (proposal && proposal.id !== draftFor) {
      draft = proposal.toml;
      draftFor = proposal.id;
      reviewed = false;
      saved = null;
    }
  });

  async function propose() {
    error = null;
    saved = null;
    try {
      await onpropose();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  async function save() {
    if (!proposal || !reviewed) return;
    saving = true;
    error = null;
    try {
      saved = await onsave(proposal.id, draft);
      draftFor = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      saving = false;
    }
  }
</script>

<dialog bind:this={dialog} class="teach" aria-labelledby="teach-title" onclose={onclose}>
  <form method="dialog" class="wrap" onsubmit={(e) => e.preventDefault()}>
    <header>
      <h2 id="teach-title">Teach a new action</h2>
      <button type="button" class="x" aria-label="Close (Escape)" onclick={onclose}>×</button>
    </header>

    <p class="lead">These recent inputs fit no action. A draft catalogue entry is generated from them; nothing is written until you review and save it.</p>
    <ol class="inputs" aria-label="Recent unmatched inputs">
      {#each teaching.inputs as input, i (i)}
        <li>{input}</li>
      {:else}
        <li class="none">No unmatched inputs recorded yet.</li>
      {/each}
    </ol>

    {#if !proposal}
      <div class="row">
        <button type="button" class="primary" onclick={propose} disabled={teaching.pending || teaching.inputs.length === 0}>
          {teaching.pending ? 'Drafting…' : 'Draft entry'}
        </button>
        <span class="dim">Codex drafts one <code>[[action]]</code> entry.</span>
      </div>
    {:else}
      <label class="editor">
        <span>Proposed entry (editable TOML)</span>
        <textarea bind:value={draft} rows="8" spellcheck="false" disabled={saving}></textarea>
      </label>
      <label class="check">
        <input type="checkbox" bind:checked={reviewed} disabled={saving} />
        I reviewed this entry and want it appended to <code>catalogue.toml</code>
      </label>
      <div class="row">
        <button type="button" class="primary" onclick={save} disabled={!reviewed || saving || !draft.trim()}>{saving ? 'Saving…' : 'Save to catalogue'}</button>
        <button type="button" onclick={propose} disabled={teaching.pending || saving}>{teaching.pending ? 'Drafting…' : 'Draft again'}</button>
        <button type="button" onclick={onclose}>Discard</button>
      </div>
    {/if}

    {#if saved}<p class="ok" role="status">{saved}</p>{/if}
    {#if error || teaching.error}<p class="err" role="alert">{error ?? teaching.error}</p>{/if}
  </form>
</dialog>

<style>
  dialog.teach {
    width: min(620px, calc(100vw - 32px));
    padding: 0;
    border: 1px solid var(--line-strong);
    border-radius: var(--radius);
    background: var(--bg-raised);
    color: var(--text);
  }
  dialog.teach::backdrop {
    background: rgb(0 0 0 / 0.5);
  }
  .wrap {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 14px 16px 16px;
  }
  header {
    display: flex;
    align-items: center;
  }
  h2 {
    font-size: 15px;
    margin: 0;
  }
  .x {
    margin-left: auto;
    border: none;
    background: none;
    font-size: 18px;
    color: var(--text-faint);
  }
  .lead {
    margin: 0;
    color: var(--text-dim);
    font-size: 13px;
  }
  .inputs {
    margin: 0;
    padding-left: 22px;
    font-family: var(--mono);
    font-size: 12.5px;
    color: var(--text-dim);
    max-height: 140px;
    overflow: auto;
  }
  .none {
    list-style: none;
    margin-left: -22px;
    font-family: var(--font);
  }
  .editor {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 12px;
    color: var(--text-dim);
  }
  textarea {
    font-family: var(--mono);
    font-size: 12.5px;
    background: var(--bg-input);
    border: 1px solid var(--line-strong);
    border-radius: var(--radius-sm);
    padding: 8px;
    resize: vertical;
  }
  .check {
    display: flex;
    gap: 8px;
    align-items: center;
    font-size: 13px;
  }
  .row {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .dim {
    color: var(--text-faint);
    font-size: 12px;
  }
  code {
    font-family: var(--mono);
    font-size: 12px;
  }
  .ok {
    margin: 0;
    color: var(--ok);
  }
  .err {
    margin: 0;
    color: var(--danger);
  }
</style>
