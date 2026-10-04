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

it('shows a recorded branch, sha and subject with their hidden characters as code points', () => {
  const rlo = String.fromCodePoint(0x202e);
  const { container } = render(
    <CommitRef branch={`main${rlo}`} sha={`😀${rlo}a1b2c3d4`} subject={`Fix${rlo} it`} />,
  );
  expect(screen.getByTitle(`Branch main⟨U+202E⟩`)).toHaveTextContent('mainU+202E');
  // Seven characters by code point: the emoji is never cut in two.
  expect(screen.getByTitle(`Commit 😀⟨U+202E⟩a1b2c3d4`)).toHaveTextContent('😀U+202Ea1b2c');
  const subject = container.querySelector('.dl-commit__subject');
  expect(subject?.tagName).toBe('BDI');
  expect(subject).toHaveAttribute('title', 'Fix⟨U+202E⟩ it');
  expect(subject).toHaveTextContent('FixU+202E it');
  expect(container.querySelectorAll('.dl-hidden-char')).toHaveLength(3);
  expect(container.textContent).not.toContain(rlo);
});
