import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { RunItem } from '../contract';
import { RunList } from './RunList';

const NOW = '2026-09-27T09:40:00Z';
const RUN: RunItem = {
  id: '7f3a2c1e9b0d44a1c3e5f7a9b1d3e5f7',
  label: '7f3a2c1e',
  href: '/runs/7f3a2c1e9b0d44a1c3e5f7a9b1d3e5f7',
  exitStatus: 1,
  counts: { failed: 2, passed: 208, skipped: 3, error: 1 },
  seconds: 132.4,
  branch: 'main',
  sha: '7f3a2c1e9b0d',
  by: { name: 'alice', you: true },
  startedAt: '2026-09-27T09:02:00Z',
  visibility: 'project',
};

describe('RunList', () => {
  it('prints each run by its label, linking to it with the full id', () => {
    render(<RunList runs={[RUN]} now={NOW} />);
    const link = screen.getByRole('link', { name: '7f3a2c1e' });
    expect(link).toHaveAttribute('href', RUN.href);
    expect(link).toHaveAttribute('title', RUN.id);
    const row = screen.getByRole('listitem');
    expect(row).toHaveTextContent('2 failed · 208 passed · 3 skipped · 1 error');
    expect(row).toHaveTextContent('alice (you)');
    expect(row).toHaveTextContent('38 min ago');
    expect(row).toHaveTextContent('2 m 12 s');
    expect(row).toHaveTextContent('Project');
  });

  it('keeps an empty pin cell without onPin, and a pin button with it', () => {
    const { container, rerender } = render(<RunList runs={[RUN]} now={NOW} />);
    expect(container.querySelector('.dl-run__pin')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByRole('button', { name: 'Pin run' })).toBeNull();
    const onPin = vi.fn();
    rerender(<RunList runs={[RUN]} now={NOW} onPin={onPin} />);
    screen.getByRole('button', { name: 'Pin run' }).click();
    expect(onPin).toHaveBeenCalledWith(RUN, true);
  });

  it('counts from the results when no counts are given, and says when there are none', () => {
    render(
      <RunList
        runs={[
          { id: 'a'.repeat(32), results: ['passed', 'failed', 'passed'] },
          { id: 'b'.repeat(32), results: [] },
        ]}
      />,
    );
    const [first, second] = screen.getAllByRole('listitem');
    expect(first).toHaveTextContent('1 failed · 2 passed');
    expect(second).toHaveTextContent('no results');
  });

  it('shows its empty state, or the one it is given', () => {
    const { rerender } = render(<RunList runs={[]} />);
    expect(screen.getByRole('heading', { name: 'No runs yet' })).toBeInTheDocument();
    rerender(<RunList runs={[]} empty={<p>Nothing recorded here.</p>} />);
    expect(screen.getByText('Nothing recorded here.')).toBeInTheDocument();
  });

  it('names its list', () => {
    render(<RunList runs={[RUN]} label="Runs of firmware" />);
    expect(
      within(screen.getByRole('list', { name: 'Runs of firmware' })).getAllByRole('listitem'),
    ).toHaveLength(1);
  });
});
