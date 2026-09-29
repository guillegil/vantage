import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RunStatus } from './RunStatus';

describe('RunStatus', () => {
  it.each([
    [0, 'all passed'],
    [1, 'tests failed'],
    [2, 'interrupted'],
    [3, 'internal error'],
    [4, 'usage error'],
    [5, 'no tests collected'],
  ])('prints exit %i as %s beside the number', (status, words) => {
    const { container } = render(<RunStatus exitStatus={status} />);
    expect(container.firstChild).toHaveTextContent(`${words}exit ${status}`);
    expect(container.firstChild).toHaveAttribute('title', `${words} · exit ${status}`);
  });

  it('keeps the words for screen readers when compact', () => {
    render(<RunStatus exitStatus={1} compact />);
    expect(screen.getByText('tests failed · exit 1')).toHaveClass('dl-sr');
  });

  it('explains an interrupted or abandoned run', () => {
    const { container, rerender } = render(<RunStatus state="interrupted" />);
    expect(screen.getByText(': a report arrived saying the session was stopped')).toHaveClass(
      'dl-sr',
    );
    rerender(<RunStatus state="abandoned" explain />);
    expect(container.firstChild).toHaveTextContent(
      'abandonedno end recorded and no contact within the grace period',
    );
  });

  it('says a run is running, and when it last reported', () => {
    const { container } = render(<RunStatus state="running" lastContact="4 s ago" />);
    expect(container.firstChild).toHaveTextContent('runninglast contact 4 s ago');
  });
});
