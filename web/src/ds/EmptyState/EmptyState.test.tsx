import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { EmptyState } from './EmptyState';

it('says what is empty, why, and what to run', () => {
  render(
    <EmptyState title="No runs yet" command="pytest --vantage">
      Runs appear here once a pytest session reports to this server.
    </EmptyState>,
  );
  expect(screen.getByRole('heading', { name: 'No runs yet' })).toBeInTheDocument();
  expect(
    screen.getByText('Runs appear here once a pytest session reports to this server.'),
  ).toHaveClass('dl-empty__text');
  expect(screen.getByText('pytest --vantage')).toBeInTheDocument();
});
