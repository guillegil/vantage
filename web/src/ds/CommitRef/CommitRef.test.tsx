import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { CommitRef } from './CommitRef';

it('prints the branch, the short sha, a dirty tree and the subject', () => {
  const { container } = render(
    <CommitRef branch="main" sha="7f3a2c1e9b0d" dirty subject="Fix the UART framing" />,
  );
  expect(container.firstChild).toHaveTextContent('main7f3a2c1dirtyFix the UART framing');
  expect(screen.getByTitle('Commit 7f3a2c1e9b0d')).toBeInTheDocument();
  expect(screen.getByTitle('The working tree had uncommitted changes')).toHaveTextContent('dirty');
});

it('says when no commit was recorded', () => {
  render(<CommitRef />);
  expect(screen.getByTitle('The plugin found no git repository')).toHaveTextContent(
    'no commit recorded',
  );
});
