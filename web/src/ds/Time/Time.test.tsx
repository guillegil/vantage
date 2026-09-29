import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { fmtRelative } from '../lib/format';
import { Time } from './Time';

const NOW = '2026-09-27T09:40:00Z';

describe('Time', () => {
  it('is relative in lists, absolute UTC on hover', () => {
    render(<Time value="2026-09-27T09:02:00Z" now={NOW} />);
    const time = screen.getByText('38 min ago');
    expect(time).toHaveAttribute('title', '2026-09-27 09:02:00 UTC');
    expect(time).toHaveAttribute('datetime', '2026-09-27T09:02:00.000Z');
  });

  it('is absolute UTC, with the zone written, in heads', () => {
    render(<Time value="2026-09-27T09:14:03Z" mode="absolute" />);
    expect(screen.getByText('2026-09-27 09:14:03 UTC')).toBeInTheDocument();
  });

  it('prints the date alone on request', () => {
    render(<Time value="2026-09-27T09:14:03Z" mode="date" />);
    expect(screen.getByText('2026-09-27')).toBeInTheDocument();
  });

  it('says nothing was recorded for no time', () => {
    render(<Time value="not a time" />);
    expect(screen.getByTitle('Not recorded')).toHaveTextContent('—');
  });

  it.each([
    ['2026-09-27T09:39:30Z', '30 s ago'],
    ['2026-09-27T07:40:00Z', '2 h ago'],
    ['2026-09-20T09:40:00Z', '7 d ago'],
    ['2026-07-01T09:40:00Z', '2026-07-01'],
    ['2026-09-27T09:41:00Z', 'just now'],
  ])('reads %s as %s', (value, words) => {
    expect(fmtRelative(value, NOW)).toBe(words);
  });
});
