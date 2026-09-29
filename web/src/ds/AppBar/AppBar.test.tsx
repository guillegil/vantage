import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { AppBar } from './AppBar';

const PROJECTS = [
  { name: 'firmware', role: 'owner' as const },
  { name: 'default', role: 'editor' as const },
];

describe('AppBar', () => {
  it('opens the account menu, says whose session it is, and signs out', async () => {
    const onSignOut = vi.fn();
    render(
      <AppBar
        project={PROJECTS[0]}
        projects={PROJECTS}
        search={false}
        user={{ name: 'alice' }}
        account={{
          note: 'Signed in as alice until 21:14 UTC',
          items: [{ label: 'Sign out', icon: 'log-out', onSelect: onSignOut }],
        }}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Account: alice' }));
    const menu = screen.getByRole('menu', { name: 'Account' });
    expect(menu).toHaveTextContent('Signed in as alice until 21:14 UTC');
    expect(screen.getByRole('menuitem', { name: 'Sign out' })).toHaveFocus();
    await userEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }));
    expect(onSignOut).toHaveBeenCalledOnce();
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('closes the account menu on Escape, back on its button', async () => {
    render(<AppBar search={false} user={{ name: 'alice' }} account={{ items: [] }} />);
    const button = screen.getByRole('button', { name: 'Account: alice' });
    await userEvent.click(button);
    expect(screen.getByRole('menu')).toHaveTextContent('alice');
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(button).toHaveFocus();
  });

  it('shows no account button without a user, as on an open server', () => {
    render(<AppBar search={false} />);
    expect(screen.queryByRole('button', { name: /^Account/ })).toBeNull();
  });

  it('forwards canCreate: no New project for someone who cannot create one', async () => {
    const onCreate = vi.fn();
    const { rerender } = render(
      <AppBar
        project={PROJECTS[0]}
        projects={PROJECTS}
        search={false}
        canCreate={false}
        onCreate={onCreate}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'firmware' }));
    expect(screen.queryByRole('menuitem', { name: 'New project' })).toBeNull();
    rerender(
      <AppBar
        project={PROJECTS[0]}
        projects={PROJECTS}
        search={false}
        canCreate
        onCreate={onCreate}
      />,
    );
    await userEvent.click(screen.getByRole('menuitem', { name: 'New project' }));
    expect(onCreate).toHaveBeenCalledOnce();
  });

  it('links its nav, marking the current page', () => {
    render(
      <AppBar
        search={false}
        project={PROJECTS[0]}
        nav={[{ id: 'runs', label: 'Runs', href: '/p/firmware/runs' }]}
        active="runs"
      />,
    );
    const nav = screen.getByRole('navigation', { name: 'Project' });
    expect(nav.querySelector('a')).toHaveAttribute('aria-current', 'page');
    expect(nav.querySelector('a')).toHaveAttribute('href', '/p/firmware/runs');
  });

  it('has no search without a catalogue to search', () => {
    render(<AppBar search={false} />);
    expect(screen.queryByRole('searchbox')).toBeNull();
  });
});
