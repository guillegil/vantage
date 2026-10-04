import { fireEvent, render, screen } from '@testing-library/react';
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

function placeAt(button: { left: number; right: number; top: number; bottom: number }) {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(1000);
  vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(160);
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(120);
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const r = this.getAttribute('aria-haspopup')
      ? button
      : { left: 0, right: 0, top: 0, bottom: 0 };
    return {
      ...r,
      x: r.left,
      y: r.top,
      width: r.right - r.left,
      height: r.bottom - r.top,
    } as DOMRect;
  });
}

it('fixes its menu to the window under the button, kept inside the window', async () => {
  placeAt({ left: 100, right: 140, top: 20, bottom: 52 });
  render(<MenuButton label="More" items={[{ label: 'Export run', onSelect: () => {} }]} />);
  await userEvent.click(screen.getByRole('button', { name: 'More' }));
  const menu = screen.getByRole('menu');
  expect(menu.style.position).toBe('fixed');
  // Aligned to the button's end, which would put it 20px off the window's edge: 8px in instead.
  expect(menu.style.left).toBe('8px');
  expect(menu.style.top).toBe('56px');
  vi.restoreAllMocks();
});

it('opens above the button where the window has no room below', async () => {
  placeAt({ left: 100, right: 140, top: 500, bottom: 532 });
  render(
    <MenuButton label="More" align="start" items={[{ label: 'Export run', onSelect: () => {} }]} />,
  );
  await userEvent.click(screen.getByRole('button', { name: 'More' }));
  const menu = screen.getByRole('menu');
  expect(menu.style.left).toBe('100px');
  expect(menu.style.top).toBe('376px');
  vi.restoreAllMocks();
});

it('closes when the page scrolls, not when its own items do', async () => {
  render(<MenuButton label="More" items={[{ label: 'Export run', onSelect: () => {} }]} />);
  await userEvent.click(screen.getByRole('button', { name: 'More' }));
  fireEvent.scroll(screen.getByRole('menu'));
  expect(screen.getByRole('menu')).toBeInTheDocument();
  fireEvent.scroll(window);
  expect(screen.queryByRole('menu')).toBeNull();
  await userEvent.click(screen.getByRole('button', { name: 'More' }));
  fireEvent(window, new Event('resize'));
  expect(screen.queryByRole('menu')).toBeNull();
});
