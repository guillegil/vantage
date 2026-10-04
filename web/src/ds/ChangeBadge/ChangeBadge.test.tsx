import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Change, ChangeBadgeProps } from '../contract';
import { ChangeBadge } from './ChangeBadge';

const WORDS: [Change, string][] = [
  ['new-failure', 'new failure'],
  ['still-failing', 'still failing'],
  ['fixed', 'fixed'],
  ['new-test', 'new test'],
  ['removed', 'removed'],
  ['not-reached', 'not reached'],
];

function text(p: ChangeBadgeProps): string {
  const { container } = render(<ChangeBadge {...p} />);
  return container.textContent ?? '';
}

describe('every change', () => {
  it.each(WORDS)('%s is named "%s" and set in its own class', (change, word) => {
    const { container } = render(<ChangeBadge change={change} />);
    const badge = container.firstElementChild as HTMLElement;
    expect(badge).toHaveClass('dl-change', `dl-change--${change}`);
    expect(badge.querySelector('.dl-change__word')).toHaveTextContent(new RegExp(`^${word}$`));
    expect(badge.querySelector('.dl-change__detail')).toBeNull();
  });

  it('draws a piece of dotline for the changes a line shows, and an icon for the rest', () => {
    for (const [change] of WORDS) {
      const { container, unmount } = render(<ChangeBadge change={change} />);
      const drawn = ['new-failure', 'still-failing', 'fixed'].includes(change);
      expect(container.querySelector('.dl-change__glyph')).toEqual(
        drawn ? expect.anything() : null,
      );
      expect(container.querySelector('.dl-change__icon')).toEqual(drawn ? null : expect.anything());
      expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
      unmount();
    }
  });

  it('lifts a new failure above the track, keeps a known one within it, and rings a fix', () => {
    const lifted = render(<ChangeBadge change="new-failure" />).container;
    expect(Number(lifted.querySelector('.dl-m--failed')?.getAttribute('y'))).toBeLessThan(0);
    const known = render(<ChangeBadge change="still-failing" outcome="error" />).container;
    expect(known.querySelector('.dl-m--failed')).toBeNull();
    for (const bar of known.querySelectorAll('.dl-m--error'))
      expect(Number(bar.getAttribute('y'))).toBeGreaterThanOrEqual(0);
    const fixed = render(<ChangeBadge change="fixed" outcome="skipped" />).container;
    expect(fixed.querySelector('.dl-m--skipped')).not.toBeNull();
    expect(fixed.querySelector('.dl-ring')).not.toBeNull();
  });

  it('draws nothing for a word it does not know', () => {
    const { container } = render(<ChangeBadge change={'renamed' as Change} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('detail', () => {
  const B = '1adf29af';
  it.each<[ChangeBadgeProps, string]>([
    [{ change: 'new-failure', was: 'passed', baseline: B }, 'New failure: passed in 1adf29af'],
    [{ change: 'new-failure', baseline: B }, 'New failure: not in 1adf29af; its first run failed'],
    [
      {
        change: 'still-failing',
        was: 'failed',
        streak: { runs: 4, since: '1ad93e49' },
        baseline: B,
      },
      'Still failing: 4 runs, since 1ad93e49',
    ],
    [{ change: 'still-failing', streak: { runs: 1 }, baseline: B }, 'Still failing: 1 run'],
    [{ change: 'still-failing', was: 'error', baseline: B }, 'Still failing: error in 1adf29af'],
    [{ change: 'still-failing', baseline: B }, 'Still failing'],
    [
      { change: 'fixed', was: 'failed', outcome: 'passed', baseline: B },
      'Fixed: failed in 1adf29af',
    ],
    [{ change: 'fixed', baseline: B }, 'Fixed'],
    [{ change: 'new-test', baseline: B }, 'New test: not in 1adf29af'],
    [
      { change: 'removed', was: 'passed', baseline: B },
      'Removed: passed in 1adf29af, not collected in this run',
    ],
    [{ change: 'removed', baseline: B }, 'Removed: in 1adf29af, not collected in this run'],
    [
      { change: 'not-reached', was: 'skipped', baseline: B },
      'Not reached: skipped in 1adf29af; this run stopped before it',
    ],
    [
      { change: 'not-reached', baseline: B },
      'Not reached: in 1adf29af; this run stopped before it',
    ],
  ])('%o reads "%s"', (p, sentence) => {
    expect(text({ ...p, detail: true })).toBe(sentence);
  });

  it('sets run labels in code', () => {
    const { container } = render(
      <ChangeBadge
        change="still-failing"
        streak={{ runs: 4, since: '1ad93e49' }}
        baseline="1adf29af"
        detail
      />,
    );
    expect([...container.querySelectorAll('code')].map((c) => c.textContent)).toEqual(['1ad93e49']);
    const nf = render(<ChangeBadge change="new-failure" was="passed" baseline="1adf29af" detail />);
    expect(nf.container.querySelector('.dl-change__detail code')).toHaveTextContent('1adf29af');
  });
});
