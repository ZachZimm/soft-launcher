import { describe, expect, it, vi } from 'vitest';
import { TeachingHistory } from './teach';
import { nativeFixture } from './core-test-fixtures';

describe('reviewed teaching', () => {
  it('keeps the last ten other inputs, ignores stale observations and saves only explicit reviewed text', async () => {
    const native = nativeFixture(); native.saveTeaching = vi.fn(native.saveTeaching);
    const history = new TeachingHistory(native);
    for (let i = 1; i <= 12; i++) history.observe(i, `unknown ${i}`, true);
    history.observe(1, 'stale', false);
    expect(history.snapshot()).toMatchObject({ available: true, otherStreak: 12, inputs: Array.from({ length: 10 }, (_, i) => `unknown ${i + 3}`) });
    const proposal = await history.propose();
    expect(native.saveTeaching).not.toHaveBeenCalled();
    expect(proposal.id).toBe(proposal.proposalId);
    await history.save(proposal.id, 'edited TOML from review');
    expect(native.saveTeaching).toHaveBeenCalledWith(proposal.proposalId, 'edited TOML from review');
    await expect(history.save(proposal.id, 'again')).rejects.toThrow('No current');
  });
  it('requires three consecutive other decisions and rejects expired review', async () => {
    let time = 0;
    const history = new TeachingHistory(nativeFixture(), () => time);
    history.observe(1, 'a', true); history.observe(2, 'b', true); history.observe(3, 'search', false);
    await expect(history.propose()).rejects.toThrow('three consecutive');
    for (let i = 4; i <= 6; i++) history.observe(i, `${i}`, true);
    const proposal = await history.propose(); time = 600001;
    await expect(history.save(proposal.id, 'edited')).rejects.toThrow('No current');
  });
});
