import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { Pager } from './Pager';

it.each([
  [{ shown: 0, hasMore: false }, 'No runs match.'],
  [{ shown: 1, hasMore: false }, 'The only run is shown.'],
  [{ shown: 12, hasMore: false }, 'All 12 runs shown.'],
  [{ shown: 50, hasMore: true }, '50 runs shown, newest first. More exist.'],
  [
    { shown: 1200, hasMore: true, noun: 'results', order: 'in the order pytest reported them' },
    '1,200 results shown, in the order pytest reported them. More exist.',
  ],
])('says what it shows, never a total: %o', (props, words) => {
  render(<Pager {...props} />);
  expect(screen.getByRole('status')).toHaveTextContent(words);
});

it('loads more, busy while it does', () => {
  const onMore = vi.fn();
  const { rerender } = render(<Pager shown={50} hasMore onMore={onMore} pageSize={50} />);
  fireEvent.click(screen.getByRole('button', { name: 'Load 50 more' }));
  expect(onMore).toHaveBeenCalledOnce();
  rerender(<Pager shown={50} hasMore onMore={onMore} loading />);
  expect(screen.getByRole('button', { name: 'Loading' })).toHaveAttribute('aria-busy', 'true');
});
