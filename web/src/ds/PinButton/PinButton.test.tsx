import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { PinButton } from './PinButton';

it('toggles, saying which way', () => {
  const onChange = vi.fn();
  render(<PinButton onChange={onChange} />);
  fireEvent.click(screen.getByRole('button', { name: 'Pin run' }));
  expect(onChange).toHaveBeenCalledWith(true);
  expect(screen.getByRole('button', { name: 'Unpin run' })).toHaveAttribute('aria-pressed', 'true');
});

it('stays focusable when unavailable, with its reason', () => {
  const onChange = vi.fn();
  render(<PinButton disabledReason="Editors and owners can pin runs" onChange={onChange} />);
  const button = screen.getByRole('button', { name: 'Pin run' });
  expect(button).toHaveAttribute('aria-disabled', 'true');
  expect(button).toHaveAccessibleDescription('Editors and owners can pin runs');
  fireEvent.click(button);
  expect(onChange).not.toHaveBeenCalled();
});
