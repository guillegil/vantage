import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { Panel } from './Panel';

it('titles its section at the level asked', () => {
  render(
    <Panel title="Sign in" level={1} flush>
      body
    </Panel>,
  );
  expect(screen.getByRole('heading', { level: 1, name: 'Sign in' })).toHaveClass('dl-panel__title');
  expect(screen.getByText('body').closest('section')).toHaveClass('dl-panel--flush');
});
