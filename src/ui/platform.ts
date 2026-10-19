import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import { getCurrentWindow } from '@tauri-apps/api/window';
import type { Catalogue } from '../lib/types';

// Thin wrapper over the Tauri surface the UI uses directly: environment
// detection, invoke for the native bridge, and the four native events.

export const hasNative = (): boolean => {
  try {
    return isTauri();
  } catch {
    return false;
  }
};

export const windowVisible = (): Promise<boolean> => getCurrentWindow().isVisible();

export const nativeInvoke = <T>(command: string, args?: Record<string, unknown>): Promise<T> => invoke<T>(command, args);

export interface NativeEventHandlers {
  visibility(visible: boolean): void;
  catalogue(catalogue: Catalogue): void;
  error(source: string, message: string): void;
  timer(seconds: number): void;
}

export async function listenNative(h: NativeEventHandlers): Promise<UnlistenFn> {
  const offs = await Promise.all([
    listen<{ visible: boolean }>('native:visibility', (e) => h.visibility(!!e.payload?.visible)),
    listen<Catalogue>('native:catalogue', (e) => h.catalogue(e.payload)),
    listen<{ source: string; message: string }>('native:error', (e) => h.error(String(e.payload?.source ?? 'native'), String(e.payload?.message ?? 'Unknown native error'))),
    listen<{ actionId: string; seconds: number }>('native:timer', (e) => h.timer(Number(e.payload?.seconds) || 0)),
  ]);
  return () => {
    for (const off of offs) off();
  };
}
