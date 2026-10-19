import { readFile } from 'node:fs/promises';
import type { Action, NativeBridge, NativeState, RuntimeSnapshot, LauncherRuntime } from '../src/lib/types';

// The benchmark reads descriptions from the actual native-owned catalogue.
// It never executes a catalogue command and uses only synthetic context.
export async function catalogueFixture(): Promise<Action[]> {
  const text = await readFile(new URL('../catalogue.toml', import.meta.url), 'utf8');
  return text.split(/\[\[action\]\]/).slice(1).map(block => {
    const field = (name: string) => block.match(new RegExp(`(?:^|[\\s,{])${name}\\s*=\\s*"([^"\\n]*)"`, 'm'))?.[1];
    const id = field('id'), description = field('description'), kind = field('kind');
    if (!id || !description || !kind) throw new Error('Benchmark could not read catalogue action');
    return {
      id, description, exec: { kind: kind as Action['exec']['kind'], ...(field('name') ? { name: field('name') } : {}), ...(field('arg') ? { arg: field('arg') } : {}), ...(field('template') ? { template: field('template') } : {}) },
      // Match the native catalogue's derived flags, not only optional TOML flags.
      needsText: ['codex', 'bonsai'].includes(kind),
      requiresConfirmation: ['shell', 'hermes'].includes(kind) || ['rename_file', 'move_file', 'kill_process', 'toggle_wifi', 'ssh', 'paste_plain'].includes(field('name') ?? '') || /requires_confirmation\s*=\s*true/.test(block),
    };
  });
}
export function syntheticState(actions: Action[], files = false): NativeState {
  return {
    catalogue: { generation: 1, actions },
    window: { class: 'org.mozilla.firefox', title: 'Synthetic project planning: meeting agenda, release preparation, local documentation, testing notes, maintenance tasks, and this week\'s development schedule' },
    clipboard: { kind: 'text', head: 'Please review the synthetic meeting agenda and send a short reply after checking the development schedule. The documentation describes local testing, pending release tasks, and the weekly team review.' },
    recent: ['agenda_for_the_synthetic_project_planning_and_weekly_team_review.txt','meeting_notes_for_the_local_development_and_release_preparation.md','quarterly_budget_review_and_synthetic_expense_reporting_worksheet.csv','readme_for_the_launcher_runtime_testing_and_maintenance_workflow.md','tasks_for_the_next_release_integration_and_regression_test_cycle.txt'].map((name, i) => ({ name, path: `/home/synthetic/Documents/${name}`, score: 1 - i / 10 })),
    candidates: files ? Array.from({ length: 5 }, (_, i) => ({ name: `download-${i + 1}.pdf`, path: `/home/synthetic/Downloads/download-${i + 1}.pdf`, score: 1 - i / 10, modifiedAt: Date.UTC(2026, 9, 17, 20, i * 5) })) : [],
    apps: [{ id: 'firefox.desktop', name: 'Firefox' }, { id: 'code.desktop', name: 'Code' }],
    time: 'Sat 17 Oct 2026 13:30, afternoon', warnings: [], filesIndexed: 100,
  };
}
export function syntheticNative(state: NativeState): NativeBridge {
  return {
    getState: async () => structuredClone(state),
    execute: async () => { throw new Error('Benchmark never executes actions'); },
    reload: async () => state.catalogue,
    proposeTeaching: async () => { throw new Error('Benchmark never generates text'); },
    saveTeaching: async () => { throw new Error('Benchmark never writes catalogue'); },
  };
}
export function settle(runtime: LauncherRuntime, input: string): Promise<RuntimeSnapshot> {
  runtime.setInput(input);
  const tick = runtime.getSnapshot().tick;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { unsubscribe(); reject(new Error('Runtime did not settle')); }, 5000);
    const unsubscribe = runtime.subscribe(snapshot => {
      if (snapshot.tick === tick && (snapshot.status === 'ready' || snapshot.status === 'stale')) {
        clearTimeout(timer); unsubscribe(); resolve(snapshot);
      }
    });
  });
}
