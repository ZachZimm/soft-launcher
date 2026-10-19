import type { MetricsSnapshot } from './types';

export function percentile(values: readonly number[], q: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.max(0, Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1))]!;
}
interface Outcome { id: string; at: number; primed: boolean; probability: number; accepted: boolean; success: boolean; undone: boolean; dismissed: boolean }
export class RuntimeMetrics {
  private latencies: number[] = [];
  private windows = 0; private others = 0; private timeouts = 0; private superseded = 0;
  private dismissals = 0; private launches = 0; private misfires = 0;
  private outcomes: Outcome[] = [];
  constructor(private now = Date.now) {}
  decision(latency: number, other: boolean): void {
    this.windows++; if (other) this.others++;
    this.latencies.push(Math.max(0, latency));
    if (this.latencies.length > 10000) this.latencies.shift();
  }
  timeout(): void { this.timeouts++; }
  supersede(): void { this.superseded++; }
  outcome(id: string, primed: boolean, probability: number, accepted: boolean, dismissed = false, success = accepted): void {
    if (dismissed) this.dismissals++; else this.launches++;
    if (!success && !dismissed) this.misfires++;
    this.outcomes.push({ id, at: this.now(), primed, probability, accepted, success, undone: false, dismissed });
    if (this.outcomes.length > 10000) this.outcomes.shift();
  }
  undo(id: string): boolean {
    const outcome = this.outcomes.find(item => item.id === id);
    if (!outcome || !outcome.success || outcome.dismissed || outcome.undone || this.now() - outcome.at > 5000) return false;
    outcome.undone = true; this.misfires++; return true;
  }
  snapshot(): MetricsSnapshot {
    const settled = this.outcomes.filter(item => item.primed && !item.dismissed && this.now() - item.at >= 5000);
    let ece = 0;
    for (let i = 0; i < 10; i++) {
      const bucket = this.outcomes.filter(item => Math.min(9, Math.floor(item.probability * 10)) === i);
      if (bucket.length) ece += bucket.length / this.outcomes.length * Math.abs(bucket.reduce((sum, item) => sum + item.probability, 0) / bucket.length - bucket.filter(item => item.accepted && !item.undone).length / bucket.length);
    }
    return {
      decisions: this.windows, latency_p50_ms: percentile(this.latencies, .5), latency_p95_ms: percentile(this.latencies, .95),
      timedOut: this.timeouts, superseded: this.superseded, other_rate: this.windows ? this.others / this.windows : 0,
      launches: this.launches, dismissals: this.dismissals,
      primed_acceptance: settled.length ? settled.filter(item => item.accepted && !item.undone).length / settled.length : null,
      misfires_per_100: this.launches ? this.misfires / this.launches * 100 : 0,
      ece: this.outcomes.length ? ece : null,
    };
  }
}
