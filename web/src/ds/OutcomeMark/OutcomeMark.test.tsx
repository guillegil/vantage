import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { OutcomeMark } from './OutcomeMark';

it('names its outcome in pytest words, with its own glyph and colour', () => {
  render(<OutcomeMark outcome="xpassed" />);
  const mark = screen.getByRole('img', { name: 'xpassed' });
  expect(mark).toHaveClass('dl-o--xpassed');
});

it('takes a label of its own', () => {
  render(<OutcomeMark outcome="failed" label="failed in call" />);
  expect(screen.getByRole('img', { name: 'failed in call' })).toBeInTheDocument();
});
