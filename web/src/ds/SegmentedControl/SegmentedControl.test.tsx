import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { SegmentedControlProps } from '../contract';
import { SegmentedControl } from './SegmentedControl';

const SCOPE: SegmentedControlProps['options'] = [
  { value: 'all', label: 'Everyone' },
  { value: 'mine', label: 'Mine' },
  { value: 'shared', label: 'Shared with me' },
];

function checked(): string {
  return screen
    .getAllByRole('radio')
    .filter((r) => r.getAttribute('aria-checked') === 'true')
    .map((r) => r.textContent)
    .join();
}

describe('roles', () => {
  it('is a named radio group with the first option chosen', () => {
    const { container } = render(<SegmentedControl label="Show runs from" options={SCOPE} />);
    expect(screen.getByRole('radiogroup', { name: 'Show runs from' })).toHaveClass('dl-seg');
    const radios = screen.getAllByRole('radio');
    expect(radios.map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false']);
    expect(radios.map((r) => r.tabIndex)).toEqual([0, -1, -1]);
    for (const r of radios) expect(r).toHaveClass('dl-seg__opt');
    expect(container.querySelector('svg')).toBeNull();
  });

  it('starts on defaultValue, and draws an option’s icon beside its label', () => {
    const { container } = render(
      <SegmentedControl
        label="Visibility"
        defaultValue="project"
        options={[
          { value: 'private', label: 'Private', icon: 'lock' },
          { value: 'project', label: 'Project', icon: 'users' },
        ]}
      />,
    );
    expect(checked()).toBe('Project');
    expect(screen.getByRole('radio', { name: 'Project' })).toHaveAttribute('tabindex', '0');
    expect(container.querySelectorAll('.dl-seg__opt svg.dl-icon')).toHaveLength(2);
  });
});

describe('keys', () => {
  it('moves and chooses with the arrows, Home and End, wrapping round', async () => {
    const onChange = vi.fn();
    render(<SegmentedControl label="Show runs from" options={SCOPE} onChange={onChange} />);
    await userEvent.tab();
    expect(screen.getByRole('radio', { name: 'Everyone' })).toHaveFocus();
    await userEvent.keyboard('{ArrowRight}');
    expect(checked()).toBe('Mine');
    expect(screen.getByRole('radio', { name: 'Mine' })).toHaveFocus();
    await userEvent.keyboard('{ArrowRight}{ArrowRight}');
    expect(checked()).toBe('Everyone');
    await userEvent.keyboard('{ArrowLeft}');
    expect(checked()).toBe('Shared with me');
    await userEvent.keyboard('{Home}');
    expect(checked()).toBe('Everyone');
    await userEvent.keyboard('{End}');
    expect(checked()).toBe('Shared with me');
    expect(screen.getByRole('radio', { name: 'Shared with me' })).toHaveFocus();
    expect(onChange.mock.calls.map((c) => c[0])).toEqual([
      'mine',
      'shared',
      'all',
      'shared',
      'all',
      'shared',
    ]);
  });

  it('is one tab stop', async () => {
    render(
      <>
        <SegmentedControl label="Show runs from" options={SCOPE} />
        <button type="button">After</button>
      </>,
    );
    await userEvent.tab();
    await userEvent.tab();
    expect(screen.getByRole('button', { name: 'After' })).toHaveFocus();
  });

  it('leaves other keys alone', () => {
    render(<SegmentedControl label="Show runs from" options={SCOPE} />);
    const first = screen.getByRole('radio', { name: 'Everyone' });
    first.focus();
    expect(fireEvent.keyDown(first, { key: 'ArrowDown' })).toBe(true);
    expect(checked()).toBe('Everyone');
  });
});

it('is the page’s when given value: a click asks, and only a new value moves it', async () => {
  const onChange = vi.fn();
  const { rerender } = render(
    <SegmentedControl label="Show" options={SCOPE} value="all" onChange={onChange} />,
  );
  await userEvent.click(screen.getByRole('radio', { name: 'Mine' }));
  expect(onChange).toHaveBeenCalledWith('mine');
  expect(checked()).toBe('Everyone');
  rerender(<SegmentedControl label="Show" options={SCOPE} value="mine" onChange={onChange} />);
  expect(checked()).toBe('Mine');
});
