import { render } from '@testing-library/react';
import { expect, it } from 'vitest';
import { initialsOf, UserChip } from './UserChip';

it('shows two initials and the name, and says which is you', () => {
  const { container } = render(<UserChip name="Ana Ruiz" you />);
  expect(container.firstChild).toHaveTextContent('ARAna Ruiz (you)');
  expect(initialsOf('alice')).toBe('A');
});

it('keeps the name for screen readers when it shows only the monogram', () => {
  const { getByText } = render(<UserChip name="alice" showName={false} />);
  expect(getByText('alice')).toHaveClass('dl-sr');
});
