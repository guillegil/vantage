import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { TabItem } from '../contract';
import { Tabs } from './Tabs';

const TABS: TabItem[] = [
  { id: 'evidence', label: 'Evidence', panel: <p>The traceback.</p> },
  {
    id: 'checks',
    label: 'Checks',
    count: 3,
    alert: true,
    plugin: 'pytest-verify',
    panel: <p>3 of 6 checks failed.</p>,
  },
  { id: 'history', label: 'History', count: 1234, panel: <p>The last runs.</p> },
];

function selected(): string | null {
  return screen
    .getAllByRole('tab')
    .filter((t) => t.getAttribute('aria-selected') === 'true')
    .map((t) => t.id.split('-').pop() ?? '')
    .join();
}

describe('roles', () => {
  it('is a named tablist whose active tab and panel name each other', () => {
    render(<Tabs label="Result" tabs={TABS} idPrefix="r-" />);
    expect(screen.getByRole('tablist', { name: 'Result' })).toBeInTheDocument();
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
    expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1, -1]);
    expect(tabs.map((t) => t.id)).toEqual(['r-evidence', 'r-checks', 'r-history']);
    for (const t of tabs) expect(t).toHaveAttribute('aria-controls', 'r-panel');
    const panel = screen.getByRole('tabpanel', { name: 'Evidence' });
    expect(panel).toHaveAttribute('id', 'r-panel');
    expect(panel).toHaveAttribute('tabindex', '0');
    expect(panel).toHaveTextContent('The traceback.');
  });

  it('counts in figures, marks a count of failures, and names the plugin a tab comes from', () => {
    const { container } = render(<Tabs label="Result" tabs={TABS} />);
    const counts = container.querySelectorAll('.dl-tab__count');
    expect([...counts].map((c) => c.textContent)).toEqual(['3', '1,234']);
    expect(counts[0]).toHaveClass('dl-tab__count--alert');
    expect(counts[1]).not.toHaveClass('dl-tab__count--alert');
    const plug = container.querySelector('.dl-tab__plugin');
    expect(plug?.querySelector('title')).toHaveTextContent('From pytest-verify');
  });

  it('without panels is the tablist alone, each tab controlling the element it names', () => {
    const { container } = render(
      <Tabs
        label="View"
        className="extra"
        tabs={[
          { id: 'a', label: 'Changes', controls: 'queue' },
          { id: 'b', label: 'All results', controls: 'all' },
        ]}
      />,
    );
    expect(container.firstElementChild).toHaveAttribute('role', 'tablist');
    expect(container.firstElementChild).toHaveClass('dl-tabs', 'extra');
    expect(screen.queryByRole('tabpanel')).toBeNull();
    expect(screen.getAllByRole('tab').map((t) => t.getAttribute('aria-controls'))).toEqual([
      'queue',
      'all',
    ]);
  });

  it('shows hidden characters in a label as code points', () => {
    const { container } = render(
      <Tabs label="Collections" tabs={[{ id: 'x', label: 'chec‮ks', panel: null }]} />,
    );
    expect(screen.getByRole('tab')).toHaveTextContent('checU+202Eks');
    expect(container.textContent).not.toContain('‮');
  });
});

describe('keys', () => {
  it('moves with the arrows, Home and End, selecting as it goes and wrapping round', async () => {
    const onChange = vi.fn();
    render(<Tabs label="Result" tabs={TABS} onChange={onChange} />);
    await userEvent.tab();
    expect(screen.getByRole('tab', { name: 'Evidence' })).toHaveFocus();
    await userEvent.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: /Checks/ })).toHaveFocus();
    expect(selected()).toBe('checks');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('3 of 6 checks failed.');
    await userEvent.keyboard('{End}');
    expect(selected()).toBe('history');
    await userEvent.keyboard('{ArrowRight}');
    expect(selected()).toBe('evidence');
    await userEvent.keyboard('{ArrowLeft}');
    expect(selected()).toBe('history');
    await userEvent.keyboard('{Home}');
    expect(selected()).toBe('evidence');
    expect(screen.getByRole('tab', { name: 'Evidence' })).toHaveFocus();
    expect(onChange.mock.calls.map((c) => c[0])).toEqual([
      'checks',
      'history',
      'evidence',
      'history',
      'evidence',
    ]);
  });

  it('moves from the strip into the panel with Tab', async () => {
    render(<Tabs label="Result" tabs={TABS} />);
    await userEvent.tab();
    await userEvent.tab();
    expect(screen.getByRole('tabpanel')).toHaveFocus();
  });

  it('leaves other keys alone', () => {
    render(<Tabs label="Result" tabs={TABS} />);
    const first = screen.getByRole('tab', { name: 'Evidence' });
    first.focus();
    expect(fireEvent.keyDown(first, { key: 'ArrowDown' })).toBe(true);
    expect(selected()).toBe('evidence');
  });
});

describe('the chosen tab', () => {
  it('starts on defaultValue, and follows clicks', async () => {
    render(<Tabs label="Result" tabs={TABS} defaultValue="checks" />);
    expect(selected()).toBe('checks');
    await userEvent.click(screen.getByRole('tab', { name: /History/ }));
    expect(selected()).toBe('history');
    expect(screen.getByRole('tabpanel', { name: /History/ })).toHaveTextContent('The last runs.');
  });

  it('is the page’s when given value: a click asks, and only a new value moves it', async () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <Tabs label="Result" tabs={TABS} value="evidence" onChange={onChange} />,
    );
    await userEvent.click(screen.getByRole('tab', { name: /Checks/ }));
    expect(onChange).toHaveBeenCalledWith('checks');
    expect(selected()).toBe('evidence');
    rerender(<Tabs label="Result" tabs={TABS} value="checks" onChange={onChange} />);
    expect(selected()).toBe('checks');
  });
});
