import { render } from '@testing-library/react';
import { expect, it } from 'vitest';
import { initialsOf, UserChip } from './UserChip';

it('shows two initials and the name, and says which is you', () => {
  const { container } = render(<UserChip name="Ana Ruiz" you />);
  expect(container.firstChild).toHaveTextContent('ARAna Ruiz (you)');
});

it('takes two letters from a username: its first two, or its parts', () => {
  expect(initialsOf('alice')).toBe('AL');
  expect(initialsOf('ci-bench')).toBe('CB');
  expect(initialsOf('j.doe')).toBe('JD');
  expect(initialsOf('ana_maria_ruiz')).toBe('AR');
  expect(initialsOf('Ana Maria Ruiz')).toBe('AR');
  expect(initialsOf('x')).toBe('X');
  expect(initialsOf('   ')).toBe('?');
});

it('sets a username in mono', () => {
  const { container, rerender } = render(<UserChip name="ci-bench" mono />);
  expect(container.querySelector('.dl-user__name')).toHaveClass('dl-user__name--mono');
  rerender(<UserChip name="Ana Ruiz" />);
  expect(container.querySelector('.dl-user__name')).not.toHaveClass('dl-user__name--mono');
});

it('keeps the name for screen readers when it shows only the monogram', () => {
  const { getByText } = render(<UserChip name="alice" showName={false} />);
  expect(getByText('alice')).toHaveClass('dl-sr');
});
