import type { Catalogue, ExecutionResponse, LauncherRuntime, RuntimeSnapshot } from '../lib/types';

// Opening an installed app from its card. The runtime owns validation and the
// native call; this layer covers the moments around it that the user can feel:
//
// - On a fresh first show the app index can arrive before the first native state
//   carries the action catalogue. An Enter pressed in that gap waits briefly for
//   the catalogue instead of failing, then opens the same app, provided the input
//   has not changed. It never waits for the model.
// - A catalogue without a plain open_app executor makes app cards unavailable,
//   with a reason, rather than letting Enter fail.
// - The runtime suspends itself when it dispatches an app. If the launch fails,
//   the same input is restored so the cards and decisions come back for a retry.

export const CATALOGUE_WAIT_MS = 1500;

export type AppExecutor = { state: 'ready' } | { state: 'loading' } | { state: 'unavailable'; reason: string };

/** Whether the current catalogue can open an app directly (mirrors the runtime's own check). */
export function appExecutor(catalogue: Catalogue): AppExecutor {
  if (catalogue.generation === 0 && catalogue.actions.length === 0) return { state: 'loading' };
  const action = catalogue.actions.find((a) => a.id === 'open_app');
  if (!action) return { state: 'unavailable', reason: 'Opening apps is not in the current action catalogue' };
  if (action.exec.kind !== 'app' || action.requiresConfirmation || action.needsText || !['app', 'appId'].includes(action.exec.arg ?? '')) {
    return { state: 'unavailable', reason: 'Opening apps directly is turned off in the current action catalogue' };
  }
  return { state: 'ready' };
}

type Runtime = Pick<LauncherRuntime, 'executeApp' | 'setInput' | 'getSnapshot' | 'subscribe'>;

export type AppLaunchResult = ExecutionResponse | { status: 'cancelled'; actionId: 'open_app'; message: string; args: { appId: string } };

/** Resolves once the catalogue is known or the choice is withdrawn (tick changed); null on timeout. */
function waitForCatalogue(runtime: Runtime, timeoutMs: number): Promise<RuntimeSnapshot | null> {
  const first = runtime.getSnapshot();
  if (appExecutor(first.catalogue).state !== 'loading') return Promise.resolve(first);
  return new Promise((resolve) => {
    let off: (() => void) | undefined;
    const timer = setTimeout(() => {
      off?.();
      resolve(null);
    }, timeoutMs);
    off = runtime.subscribe((s) => {
      if (appExecutor(s.catalogue).state === 'loading' && s.tick === first.tick) return;
      clearTimeout(timer);
      off?.();
      resolve(s);
    });
  });
}

export interface LaunchOptions {
  /** The UI still shows this choice: same input, launcher not dismissed or hidden. */
  isCurrent?: () => boolean;
  timeoutMs?: number;
}

/** Open `appId`, chosen while the input was `input`. */
export async function launchApp(runtime: Runtime, appId: string, input: string, options: LaunchOptions = {}): Promise<AppLaunchResult> {
  const args = { appId };
  const isCurrent = options.isCurrent ?? (() => true);
  const cancelled = (message: string): AppLaunchResult => ({ status: 'cancelled', actionId: 'open_app', args, message });
  // Typing and dismissal both advance the tick; either one withdraws this choice.
  const tick = runtime.getSnapshot().tick;
  const snap = await waitForCatalogue(runtime, options.timeoutMs ?? CATALOGUE_WAIT_MS);
  if (!snap) return { status: 'error', actionId: 'open_app', args, message: 'The launcher is still loading its actions. Press Enter again in a moment.' };
  if (snap.tick !== tick || snap.input !== input || !isCurrent()) return cancelled('The input changed before the app opened');
  const executor = appExecutor(snap.catalogue);
  if (executor.state === 'unavailable') return { status: 'error', actionId: 'open_app', args, message: executor.reason };
  let reply: ExecutionResponse;
  try {
    reply = await runtime.executeApp(appId);
  } catch (err) {
    reply = { status: 'error', actionId: 'open_app', args, message: err instanceof Error ? err.message : String(err) };
  }
  // The runtime suspends itself when it dispatches, so a failed launch leaves no
  // app cards. Restore the same input so they come back and Enter can retry, but
  // only if the user is still looking at this choice: never revive a dismissed
  // launcher or overwrite newer typing.
  if (reply.status !== 'executed' && isCurrent() && runtime.getSnapshot().input === input) runtime.setInput(input);
  return reply;
}
