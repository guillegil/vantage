import { render } from '@testing-library/react';
import { expect, it } from 'vitest';
import { RetentionTag } from './RetentionTag';

it('says a run is kept, pinned, or when a rule deletes it', () => {
  const { container, rerender } = render(<RetentionTag state="kept" />);
  expect(container.firstChild).toHaveTextContent('kept: no retention rule matches this run');
  rerender(<RetentionTag state="pinned" />);
  expect(container.firstChild).toHaveTextContent('kept · pinned');
  rerender(<RetentionTag state="expires" days={5} rule="after 30 days" />);
  expect(container.firstChild).toHaveTextContent('deleted in 5 d. Rule: after 30 days');
  expect(container.firstChild).toHaveClass('dl-tag--warning');
});
