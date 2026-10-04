import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { BaselineNoteProps } from '../contract';
import { BaselineNote } from './BaselineNote';

const B1 = {
  label: '1adf29af',
  href: '/runs/1adf29af3c7e41d09b5a2f6e8c1d4b70',
  id: '1adf29af3c7e41d09b5a2f6e8c1d4b70',
  branch: 'main',
};
const B0 = {
  label: '0b77e4f2',
  href: '/runs/0b77e4f25a6c48d19e3f7b2c4d6a8e01',
  id: '0b77e4f25a6c48d19e3f7b2c4d6a8e01',
  branch: 'main',
};

function sentence(p: BaselineNoteProps): string {
  const { container } = render(<BaselineNote {...p} />);
  return container.querySelector('.dl-baseline')?.textContent ?? '';
}

describe('every case of its table', () => {
  it.each<[string, BaselineNoteProps, string]>([
    [
      'same branch',
      { baseline: B1, earlier: 2520 },
      'Compared with 1adf29af on main, 42 min earlier',
    ],
    [
      'no earlier complete run on its branch',
      { baseline: B0, earlier: 10840, fallback: { reason: 'branch', branch: 'feat/uart-dma' } },
      'No earlier complete run on feat/uart-dma; compared with 0b77e4f2 on main, 3 h earlier',
    ],
    [
      'detached HEAD',
      {
        baseline: B0,
        earlier: 10840,
        fallback: { reason: 'detached', commit: '4b8f6a3c2d91e07f' },
      },
      'No branch recorded (detached HEAD at 4b8f6a3); compared with 0b77e4f2 on main, 3 h earlier',
    ],
    [
      'git information, but no branch or commit',
      { baseline: B0, earlier: 10840, fallback: { reason: 'no-branch' } },
      'No branch recorded; compared with 0b77e4f2 on main, 3 h earlier',
    ],
    [
      'no git information',
      { baseline: B0, earlier: 10840, fallback: { reason: 'no-git' } },
      'Recorded outside a git repository; compared with 0b77e4f2 on main, 3 h earlier',
    ],
    ['nothing earlier was complete', { state: 'none' }, 'Nothing to compare with yet.'],
    ['still running', { state: 'pending' }, 'Compared with its baseline once the session ends.'],
    ['abandoned', { state: 'abandoned' }, 'Not compared: no end was recorded.'],
  ])('%s', (_case, p, words) => {
    expect(sentence(p)).toBe(words);
  });
});

describe('what it says around the table', () => {
  it('is compared when it has a baseline and has nothing to compare with when not', () => {
    expect(sentence({ baseline: B1 })).toBe('Compared with 1adf29af on main');
    expect(sentence({})).toBe('Nothing to compare with yet.');
    expect(sentence({ state: 'compared', baseline: null })).toBe('Nothing to compare with yet.');
    expect(sentence({ state: 'none', baseline: B1, earlier: 60 })).toBe(
      'Nothing to compare with yet.',
    );
  });

  it('says what a run waits for even when given a baseline', () => {
    expect(sentence({ state: 'pending', baseline: B1 })).toBe(
      'Compared with its baseline once the session ends.',
    );
    expect(sentence({ state: 'abandoned', baseline: B1 })).toBe(
      'Not compared: no end was recorded.',
    );
  });

  it('names no branch for a baseline that recorded none', () => {
    expect(
      sentence({
        baseline: { ...B0, branch: null },
        earlier: 10840,
        fallback: { reason: 'no-git' },
      }),
    ).toBe('Recorded outside a git repository; compared with 0b77e4f2, 3 h earlier');
  });

  it('prints how much earlier as a run list prints times', () => {
    expect(sentence({ baseline: B1, earlier: 0.2 })).toBe(
      'Compared with 1adf29af on main, under 1 s earlier',
    );
    expect(sentence({ baseline: B1, earlier: 42 })).toBe(
      'Compared with 1adf29af on main, 42 s earlier',
    );
    expect(sentence({ baseline: B1, earlier: 190000 })).toBe(
      'Compared with 1adf29af on main, 2 d earlier',
    );
  });
});

describe('the baseline label', () => {
  it('links to the baseline run, in mono, with its full id as the tooltip', () => {
    render(<BaselineNote baseline={B1} earlier={2520} />);
    const link = screen.getByRole('link', { name: '1adf29af' });
    expect(link).toHaveAttribute('href', B1.href);
    expect(link).toHaveAttribute('title', B1.id);
    expect(link).toHaveClass('dl-link', 'dl-mono');
  });

  it('is plain mono text without an address, and has no tooltip without an id', () => {
    const { container } = render(<BaselineNote baseline={{ label: '1adf29af' }} />);
    expect(screen.queryByRole('link')).toBeNull();
    const label = container.querySelector('.dl-mono');
    expect(label).toHaveTextContent('1adf29af');
    expect(label).not.toHaveAttribute('title');
  });
});

describe('recorded text', () => {
  it('sets a branch apart and shows its hidden characters as code points', () => {
    const { container } = render(
      <BaselineNote
        baseline={{ ...B0, branch: 'ma​in' }}
        earlier={190000}
        fallback={{ reason: 'branch', branch: 'fix/‮gnilaes' }}
      />,
    );
    const words = container.querySelector('.dl-baseline')?.textContent ?? '';
    expect(words).not.toMatch(/[​‮]/);
    expect(words).toBe(
      'No earlier complete run on fix/U+202Egnilaes; compared with 0b77e4f2 on maU+200Bin, 2 d earlier',
    );
    const isolated = [...container.querySelectorAll('bdi.dl-recorded')].map((b) => b.textContent);
    expect(isolated).toEqual(['fix/U+202Egnilaes', 'maU+200Bin']);
    expect(container.querySelectorAll('.dl-hidden-char')).toHaveLength(2);
  });

  it('cuts a detached commit to seven characters by code point and shows a hidden one', () => {
    const { container } = render(
      <BaselineNote baseline={B0} fallback={{ reason: 'detached', commit: '‮4b8f6a3c2d91' }} />,
    );
    const commit = container.querySelector('bdi.dl-recorded');
    expect(commit).toHaveTextContent(/^U\+202E4b8f6a$/);
    expect(commit?.querySelector('.dl-hidden-char')).not.toBeNull();
  });
});

it('ends the line with its children', () => {
  const { container } = render(
    <BaselineNote baseline={B1} earlier={2520} className="extra">
      <button type="button">Copy rerun of 2 new failures</button>
    </BaselineNote>,
  );
  const line = container.firstElementChild as HTMLElement;
  expect(line).toHaveClass('dl-runhead__base', 'extra');
  expect(line.lastElementChild).toBe(screen.getByRole('button'));
  expect(line.firstElementChild).toHaveClass('dl-baseline');
});
