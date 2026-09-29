import { render } from '@testing-library/react';
import { expect, it } from 'vitest';
import { VisibilityTag } from './VisibilityTag';

it('says who can see a run', () => {
  const { container, rerender } = render(<VisibilityTag visibility="project" project="firmware" />);
  expect(container.firstChild).toHaveTextContent('Project');
  expect(container.firstChild).toHaveAttribute('title', 'Visible to every member of firmware');
  rerender(<VisibilityTag visibility="private" sharedWith={2} />);
  expect(container.firstChild).toHaveTextContent('Private · +2');
});
