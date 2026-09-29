import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { ProjectSwitcher } from './ProjectSwitcher';

it('lists the projects with their roles, the current one checked, and chooses one', async () => {
  const onSelect = vi.fn();
  render(
    <ProjectSwitcher
      current={{ name: 'firmware', role: 'viewer' }}
      projects={[
        { name: 'firmware', role: 'viewer' },
        { name: 'default', role: 'editor' },
      ]}
      onSelect={onSelect}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: 'firmware' }));
  expect(screen.getByRole('menu', { name: 'Projects' })).toHaveTextContent('Your projects');
  expect(screen.getByRole('menuitem', { name: /firmware/ })).toHaveAttribute(
    'aria-current',
    'true',
  );
  expect(screen.getByRole('menuitem', { name: /firmware/ })).toHaveTextContent('Viewer');
  await userEvent.click(screen.getByRole('menuitem', { name: /default/ }));
  expect(onSelect).toHaveBeenCalledWith({ name: 'default', role: 'editor' });
});

it('shows no badge for a null role, as on an open server', async () => {
  render(
    <ProjectSwitcher
      current={{ name: 'default', role: null }}
      projects={[{ name: 'default', role: null }]}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: 'default' }));
  const item = screen.getByRole('menuitem', { name: /default/ });
  expect(item.querySelector('.dl-role')).toBeNull();
});

it('offers New project only with somewhere to send it', async () => {
  render(<ProjectSwitcher current={{ name: 'default', role: null }} projects={[]} />);
  await userEvent.click(screen.getByRole('button', { name: 'default' }));
  expect(screen.queryByRole('menuitem', { name: 'New project' })).toBeNull();
  expect(
    screen.getByText('No projects you can open yet. An admin of this server can add you to one.'),
  ).toBeInTheDocument();
});
