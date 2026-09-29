import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { MenuButton } from './MenuButton';

it('opens its menu, keeps an unavailable item with its reason, and runs a choice', async () => {
  const onExport = vi.fn();
  const onDelete = vi.fn();
  render(
    <MenuButton
      label="More actions for run 7f3a2c1e"
      items={[
        { label: 'Export run', icon: 'archive', onSelect: onExport },
        { separator: true },
        {
          label: 'Delete run',
          icon: 'trash',
          danger: true,
          onSelect: onDelete,
          disabledReason: 'Editors and owners can delete runs',
        },
      ]}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: 'More actions for run 7f3a2c1e' }));
  const del = screen.getByRole('menuitem', { name: /Delete run/ });
  expect(del).toHaveAttribute('aria-disabled', 'true');
  expect(del).toHaveTextContent('Editors and owners can delete runs');
  await userEvent.click(del);
  expect(onDelete).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('menuitem', { name: 'Export run' }));
  expect(onExport).toHaveBeenCalledOnce();
  expect(screen.queryByRole('menu')).toBeNull();
});
