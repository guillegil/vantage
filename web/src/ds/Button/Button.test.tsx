import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { Button } from './Button';

it('is a button of its variant, submitting when asked to', () => {
  render(
    <Button variant="primary" type="submit">
      Sign in
    </Button>,
  );
  const button = screen.getByRole('button', { name: 'Sign in' });
  expect(button).toHaveClass('dl-btn', 'dl-btn--primary');
  expect(button).toHaveAttribute('type', 'submit');
});

it('while busy, says so and ignores clicks', () => {
  const onClick = vi.fn();
  render(
    <Button busy busyLabel="Signing in" onClick={onClick}>
      Sign in
    </Button>,
  );
  const button = screen.getByRole('button', { name: 'Signing in' });
  expect(button).toHaveAttribute('aria-busy', 'true');
  expect(button).toHaveAttribute('aria-disabled', 'true');
  fireEvent.click(button);
  expect(onClick).not.toHaveBeenCalled();
});

it('names an icon-only button by its label', () => {
  render(<Button icon="cross" label="Close" />);
  expect(screen.getByRole('button', { name: 'Close' })).toHaveClass('dl-btn--icon');
});

it('gives the reason it cannot be used', () => {
  render(<Button disabledReason="Admins create projects">New project</Button>);
  expect(screen.getByRole('button', { name: 'New project' })).toHaveAccessibleDescription(
    'Admins create projects',
  );
});

it('is a link when given an address', () => {
  render(<Button href="/p/default/runs">Runs</Button>);
  expect(screen.getByRole('link', { name: 'Runs' })).toHaveAttribute('href', '/p/default/runs');
});
