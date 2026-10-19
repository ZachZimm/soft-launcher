<script lang="ts">
  import { onMount, tick, untrack } from 'svelte';
  import DistributionRow, { type CardInfo } from './components/DistributionRow.svelte';
  import AppRow from './components/AppRow.svelte';
  import OtherBar from './components/OtherBar.svelte';
  import FeedbackPanel, { type ConfirmView, type Outcome } from './components/FeedbackPanel.svelte';
  import StatusStrip from './components/StatusStrip.svelte';
  import TeachDialog from './components/TeachDialog.svelte';
  import SettingsPanel from './components/SettingsPanel.svelte';
  import { EMPTY_ORDER, layoutRow, placeRow, type Entry, type OrderState, type RowLayout } from './lib/layout';
  import type { Candidate, InstalledAppCandidate, LauncherRuntime, RuntimeSnapshot } from './lib/types';
  import * as ix from './ui/interaction';
  import { actionLabel, AGENT_DESTINATION, agentInputProblem, argSummary, busyText, describeError, isAgentAction, isGuarded, modelNotice, tidyResult } from './ui/format';
  import { layoutParams, saveSettings, type UiSettings } from './ui/settings';
  import { appExecutor, launchApp } from './ui/appLaunch';
  import { isSlowWarning, loadLedger, recordSlow, saveLedger, slowAllowed, stripErrors } from './ui/notices';
  import type { RecordedTick, Recording } from './demo/recording';
  import type { AppEnv } from './ui/env';

  interface Props {
    runtime: LauncherRuntime;
    env: AppEnv;
  }
  let { runtime, env }: Props = $props();

  const UNDO_WINDOW_MS = 5000;
  /**
   * A distribution that holds steady this long counts as another hysteresis tick.
   * Without it a decisive new leader would wait behind a stale one for as long as
   * the user stops typing. Any keystroke leaves the ready state and cancels it.
   */
  const SETTLE_MS = 180;
  const RECORDING_TICKS = 20;
  const clock = () => performance.now();

  // ---- runtime snapshot ------------------------------------------------------
  // The runtime and env are fixed for this component's lifetime (main.ts remounts on change).
  let snap = $state<RuntimeSnapshot>(untrack(() => runtime.getSnapshot()));
  let input = $state('');
  let settings = $state<UiSettings>(untrack(() => ({ ...env.settings })));
  let rowWidth = $state(0);
  let now = $state(clock());

  // ---- row layout (hysteresis state is not reactive: it advances once per decision)
  let order: OrderState = EMPTY_ORDER;
  let lastDecisionId: string | undefined;
  let row = $state<RowLayout>({ cards: [], hidden: [], state: EMPTY_ORDER });

  // ---- interaction ---------------------------------------------------------
  let ui = $state<ix.InteractionState>({ ...ix.INITIAL });
  let outcome = $state<Outcome | null>(null);
  let busyLabel = $state<string | null>(null);
  let settingsOpen = $state(false);
  let teachOpen = $state(false);
  let errorHiddenAt = $state(0);
  let toast = $state<string | null>(null);
  let inputEl: HTMLInputElement | undefined = $state();
  let stackEl: HTMLDivElement | undefined = $state();
  /** This shown session already ended (Escape or an executed action); a later native hide is not another dismissal. */
  let sessionEnded = false;
  let recording: RecordedTick[] = [];
  let recordingStart = 0;
  let recordingCount = $state(0);

  const params = $derived(layoutParams(settings, rowWidth || 720));
  const byId = $derived(new Map(snap.candidates.map((c) => [c.action.id, c])));

  // ---- installed apps ---------------------------------------------------------
  // Local code matches, shown first and independent of the model. The runtime
  // clears them while it dispatches one; keep showing the list the user chose from
  // until the launch finishes.
  let frozenApps = $state<InstalledAppCandidate[] | null>(null);
  let openingKey = $state<string | null>(null);
  const apps = $derived(frozenApps ?? snap.appCandidates);
  const executor = $derived(appExecutor(snap.catalogue));
  const appsUnavailable = $derived(executor.state === 'unavailable' ? executor.reason : null);
  /**
   * The model's generic "Open app" card adds nothing next to real app cards, and
   * with no matching app it can only fail; the app row covers both cases.
   */
  const hideOpenApp = (c: Candidate) => c.action.id === 'open_app' && (apps.length > 0 || !!c.argumentError);
  /** Nothing typed yet: the model's guesses over an empty input are noise, so show none. */
  const blank = $derived(input.trim() === '');
  const entries = $derived<Entry[]>(blank ? [] : snap.candidates.filter((c) => c.action.id !== 'other' && !hideOpenApp(c)).map((c) => ({ id: c.action.id, p: c.probability })));
  const otherMass = $derived(byId.get('other')?.probability ?? 0);
  const topId = $derived(snap.candidates[0]?.action.id ?? null);
  const ready = $derived(snap.status === 'ready' && !snap.stale);
  const showingStale = $derived(snap.status === 'stale');
  const leaderId = $derived(row.cards[0]?.id ?? null);

  /** The leader is primed when it is the model's top choice, in the act band, with valid args and no confirmation gate. */
  function isPrimed(c: Candidate | undefined): boolean {
    return !!c && c.action.id === topId && c.band === 'act' && !c.argumentError && !isGuarded(c.action);
  }
  // Primed outline stays on the last decision while typing (no flicker); Enter itself waits for `ready`.
  const primedId = $derived(leaderId && !showingStale && snap.status !== 'warming' && isPrimed(byId.get(leaderId)) ? leaderId : null);

  /** A reason the card can't run: core's argument error, or input too long for the Hermes handoff. */
  const problemOf = (c: Candidate): string | undefined => c.argumentError ?? agentInputProblem(c.action, c.args);

  /** Action cards Enter must not try to run, with the reason to show instead. */
  const blocked = $derived(
    Object.fromEntries(
      row.cards.flatMap((card) => {
        const c = byId.get(card.id);
        const problem = c && problemOf(c);
        return problem ? [[card.id, `${actionLabel(card.id)} can’t run: ${problem}`]] : [];
      }),
    ),
  );

  /**
   * A leader the core allows one explicit Enter without priming it: a valid web
   * search for the current input. Its band and confidence stay as they are.
   * Unlike the primed outline it is never carried over while typing or stale.
   */
  const directId = $derived.by(() => {
    const c = leaderId ? byId.get(leaderId) : undefined;
    return ix.directLeader({
      ready,
      leaderId,
      topId,
      primedId,
      directActivation: c?.directActivation === true,
      blocked: !c || !!problemOf(c),
      pending: !!c?.argumentPending,
      guarded: !!c && isGuarded(c.action),
    });
  });

  /**
   * Leader opacity encodes confidence, which makes a low-confidence card look
   * disabled. A direct card does run on Enter, so keep it legible; its numbers
   * and the missing primed outline still show the confidence honestly.
   */
  const DIRECT_MIN_OPACITY = 0.85;
  const cards = $derived(row.cards.map((c) => (c.id === directId ? { ...c, opacity: Math.max(c.opacity, DIRECT_MIN_OPACITY) } : c)));

  const view = $derived<ix.View>({ ready, order: row.cards.map((c) => c.id), leaderId, primedId: ready ? primedId : null, directId, apps: apps.map((c) => c.app.id), blocked });
  const targetId = $derived(ix.target(ui, view));
  /** The primed and direct cues are only true when Enter would run that card, not when an app card has Enter. */
  const shownPrimedId = $derived(primedId && targetId === primedId ? primedId : null);
  const shownDirectId = $derived(directId && targetId === directId ? directId : null);
  const targetIsApp = $derived(ix.appIdOf(targetId) !== null);

  function confidenceOf(id: string): number {
    return byId.get(id)?.confidence ?? 0;
  }

  function info(id: string): CardInfo {
    const c = byId.get(id);
    if (!c) return { label: actionLabel(id), detail: '', needsConfirm: false, confidence: 0 };
    const problem = problemOf(c);
    return {
      label: actionLabel(id),
      detail: argSummary(c.action, c.args) || c.action.description,
      ...(problem ? { error: problem } : c.argumentPending ? { error: 'choosing between matching files…' } : {}),
      needsConfirm: isGuarded(c.action),
      confidence: c.confidence,
      ...(c.preview && !c.argumentError ? { result: resultOf(c) } : {}),
    };
  }

  /** Card result from the read-only native preview: the input side and the answer. */
  function resultOf(c: Candidate): NonNullable<CardInfo['result']> {
    const p = c.preview!;
    const given = c.args.expr ?? (c.args.value !== undefined && c.args.fromUnit ? `${c.args.value} ${c.args.fromUnit}` : '');
    if (p.status === 'ready' && p.text !== undefined) return { status: 'ready', given, text: tidyResult(p.text) };
    if (p.status === 'error') return { status: 'error', given, text: p.error ?? 'no result' };
    return { status: 'pending', given, text: '' };
  }

  // Advance hysteresis on each new decision; otherwise only re-place cards.
  $effect(() => {
    const decisionId = snap.decision?.id;
    const p = params;
    const e = entries;
    if (decisionId && decisionId !== lastDecisionId && snap.status === 'ready') {
      lastDecisionId = decisionId;
      row = layoutRow(order, e, p, confidenceOf);
      order = row.state;
      record(snap);
      untrack(() => scheduleSettle(decisionId, p.swapTicks - 1));
    } else {
      row = placeRow(order, e, p, confidenceOf);
    }
  });

  let settleTimer: ReturnType<typeof setTimeout> | undefined;
  function scheduleSettle(decisionId: string, remaining: number) {
    clearTimeout(settleTimer);
    if (remaining <= 0 || Object.keys(order.streaks).length === 0) return;
    settleTimer = setTimeout(() => {
      if (snap.decision?.id !== decisionId || snap.status !== 'ready' || snap.stale) return;
      row = layoutRow(order, entries, params, confidenceOf);
      order = row.state;
      scheduleSettle(decisionId, remaining - 1);
    }, SETTLE_MS);
  }

  // Each new view may drop a vanished selection or an expired confirmation. Never executes.
  $effect(() => {
    const v = view;
    untrack(() => apply(ix.refresh(ui, v, clock())));
  });

  // Tick a clock while a confirmation countdown or undo window is visible.
  $effect(() => {
    if (!ui.confirm && !outcome) return;
    const t = setInterval(() => {
      now = clock();
      if (ui.confirm) apply(ix.refresh(ui, view, now));
    }, 250);
    return () => clearInterval(t);
  });

  function record(s: RuntimeSnapshot) {
    const at = Date.now();
    if (recording.length === 0) recordingStart = at;
    const lead = s.candidates[0];
    recording = [
      ...recording,
      {
        input: s.input,
        at: at - recordingStart,
        candidates: s.candidates.filter((c) => c.action.id !== 'other').map((c) => ({ id: c.action.id, p: c.probability })),
        other: s.candidates.find((c) => c.action.id === 'other')?.probability ?? 0,
        confidence: lead?.confidence ?? 0,
        band: lead?.band ?? 'fallback',
        choice: lead?.action.id ?? 'other',
      },
    ].slice(-RECORDING_TICKS);
    recordingCount = recording.length;
  }

  // ---- effects from the interaction machine ----------------------------------
  function apply([next, effects]: ix.Step) {
    ui = next;
    for (const e of effects) void run(e);
  }

  async function run(e: ix.Effect) {
    switch (e.type) {
      case 'scheduleHold':
        setTimeout(() => apply(ix.holdElapsed(ui, e.actionId, clock())), e.delayMs);
        return;
      case 'cancelConfirmation':
        runtime.cancelConfirmation();
        return;
      case 'dismissOutcome':
        outcome = null;
        return;
      case 'dismissLauncher':
        outcome = null;
        sessionEnded = true;
        await runtime.dismiss();
        if (env.native) await env.hideWindow().catch((err) => runtime.reportNativeError('window', String(err)));
        else setInput('');
        return;
      case 'execute':
        return execute(e);
      case 'launchApp':
        return openApp(e.appId);
    }
  }

  async function openApp(appId: string) {
    const chosen = apps.find((c) => c.app.id === appId);
    const name = chosen?.app.name ?? 'the app';
    const typed = input;
    frozenApps = [...apps];
    openingKey = ix.appKey(appId);
    outcome = null;
    let reply;
    try {
      reply = await launchApp(runtime, appId, typed, { isCurrent: () => !sessionEnded && input === typed });
    } finally {
      openingKey = null;
      frozenApps = null;
    }
    const at = clock();
    now = at;
    if (reply.status === 'cancelled') {
      ui = { ...ui, busy: false, hint: sessionEnded ? null : 'The input changed, so nothing was opened' };
      return;
    }
    apply(ix.executed(ui, reply, at));
    if (reply.status === 'executed') {
      sessionEnded = true;
      // The new window is the feedback; get out of its way at once. The browser
      // demo has no window, so it clears the input and reports the dry run.
      if (env.native) void env.hideWindow().catch((err) => runtime.reportNativeError('window', String(err)));
      setInput('');
      if (env.native) {
        // Clearing the input resumed the runtime; suspend it again so nothing is
        // decided while hidden. The app-dispatched decision is excluded from
        // dismissal logging, so this records no rejection.
        void runtime.dismiss();
        return;
      }
    } else if (sessionEnded || input !== typed) {
      return;
    }
    outcome = {
      kind: reply.status === 'executed' ? 'success' : 'error',
      actionId: reply.actionId,
      label: `Open ${name}`,
      // The panel already says "Open Brave failed"; don't repeat it as the reason.
      message: reply.message === `Could not open ${name}` ? 'The app did not start.' : reply.message,
      at,
      ...(reply.status === 'error' ? { next: 'Press Enter to try again, or pick another card with Tab.' } : {}),
    };
  }

  async function execute(e: Extract<ix.Effect, { type: 'execute' }>) {
    const label = actionLabel(e.actionId);
    const action = byId.get(e.actionId)?.action;
    busyLabel = busyText(action, e.actionId);
    outcome = null;
    let reply;
    try {
      reply = await runtime.execute(e.actionId, { primed: e.primed, hold_ms: Math.round(e.holdMs), confirmed: e.confirmed });
    } catch (err) {
      reply = { status: 'error' as const, actionId: e.actionId, message: err instanceof Error ? err.message : String(err), args: {} };
    } finally {
      busyLabel = null;
    }
    const at = clock();
    now = at;
    apply(ix.executed(ui, reply, at));
    if (reply.status === 'confirmationRequired') return;
    outcome = {
      kind: reply.status === 'executed' ? 'success' : 'error',
      actionId: reply.actionId,
      label,
      message: reply.message,
      ...(reply.text !== undefined ? { text: reply.text } : {}),
      ...(reply.outcomeId ? { outcomeId: reply.outcomeId } : {}),
      at,
      // A failed handoff keeps the typed text, so retrying is the same gesture again.
      ...(reply.status === 'error' && action && isAgentAction(action) ? { next: 'Your text is unchanged. Hold Enter to try again, or Esc.' } : {}),
    };
    // Fire-and-forget actions close the overlay; text results stay to be read or copied.
    if (reply.status === 'executed') sessionEnded = true;
    if (reply.status === 'executed' && env.native && reply.text === undefined) {
      setTimeout(() => {
        void env.hideWindow().catch((err) => runtime.reportNativeError('window', String(err)));
        setInput('');
        outcome = null;
      }, 450);
    }
  }

  // ---- input and keys --------------------------------------------------------
  function setInput(value: string) {
    input = value;
    outcome = null;
    apply(ix.inputChanged(ui));
    runtime.setInput(value);
  }

  const canUndo = $derived(!!outcome?.outcomeId && outcome.kind === 'success' && now - outcome.at < UNDO_WINDOW_MS);

  function undo() {
    if (!outcome?.outcomeId || !canUndo) return;
    if (runtime.markUndone(outcome.outcomeId)) outcome = { ...outcome, undone: true };
  }

  function onKeydown(e: KeyboardEvent) {
    if (teachOpen) return;
    if (e.key === ',' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      toggleSettings();
      return;
    }
    if (settingsOpen && e.target !== inputEl) return;
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      apply(ix.enterDown(ui, view, clock(), e.repeat));
    } else if (e.key === 'Tab') {
      e.preventDefault();
      apply(ix.tab(ui, view, e.shiftKey ? -1 : 1));
    } else if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && !e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      e.preventDefault();
      apply(ix.row(ui, view, e.key === 'ArrowDown' ? 1 : -1));
    } else if (e.key === 'Escape') {
      e.preventDefault();
      if (settingsOpen) {
        settingsOpen = false;
        return;
      }
      apply(ix.escape(ui, { outcomeVisible: !!outcome }));
    } else if (e.key.toLowerCase() === 't' && e.altKey && snap.teaching.available) {
      e.preventDefault();
      teachOpen = true;
    } else if (e.key.toLowerCase() === 'r' && (e.ctrlKey || e.metaKey) && notice?.retry) {
      e.preventDefault();
      retry();
    } else if (e.key.toLowerCase() === 'z' && (e.ctrlKey || e.metaKey) && canUndo) {
      e.preventDefault();
      undo();
    }
  }

  function onKeyup(e: KeyboardEvent) {
    if (e.key === 'Enter') apply(ix.enterUp(ui, clock()));
  }

  function onPress(id: string, e: PointerEvent) {
    if (e.button !== 0) return;
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    apply(ix.pointerDown(ui, view, id, clock()));
    inputEl?.focus();
  }

  function onRelease() {
    apply(ix.pointerUp(ui, clock()));
  }

  function onAppRelease(key: string) {
    apply(ix.pointerUp(ui, clock(), key || null));
  }

  async function focusInput(select = false) {
    await tick();
    inputEl?.focus();
    if (select) inputEl?.select();
  }

  function toggleSettings() {
    settingsOpen = !settingsOpen;
    if (!settingsOpen) void focusInput();
  }

  function changeSettings(next: UiSettings) {
    if (next.debounceMs !== settings.debounceMs) runtime.setDebounce(next.debounceMs);
    settings = next;
    saveSettings(next);
  }

  async function copyText(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      flash('Copied to the clipboard');
    } catch {
      flash('The webview refused clipboard access; use the Copy result action');
    }
  }

  async function copyRecording() {
    const rec: Recording = { name: `Live recording ${new Date().toISOString()}`, source: env.model === 'live' ? 'live' : 'synthetic', createdAt: new Date().toISOString(), ticks: recording };
    await copyText(JSON.stringify(rec, null, 2));
  }

  function flash(message: string) {
    toast = message;
    setTimeout(() => {
      if (toast === message) toast = null;
    }, 2500);
  }

  // ---- teaching --------------------------------------------------------------
  async function propose() {
    await runtime.proposeTeaching();
  }
  async function saveTeaching(id: string, toml: string): Promise<string> {
    const catalogue = await runtime.saveTeaching(id, toml);
    return `Saved. Catalogue generation ${catalogue.generation} has ${catalogue.actions.length} actions.`;
  }
  function closeTeach() {
    teachOpen = false;
    void focusInput();
  }

  // ---- native window size ----------------------------------------------------
  // The native window is exactly the panel. Ask it to follow the content height;
  // native clamps it, and the panel scrolls beyond the maximum. The teach dialog is
  // modal over the whole window, so it asks for the maximum.
  const PANEL_PADDING_Y = 22;
  const DIALOG_HEIGHT = 600;
  let contentHeight = $state(0);
  $effect(() => {
    if (!env.native || !stackEl) return;
    const el = stackEl;
    const ro = new ResizeObserver(() => (contentHeight = Math.ceil(el.getBoundingClientRect().height)));
    ro.observe(el);
    return () => ro.disconnect();
  });
  $effect(() => {
    if (!env.native || !contentHeight) return;
    const height = teachOpen ? DIALOG_HEIGHT : contentHeight + PANEL_PADDING_Y;
    const frame = requestAnimationFrame(() => void env.fitWindow?.(height).catch((err) => runtime.reportNativeError('window', String(err))));
    return () => cancelAnimationFrame(frame);
  });

  // ---- lifecycle -------------------------------------------------------------
  onMount(() => {
    const off = runtime.subscribe((s) => (snap = s));
    runtime.setDebounce(settings.debounceMs);
    // A hidden native start (login service) must not warm the model; the first show does.
    void (env.isVisible ? env.isVisible() : Promise.resolve(true))
      .catch(() => true)
      .then((visible) => {
        if (visible) void runtime.open();
      });
    void focusInput();
    let unlisten: (() => void) | undefined;
    let disposed = false;
    void env
      .listen?.({
        visibility(visible) {
          if (!visible) {
            // Hidden by focus loss or the hotkey: drop this input's intent but keep the
            // runtime, so a cold model load in progress can finish for the next open.
            settingsOpen = false;
            teachOpen = false;
            if (!sessionEnded) void runtime.dismiss();
            sessionEnded = true;
            return;
          }
          sessionEnded = false;
          outcome = null;
          // Errors from an earlier open are history, not news. A failure that is
          // still current comes back through the model notice or a fresh error.
          errorHiddenAt = Date.now();
          apply(ix.inputChanged(ui));
          void runtime.open();
          void focusInput(true);
        },
        catalogue(c) {
          // Native already reloaded; only re-decide. Never call runtime.reload() here:
          // native emits this event after a reload, which would loop.
          flash(`Catalogue reloaded: ${c.actions.length} actions (generation ${c.generation})`);
          setInput(input);
        },
        error(source, message) {
          runtime.reportNativeError(source, message);
        },
        timer(seconds) {
          flash(`Timer finished${seconds ? ` (${seconds >= 60 ? `${Math.round(seconds / 60)} min` : `${seconds} s`})` : ''}`);
        },
      })
      .then((u) => {
        if (disposed) u();
        else unlisten = u;
      })
      .catch((err) => runtime.reportNativeError('events', String(err)));
    return () => {
      disposed = true;
      clearTimeout(settleTimer);
      off();
      unlisten?.();
    };
  });

  const host = $derived.by(() => {
    try {
      return new URL(env.decision.url).host;
    } catch {
      return env.decision.url;
    }
  });
  // model.since is wall-clock time, unlike the performance clock above.
  let wallNow = $state(Date.now());
  const notice = $derived(row.cards.length === 0 ? modelNotice(snap.model, host, wallNow) : null);

  // Tick while the model loads so the elapsed time stays current.
  $effect(() => {
    if (snap.model.status !== 'warming') return;
    wallNow = Date.now();
    const t = setInterval(() => (wallNow = Date.now()), 1000);
    return () => clearInterval(t);
  });

  function retry() {
    void runtime.retry();
    void focusInput();
  }

  // ---- status strip errors ---------------------------------------------------
  // Only current problems; see ui/notices.ts for the rules and the slow-warning cap.
  let lastSuccessAt = $state(0);
  let lastSuccessDecision: string | undefined;
  $effect(() => {
    const id = snap.decision?.id;
    if (id && id !== lastSuccessDecision && snap.status === 'ready' && !snap.stale) {
      lastSuccessDecision = id;
      lastSuccessAt = Date.now();
    }
  });
  let ledger = $state(loadLedger());
  const strip = $derived(
    stripErrors(snap.errors, {
      hiddenBefore: errorHiddenAt,
      resolvedBefore: Math.max(lastSuccessAt, snap.model.status === 'ready' ? (snap.model.since ?? 0) : 0),
      bridge: env.model === 'demo' && !env.native ? null : snap.bridge,
      blockingNotice: !!notice,
      ledger,
    }),
  );
  const latestError = $derived(strip.error);
  /**
   * With app cards showing, a model failure is passive: the apps still open. Its
   * notice shrinks to one line, and a timeout there counts against the same
   * slow-warning cap as the strip. Ctrl+R still retries.
   */
  const passiveModelError = $derived(apps.length > 0 && notice?.tone === 'error' ? (snap.model.error ?? null) : null);
  const passiveShown = $derived(!!passiveModelError && (!isSlowWarning(passiveModelError) || slowAllowed(ledger, passiveModelError)));
  // Count a slow-response warning once it is actually on screen.
  $effect(() => {
    const e = strip.error && isSlowWarning(strip.error) ? strip.error : passiveShown && passiveModelError && isSlowWarning(passiveModelError) ? passiveModelError : null;
    if (!e) return;
    const current = untrack(() => ledger);
    const next = recordSlow(current, e);
    if (next !== current) {
      ledger = next;
      saveLedger(next);
    }
  });
  const loading = $derived(!blank && row.cards.length === 0 && (snap.status === 'warming' || snap.status === 'deciding' || snap.status === 'debouncing' || snap.status === 'idle'));
  const emptyMessage = $derived(
    blank
      ? 'Type an app name, a sum, or what you want to do'
      : loading
        ? snap.status === 'warming'
          ? 'Warming up the decision model; the first answer after idle can take a few seconds'
          : 'Deciding…'
        : latestError && !snap.decision
          ? describeError(latestError, host)
          : topId === 'other'
            ? 'Nothing in the catalogue fits this input'
            : 'No likely actions',
  );
  /**
   * With app cards showing, an empty action row ("nothing fits", skeletons, the
   * other bar) is noise: the apps are the answer. Keep the row for real action
   * cards and for a model failure notice with Retry.
   */
  const actionRowShown = $derived(apps.length === 0 || row.cards.length > 0);
  /** One quiet line instead of the empty row while apps are already usable. */
  const actionsPendingText = $derived(
    notice?.tone === 'busy'
      ? `${notice.title} · apps open now`
      : notice?.tone === 'error'
        ? passiveShown
          ? `${notice.title} · Ctrl+R retries`
          : null
        : loading
          ? 'Looking for other actions…'
          : null,
  );
  const otherShown = $derived(!blank && (apps.length === 0 || row.cards.length > 0));
  const confirmView = $derived<ConfirmView | null>(
    // Once the second Enter is sent the panel shows the pending line instead: a
    // cold Hermes Desktop can take seconds, and "press Enter again" would be false.
    ui.confirm && !ui.busy
      ? (() => {
          const c = byId.get(ui.confirm.actionId);
          const agent = c && isAgentAction(c.action);
          return {
            label: actionLabel(ui.confirm.actionId),
            message: ui.confirm.message,
            // The agent gets exactly this text; show all of it, not the card's one-line summary.
            detail: c ? (agent ? (c.args.input ?? '') : argSummary(c.action, c.args)) : '',
            expiresAt: ui.confirm.expiresAt,
            armed: ui.confirm.armed,
            ...(agent
              ? {
                  title: 'Send to Hermes Desktop?',
                  verb: 'send',
                  route: [`Your typed text · ${[...(c.args.input ?? '')].length} characters → ${AGENT_DESTINATION}, as a new conversation`, 'Clipboard is not sent'],
                }
              : {}),
          };
        })()
      : null,
  );
</script>

<svelte:window onkeydown={onKeydown} onkeyup={onKeyup} onblur={() => apply(ix.enterUp(ix.pointerUp(ui, clock())[0], clock()))} />

<main class="launcher" class:native={env.native} class:demo={!env.native} style="--ease-ms: {settings.easeMs}ms">
  <div class="panel">
   <div class="stack" bind:this={stackEl}>
    <div class="input-row">
      <label for="launcher-input" class="visually-hidden">What do you want to do?</label>
      <input
        id="launcher-input"
        bind:this={inputEl}
        value={input}
        oninput={(e) => setInput((e.currentTarget as HTMLInputElement).value)}
        placeholder="Type what you want to do"
        autocomplete="off"
        autocapitalize="off"
        spellcheck="false"
        role="combobox"
        aria-expanded="true"
        aria-controls="launcher-row"
        aria-autocomplete="list"
        aria-activedescendant={targetId ? `launcher-card-${targetId}` : undefined}
        aria-describedby="launcher-help"
      />
      {#if !env.native}
        <span
          class="mode"
          title={env.model === 'live' ? 'Browser demo: simulated native, live decision model; nothing runs' : 'Browser demo: simulated native and offline toy model; nothing runs'}
        >
          {env.model === 'live' ? 'DEMO · live model' : 'DEMO'}
        </span>
      {/if}
    </div>
    <span id="launcher-help" class="visually-hidden">Matching apps come first; Enter opens the highlighted app. Tab and the arrow keys move between cards. Enter runs a primed action or a plain web search; hold Enter for other actions. Escape steps back.</span>

    {#if apps.length > 0}
      <AppRow
        {apps}
        {targetId}
        explicit={!!ui.selectedId}
        unavailable={appsUnavailable}
        {openingKey}
        idPrefix="launcher"
        onpress={onPress}
        onrelease={onAppRelease}
      />
    {/if}

    {#if actionRowShown}
    <DistributionRow
      {cards}
      {info}
      {targetId}
      explicit={!!ui.selectedId}
      primedId={shownPrimedId}
      directId={shownDirectId}
      holdId={ui.hold?.actionId ?? null}
      confirmId={ui.confirm?.actionId ?? null}
      showNumbers={settings.showNumbers}
      easeMs={settings.easeMs}
      dimmed={showingStale}
      {loading}
      {emptyMessage}
      {notice}
      onretry={retry}
      idPrefix="launcher"
      label="Likely actions"
      onpress={onPress}
      onrelease={onRelease}
      bind:width={rowWidth}
    />

    {#if otherShown}
    <OtherBar
      mass={otherMass}
      leading={topId === 'other'}
      offer={snap.teaching.available && apps.length === 0}
      hiddenCount={row.hidden.filter((h) => h.p >= 0.01).length}
      showNumbers={settings.showNumbers}
      easeMs={settings.easeMs}
      onteach={() => (teachOpen = true)}
    />
    {/if}
    {:else if actionsPendingText}
      <p class="actions-pending" class:warn={notice?.tone === 'error'} role="status">{actionsPendingText}</p>
    {/if}

    <FeedbackPanel
      confirm={confirmView}
      {outcome}
      hint={toast ?? ui.hint}
      {busyLabel}
      {canUndo}
      {now}
      oncopy={copyText}
      onundo={undo}
      ondismiss={() => {
        outcome = null;
        void focusInput();
      }}
      oncancel={() => {
        apply(ix.escape(ui, { outcomeVisible: false }));
        void focusInput();
      }}
    />

    <StatusStrip
      status={snap.status}
      stale={snap.stale}
      hasCandidates={row.cards.length > 0}
      backendLabel={env.backendLabel}
      {host}
      p50={snap.metrics.latency_p50_ms}
      p95={snap.metrics.latency_p95_ms}
      decisions={snap.metrics.decisions}
      bridge={env.model === 'demo' && !env.native ? null : snap.bridge}
      error={latestError}
      errorCount={strip.count}
      enterHint={ui.confirm ? 'confirm' : targetIsApp ? 'open' : shownPrimedId ? 'run' : shownDirectId ? 'search' : targetId && !blocked[targetId] ? 'hold' : 'none'}
      onsettings={toggleSettings}
      onclearerror={() => (errorHiddenAt = snap.errors.at(-1)?.at ?? Date.now())}
    />

    {#if !env.native}
      <p class="demo-note">
        Browser demo. The native backend is simulated and every action is a dry run.
        {env.model === 'demo' ? 'Decisions come from an offline keyword model, not Clef.' : `Decisions come from ${env.backendLabel}.`}
        <a href="./replay.html">Replay page</a>
      </p>
    {/if}

    {#if settingsOpen}
      <SettingsPanel
        {settings}
        decision={env.decision}
        decisionEditable={env.model === 'live'}
        recordingTicks={recordingCount}
        onchange={changeSettings}
        ondecision={(d) => env.restart(d)}
        oncopyrecording={copyRecording}
        onclose={toggleSettings}
      />
    {/if}
   </div>
  </div>

  <TeachDialog open={teachOpen} teaching={snap.teaching} onclose={closeTeach} onpropose={propose} onsave={saveTeaching} />
</main>

<style>
  .launcher {
    display: flex;
    justify-content: center;
    padding: 24px 16px;
  }
  .panel {
    width: min(760px, 100%);
    max-height: calc(100vh - 48px);
    overflow-y: auto;
    overscroll-behavior: contain;
    background: var(--bg);
    border: 2px solid var(--line-strong);
    border-radius: 8px;
    box-shadow: 0 5px 30px rgb(0 0 0 / 0.45);
    padding: 12px 14px 10px;
  }
  /* Native: the opaque window is the panel. Hyprland adds border, rounding and shadow
     to a window, but nothing to a layer-shell overlay, so that draws its own border. */
  .launcher.native {
    padding: 0;
    height: 100vh;
  }
  .native .panel {
    width: 100%;
    max-height: 100vh;
    border: none;
    border-radius: 0;
    box-shadow: none;
  }
  :global(html[data-surface='layer']) .native .panel {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
  .stack {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .input-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  input {
    flex: 1;
    min-width: 0;
    font-size: 18px;
    padding: 9px 12px;
    background: var(--bg-input);
    border: 2px solid var(--line-strong);
    border-radius: var(--radius);
    outline: none;
  }
  input::placeholder {
    color: var(--text-faint);
  }
  input:focus {
    border-color: var(--accent);
  }
  .mode {
    flex: none;
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 0.06em;
    padding: 3px 7px;
    border-radius: 4px;
    color: var(--warn);
    background: var(--warn-soft);
    border: 1px solid var(--warn);
  }
  .actions-pending {
    margin: 0;
    font-size: 12px;
    color: var(--text-faint);
  }
  .actions-pending.warn {
    color: var(--warn);
  }
  .demo-note {
    margin: 0;
    font-size: 11.5px;
    color: var(--text-faint);
  }
  .demo-note a {
    color: var(--accent);
  }
</style>
