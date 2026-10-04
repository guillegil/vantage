import type { Change, IconName } from '../contract';
import { plural } from './format';

// What a result did compared with its run's baseline: the previous finished run on the same branch,
// else the project's previous run. The server decides each result's change; these name and draw it.
export const CHANGES: Record<Change, { word: string; group: string; one: string; many: string }> = {
  'new-failure': {
    word: 'new failure',
    group: 'New failures',
    one: 'new failure',
    many: 'new failures',
  },
  'still-failing': {
    word: 'still failing',
    group: 'Still failing',
    one: 'still failing',
    many: 'still failing',
  },
  fixed: { word: 'fixed', group: 'Fixed', one: 'fixed', many: 'fixed' },
  'new-test': { word: 'new test', group: 'New tests', one: 'new test', many: 'new tests' },
  removed: { word: 'removed', group: 'Removed tests', one: 'removed test', many: 'removed tests' },
  'not-reached': {
    word: 'not reached',
    group: 'Not reached',
    one: 'test not reached',
    many: 'tests not reached',
  },
};

// The changes that draw nothing in a line take an icon instead.
export const CHANGE_ICON: Partial<Record<Change, IconName>> = {
  'new-test': 'plus',
  removed: 'circle-minus',
  'not-reached': 'square',
};

export function isChange(c: unknown): c is Change {
  return typeof c === 'string' && Object.hasOwn(CHANGES, c);
}

// The queue's order for the changes a run's own results carry; the tests it lacks come after them.
export const CHANGE_ORDER: readonly Change[] = [
  'new-failure',
  'still-failing',
  'fixed',
  'new-test',
];

// The short line under a queue row: what it was, or since when it fails.
export function changeNote(
  r: { change?: Change | null; was?: string | null; streak?: { runs: number; since?: string } },
  base: string | null,
): string | null {
  const was = r.was ? r.was + (base ? ` in ${base}` : ' before') : null;
  switch (r.change) {
    case 'new-failure':
      return was || (base ? `not in ${base}` : 'new test');
    case 'still-failing':
      if (r.streak?.runs)
        return `failing ${plural(r.streak.runs, 'run', 'runs')}${r.streak.since ? ` since ${r.streak.since}` : ''}`;
      return was;
    case 'fixed':
      return was;
    case 'new-test':
      return base ? `not in ${base}` : null;
    case 'removed':
      return r.was
        ? `${r.was}${base ? ` in ${base}` : ''}, not collected in this run`
        : 'not collected in this run';
    case 'not-reached':
      return r.was
        ? `${r.was}${base ? ` in ${base}` : ''}; the run stopped first`
        : 'the run stopped first';
    default:
      return null;
  }
}
