import { render } from '@testing-library/react';
import { expect, it } from 'vitest';
import { RoleBadge } from './RoleBadge';

it.each([
  ['owner', 'Owner'],
  ['editor', 'Editor'],
  ['viewer', 'Viewer'],
] as const)('names the %s role', (role, words) => {
  const { container } = render(<RoleBadge role={role} />);
  expect(container.firstChild).toHaveTextContent(words);
});

it('prints nothing on an open server, which checks no role', () => {
  const { container } = render(<RoleBadge role={null} />);
  expect(container.firstChild).toBeNull();
});
