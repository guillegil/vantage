import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { Tooltip } from './Tooltip';

it('describes its control, shown on focus and hidden on Escape', () => {
  render(
    <Tooltip content="Editors and owners can pin runs">
      <button type="button">Pin</button>
    </Tooltip>,
  );
  const button = screen.getByRole('button', { name: 'Pin' });
  expect(button).toHaveAccessibleDescription('Editors and owners can pin runs');
  const tip = screen.getByRole('tooltip', { hidden: true });
  expect(tip).not.toBeVisible();
  fireEvent.focus(button);
  expect(screen.getByRole('tooltip')).toBeVisible();
  fireEvent.keyDown(button, { key: 'Escape' });
  expect(screen.getByRole('tooltip', { hidden: true })).not.toBeVisible();
});

it('does not describe what only repeats the name', () => {
  render(
    <Tooltip content="Close" describe={false}>
      <button type="button" aria-label="Close" />
    </Tooltip>,
  );
  expect(screen.getByRole('button', { name: 'Close' })).not.toHaveAttribute('aria-describedby');
});
