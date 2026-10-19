import { expect, it } from 'vitest';
import { RuntimeMetrics, percentile } from './metrics';

it('counts settled primed fires without dismissals and tracks undo within five seconds', () => {
  let now = 0; const metrics = new RuntimeMetrics(() => now);
  metrics.decision(80, false); metrics.decision(120, true); metrics.timeout();
  metrics.outcome('fire', true, .9, true);
  metrics.outcome('dismiss', true, .9, false, true);
  expect(metrics.snapshot().primed_acceptance).toBeNull();
  now = 4999; expect(metrics.undo('fire')).toBe(true); expect(metrics.undo('fire')).toBe(false);
  now = 5001;
  expect(metrics.snapshot()).toMatchObject({ decisions: 2, latency_p50_ms: 80, latency_p95_ms: 120, timedOut: 1, launches: 1, dismissals: 1, primed_acceptance: 0, misfires_per_100: 100, other_rate: .5 });
  expect(metrics.snapshot().ece).toBeCloseTo(.9);
  metrics.outcome('late', true, .9, true); now += 5001; expect(metrics.undo('late')).toBe(false);
  expect(percentile([], .95)).toBe(0);
});
