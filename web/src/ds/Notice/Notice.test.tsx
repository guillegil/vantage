import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { Notice } from './Notice';

it('alerts only for danger', () => {
  const { rerender } = render(
    <Notice tone="danger" title="Can’t reach vantage">
      The server did not answer.
    </Notice>,
  );
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Can’t reach vantageThe server did not answer.',
  );
  rerender(<Notice tone="warning">Your session ended</Notice>);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByText('Your session ended')).toBeInTheDocument();
});

it('holds an action', () => {
  render(<Notice action={<button type="button">Try again</button>}>Text</Notice>);
  expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
});
