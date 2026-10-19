import type { Catalogue, NativeBridge, TeachingProposal, TeachingSnapshot } from './types';

export class TeachingHistory {
  private history: string[] = [];
  private streak = 0;
  private proposal?: TeachingProposal;
  private expiresAt = 0;
  private pending = false;
  private error?: string;
  private lastTick = -1;
  constructor(private native: NativeBridge, private now = Date.now) {}
  observe(tick: number, input: string, leadingOther: boolean): void {
    if (tick <= this.lastTick) return;
    this.lastTick = tick;
    this.streak = leadingOther && input.trim() ? this.streak + 1 : 0;
    if (leadingOther && input.trim()) this.history = [...this.history, Array.from(input).slice(0, 700).join('')].slice(-10);
  }
  snapshot(): TeachingSnapshot {
    return { available: this.streak >= 3, otherStreak: this.streak, inputs: [...this.history], pending: this.pending, ...(this.proposal ? { proposal: { ...this.proposal } } : {}), ...(this.error ? { error: this.error } : {}) };
  }
  async propose(): Promise<TeachingProposal> {
    if (this.pending) throw new Error('A teaching request is already pending');
    if (this.streak < 3 || !this.history.length) throw new Error('Teaching requires three consecutive other inputs');
    this.pending = true; this.error = undefined;
    try {
      const proposal = await this.native.proposeTeaching([...this.history]);
      this.proposal = { ...proposal, id: proposal.proposalId };
      this.expiresAt = this.now() + proposal.expiresInMs;
      return { ...this.proposal };
    } catch (error) { this.error = String(error); throw error; }
    finally { this.pending = false; }
  }
  async save(id: string, reviewedToml: string): Promise<Catalogue> {
    if (this.pending) throw new Error('A teaching request is already pending');
    if (!this.proposal || this.proposal.id !== id || this.now() >= this.expiresAt) throw new Error('No current reviewed proposal');
    if (!reviewedToml.trim() || reviewedToml.length > 32768) throw new Error('Reviewed TOML is empty or too large');
    this.pending = true; this.error = undefined;
    try {
      const catalogue = await this.native.saveTeaching(this.proposal.proposalId, reviewedToml);
      this.proposal = undefined; this.streak = 0;
      return catalogue;
    } catch (error) { this.error = String(error); throw error; }
    finally { this.pending = false; }
  }
}
