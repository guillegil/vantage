import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { Command } from './Command';

it('prints a command after its prompt, and copies it', async () => {
  const writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  render(<Command text="pytest --vantage" />);
  expect(screen.getByText('pytest --vantage')).toBeInTheDocument();
  expect(screen.getByText('$')).toHaveClass('dl-cmd__prompt');
  fireEvent.click(screen.getByRole('button', { name: 'Copy command' }));
  expect(writeText).toHaveBeenCalledWith('pytest --vantage');
  expect(await screen.findAllByText('Copied')).not.toHaveLength(0);
});

it('leaves out its copy button on request', () => {
  render(<Command text="vantage push" copy={false} prompt={false} />);
  expect(screen.queryByRole('button')).toBeNull();
  expect(screen.queryByText('$')).toBeNull();
});
