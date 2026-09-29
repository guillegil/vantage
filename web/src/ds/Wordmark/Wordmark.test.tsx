import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { Wordmark } from './Wordmark';

it('is the name, lowercase, and a link home when given one', () => {
  render(<Wordmark href="/" />);
  expect(screen.getByRole('link', { name: 'vantage' })).toHaveAttribute('href', '/');
});

it('is plain text without a link', () => {
  const { container } = render(<Wordmark />);
  expect(container.querySelector('span.dl-wordmark')).toHaveTextContent('vantage');
});
