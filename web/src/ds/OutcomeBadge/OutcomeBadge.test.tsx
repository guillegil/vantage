import { render } from '@testing-library/react';
import { expect, it } from 'vitest';
import { OutcomeBadge } from './OutcomeBadge';

it('prints the outcome', () => {
  const { container } = render(<OutcomeBadge outcome="skipped" />);
  expect(container.firstChild).toHaveTextContent('skipped');
  expect(container.firstChild).toHaveClass('dl-badge--skipped');
});

it('counts in pytest plurals, grouped the English way', () => {
  const { container, rerender } = render(<OutcomeBadge outcome="error" count={2} />);
  expect(container.firstChild).toHaveTextContent('2errors');
  rerender(<OutcomeBadge outcome="error" count={1} />);
  expect(container.firstChild).toHaveTextContent('1error');
  rerender(<OutcomeBadge outcome="passed" count={19998} />);
  expect(container.firstChild).toHaveTextContent('19,998passed');
});
