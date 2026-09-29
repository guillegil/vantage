import type { IconName, Outcome, OutcomeCounts, ResultLike } from '../contract';

export const OUTCOMES: Record<Outcome, { glyph: IconName; ch: string; rank: number }> = {
  passed: { glyph: 'check', ch: '.', rank: 1 },
  xfailed: { glyph: 'circle-cross', ch: 'x', rank: 0 },
  skipped: { glyph: 'dash', ch: 's', rank: -1 },
  xpassed: { glyph: 'circle-check', ch: 'X', rank: 2 },
  failed: { glyph: 'cross', ch: 'F', rank: 3 },
  error: { glyph: 'alert', ch: 'E', rank: 4 },
};

// The order pytest prints its summary counts in.
export const PYTEST_ORDER = [
  'failed',
  'passed',
  'skipped',
  'deselected',
  'xfailed',
  'xpassed',
  'warnings',
  'error',
] as const satisfies readonly (keyof OutcomeCounts)[];

export function isOutcome(o: unknown): o is Outcome {
  return typeof o === 'string' && Object.hasOwn(OUTCOMES, o);
}

export function outcomeOf(r: ResultLike | null | undefined): Outcome {
  return typeof r === 'string' ? r : r?.outcome || 'skipped';
}

export function rankOf(o: string): number {
  return isOutcome(o) ? OUTCOMES[o].rank : -2;
}

export function isFailing(o: string | null | undefined): boolean {
  return o === 'failed' || o === 'error';
}

export function countOutcomes<K extends string>(list: K[]): Partial<Record<K, number>> {
  const c: Partial<Record<K, number>> = {};
  for (const o of list) c[o] = (c[o] || 0) + 1;
  return c;
}

export function partWord(key: string, n: number): string {
  if (key === 'error') return n === 1 ? 'error' : 'errors';
  if (key === 'warnings') return n === 1 ? 'warning' : 'warnings';
  return key;
}

export function describeCounts(c: OutcomeCounts): string {
  const parts: string[] = [];
  for (const k of PYTEST_ORDER) {
    const n = c[k];
    if (n) parts.push(`${n} ${partWord(k, n)}`);
  }
  return parts.length ? parts.join(', ') : 'no tests ran';
}
