import { describe, expect, it, vi } from 'vitest';
import { createLauncherRuntime } from '../lib/loop';
import { action, backendFixture, nativeFixture, stateFixture } from '../lib/core-test-fixtures';
import type { App, NativeBridge } from '../lib/types';
import { appExecutor, launchApp } from './appLaunch';

const BRAVE: App = { id: 'brave-browser.desktop', name: 'Brave', genericName: 'Web Browser', keywords: ['browser'] };
const BROGUE: App = { id: 'brogue-ce.desktop', name: 'Brogue CE', genericName: 'Roguelike game' };
const openApp = action('open_app', 'app', 'app');

function setup(over: { getStateDelayMs?: number; execute?: NativeBridge['execute'] } = {}) {
  const state = stateFixture([openApp, action('web_search'), action('other', 'none', 'none')]);
  const native = nativeFixture(state);
  const getState = native.getState;
  native.getState = async (input) => {
    if (over.getStateDelayMs) await new Promise((r) => setTimeout(r, over.getStateDelayMs));
    return getState(input);
  };
  native.getApps = async () => [BRAVE, BROGUE];
  native.execute = vi.fn(over.execute ?? (async (actionId: string) => ({ status: 'executed' as const, actionId, message: 'Opened' })));
  // A model that never answers: app launching must not depend on it.
  const runtime = createLauncherRuntime({ native, backend: backendFixture(() => new Promise(() => undefined)), bridge: null });
  return { runtime, native };
}

describe('app executor availability', () => {
  it('is loading until the first catalogue arrives, then ready for a plain open_app', () => {
    expect(appExecutor({ generation: 0, actions: [] }).state).toBe('loading');
    expect(appExecutor({ generation: 1, actions: [openApp] }).state).toBe('ready');
  });

  it('is unavailable, with a reason, when open_app is missing, guarded or rebound', () => {
    expect(appExecutor({ generation: 1, actions: [action('web_search')] })).toMatchObject({ state: 'unavailable' });
    expect(appExecutor({ generation: 1, actions: [{ ...openApp, requiresConfirmation: true }] })).toMatchObject({ state: 'unavailable' });
    expect(appExecutor({ generation: 1, actions: [action('open_app', 'input', 'shell')] })).toMatchObject({ state: 'unavailable' });
  });
});

describe('opening an app from its card', () => {
  it('waits for the catalogue when Enter comes right after a fresh show, then opens the same app once', async () => {
    const { runtime, native } = setup({ getStateDelayMs: 60 });
    void runtime.open();
    await new Promise((r) => setTimeout(r, 5));
    runtime.setInput('br');
    expect(runtime.getSnapshot().appCandidates[0]?.app.id).toBe('brave-browser.desktop');
    expect(appExecutor(runtime.getSnapshot().catalogue).state).toBe('loading');
    const reply = await launchApp(runtime, 'brave-browser.desktop', 'br');
    expect(reply.status).toBe('executed');
    expect(native.execute).toHaveBeenCalledExactlyOnceWith('open_app', { appId: 'brave-browser.desktop' });
    await runtime.close();
  });

  it('gives up with a press-again message if the catalogue never arrives', async () => {
    const { runtime, native } = setup({ getStateDelayMs: 10_000 });
    void runtime.open();
    await new Promise((r) => setTimeout(r, 5));
    runtime.setInput('br');
    const reply = await launchApp(runtime, 'brave-browser.desktop', 'br', { timeoutMs: 30 });
    expect(reply).toMatchObject({ status: 'error', message: expect.stringMatching(/press Enter again/i) });
    expect(native.execute).not.toHaveBeenCalled();
    await runtime.close();
  });

  it('does not open anything if the input changed while waiting', async () => {
    const { runtime, native } = setup({ getStateDelayMs: 40 });
    void runtime.open();
    await new Promise((r) => setTimeout(r, 5));
    runtime.setInput('br');
    const pending = launchApp(runtime, 'brave-browser.desktop', 'br');
    runtime.setInput('bro');
    expect((await pending).status).toBe('cancelled');
    expect(native.execute).not.toHaveBeenCalled();
    await runtime.close();
  });

  it('restores the cards after a failed launch so one more Enter can retry', async () => {
    let fail = true;
    const { runtime, native } = setup({
      execute: async (actionId) => {
        if (fail) throw new Error('spawn failed');
        return { status: 'executed', actionId, message: 'Opened' };
      },
    });
    void runtime.open();
    await new Promise((r) => setTimeout(r, 5));
    runtime.setInput('brave browser');
    const first = await launchApp(runtime, 'brave-browser.desktop', 'brave browser');
    expect(first.status).toBe('error');
    expect(first.message).toMatch(/Brave/);
    const after = runtime.getSnapshot();
    expect(after.input).toBe('brave browser');
    expect(after.appCandidates.map((c) => c.app.id)).toEqual(['brave-browser.desktop']);
    expect(after.localAppMetrics.failures).toBe(1);
    fail = false;
    expect((await launchApp(runtime, 'brave-browser.desktop', 'brave browser')).status).toBe('executed');
    expect(native.execute).toHaveBeenCalledTimes(2);
    expect(runtime.getSnapshot().metrics.launches).toBe(0);
    await runtime.close();
  });

  it('cancels a waiting launch when the launcher is dismissed', async () => {
    const { runtime, native } = setup({ getStateDelayMs: 40 });
    void runtime.open();
    await new Promise((r) => setTimeout(r, 5));
    runtime.setInput('br');
    const pending = launchApp(runtime, 'brave-browser.desktop', 'br');
    await runtime.dismiss();
    expect((await pending).status).toBe('cancelled');
    expect(native.execute).not.toHaveBeenCalled();
    await runtime.close();
  });

  it('does not revive a dismissed launcher or overwrite newer typing after a failed launch', async () => {
    let release!: () => void;
    const { runtime } = setup({
      execute: () => new Promise((_, reject) => (release = () => reject(new Error('spawn failed')))),
    });
    void runtime.open();
    await new Promise((r) => setTimeout(r, 5));
    runtime.setInput('br');
    let current = true;
    const pending = launchApp(runtime, 'brave-browser.desktop', 'br', { isCurrent: () => current });
    await new Promise((r) => setTimeout(r, 5));
    // The user hides the launcher while the native call is still running.
    current = false;
    await runtime.dismiss();
    release();
    expect((await pending).status).toBe('error');
    const after = runtime.getSnapshot();
    expect(after.appCandidates).toEqual([]);
    expect(after.status).toBe('idle');
    await runtime.close();
  });
});
