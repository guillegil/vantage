import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Outcome } from '../contract';
import { HistoryGrid, historyNote } from './HistoryGrid';

const RUNS = ['1a9e0c4d', '1aa1c200', '1aa4b7b3', '7f3a2c1e'].map((id, i) => ({
  id,
  label: id,
  detail: i === 3 ? 'main at 7aa1c5d' : 'main',
}));
const NODE = 'tests/power/test_rails.py::test_rail_under_load[max_load]';

function runsOf(n: number) {
  return Array.from({ length: n }, (_, i) => ({ id: `r${i}`, label: `r${i}` }));
}

describe('historyNote', () => {
  it('says how long a failing test has failed, and since which run', () => {
    expect(historyNote(['passed', 'failed', 'passed', 'failed', 'error'], runsOf(5))).toMatchObject(
      { kind: 'failing', strong: 'failing 2 runs', rest: ' since r3' },
    );
  });

  it('calls a test flaky at three flips, and not at two', () => {
    expect(historyNote(['failed', 'passed', 'failed', 'passed'], runsOf(4))).toEqual({
      kind: 'flaky',
      strong: 'flaky',
      rest: ' · 3 flips in 4 runs',
    });
    expect(historyNote(['passed', 'failed', 'passed', 'failed', 'passed'], runsOf(5))).toEqual({
      kind: 'flaky',
      strong: 'flaky',
      rest: ' · 4 flips in 5 runs',
    });
    expect(historyNote(['passed', 'failed', 'passed'], runsOf(3))).toMatchObject({
      kind: 'passing',
    });
  });

  it('never rounds a pass rate up to 100% while anything is not passing', () => {
    const outs: Outcome[] = Array.from({ length: 1000 }, () => 'passed');
    outs[3] = 'xpassed';
    expect(historyNote(outs, runsOf(1000)).strong).toBe('99.9%');
  });

  it('leaves skips out of the rate and says in how many runs the test ran', () => {
    expect(historyNote([null, 'skipped', 'passed', 'xfailed'], runsOf(4))).toEqual({
      kind: 'passing',
      strong: '100%',
      rest: ' passing · in 3 of 4 runs',
    });
    expect(historyNote([null, null], runsOf(2))).toEqual({
      kind: 'none',
      rest: 'not in these runs',
    });
  });
});

describe('HistoryGrid', () => {
  it('draws a test across runs, oldest to newest, with its note', () => {
    const { container } = render(
      <HistoryGrid
        runs={RUNS}
        rows={[{ nodeid: NODE, outcomes: ['passed', null, 'failed', 'failed'] }]}
      />,
    );
    expect(screen.getByText('4 runs, oldest to newest')).toBeInTheDocument();
    expect(container.querySelector('.dl-hgrid__label')).toHaveTextContent('…/test_rails.py');
    expect(container.querySelectorAll('.dl-m--failed')).toHaveLength(2);
    expect(container.querySelectorAll('.dl-m--none')).toHaveLength(1);
    const slider = screen.getByRole('slider', { name: `History of ${NODE}` });
    expect(slider).toHaveAccessibleDescription('failing 2 runs since 1aa4b7b3');
    expect(slider).toHaveAttribute('aria-valuetext', '7f3a2c1e (main at 7aa1c5d): failed');
    expect(slider).not.toHaveAttribute('aria-keyshortcuts');
  });

  it('moves a cursor from run to run with the arrow keys, reading each run', () => {
    render(
      <HistoryGrid
        runs={RUNS}
        rows={[{ nodeid: NODE, outcomes: ['passed', null, 'failed', 'error'] }]}
      />,
    );
    const slider = screen.getByRole('slider');
    fireEvent.focus(slider);
    fireEvent.keyDown(slider, { key: 'Home' });
    expect(slider).toHaveAttribute('aria-valuenow', '1');
    expect(screen.getByText('1a9e0c4d (main): passed')).toHaveClass('dl-hgrid__readout');
    fireEvent.keyDown(slider, { key: 'ArrowRight' });
    expect(slider).toHaveAttribute('aria-valuetext', '1aa1c200 (main): not in this run');
    fireEvent.keyDown(slider, { key: 'End' });
    expect(slider).toHaveAttribute('aria-valuenow', '4');
    fireEvent.blur(slider);
    expect(screen.queryByText(/\(main at 7aa1c5d\): error/)).toBeNull();
  });

  it('stacks for a side column: no label, the note under the marks', () => {
    const { container } = render(
      <HistoryGrid
        stacked
        runs={RUNS}
        rows={[{ nodeid: NODE, outcomes: ['passed', 'passed', 'passed', 'passed'] }]}
      />,
    );
    expect(container.querySelector('.dl-hgrid--stacked')).not.toBeNull();
    expect(container.querySelector('.dl-hgrid__label')).toBeNull();
    // Too few runs to set two labels apart: the span is said in words, once.
    expect(screen.getAllByText('4 runs, oldest to newest')).toHaveLength(1);
    expect(container.querySelector('.dl-hgrid__axis')).toHaveClass('dl-hgrid__axis--words');
    expect(container.querySelector('.dl-hgrid__note')).toHaveTextContent('100% passing');
  });

  it('puts the first and last labels under the marks where both fit apart', () => {
    const { container, rerender } = render(
      <HistoryGrid
        runs={runsOf(20)}
        rows={[{ nodeid: NODE, outcomes: Array(20).fill('passed') }]}
      />,
    );
    const axis = container.querySelectorAll('.dl-hgrid__axis')[1] as HTMLElement;
    expect(axis).not.toHaveClass('dl-hgrid__axis--words');
    expect(Array.from(axis.children).map((c) => c.textContent)).toEqual(['r0', 'r19']);
    expect(axis.style.width).toBe('198px');
    rerender(
      <HistoryGrid stacked runs={runsOf(1)} rows={[{ nodeid: NODE, outcomes: ['passed'] }]} />,
    );
    expect(screen.getByText('the only run')).toBeInTheDocument();
  });

  it('gives a filled stack the width it has, larger marks for fewer runs', () => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        disconnect() {}
      },
    );
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (
      this: Element,
    ) {
      return { width: this.classList.contains('dl-hgrid__measure') ? 196 : 0 } as DOMRect;
    });
    const rows = [{ nodeid: NODE, outcomes: ['passed', null, 'failed', 'failed'] as Outcome[] }];
    const { container, rerender } = render(<HistoryGrid stacked fill runs={RUNS} rows={rows} />);
    let svg = container.querySelector('svg') as SVGSVGElement;
    // 28px a run, 24px marks: 4 × 28 − 4.
    expect(svg).toHaveAttribute('width', '108');
    expect(svg).toHaveAttribute('height', '24');
    expect(container.querySelector('.dl-m--none')).toHaveAttribute('x', `${28 + 11}`);
    // Not stacked, fill does nothing: 10px a run.
    rerender(<HistoryGrid fill runs={RUNS} rows={rows} />);
    svg = container.querySelector('svg') as SVGSVGElement;
    expect(svg).toHaveAttribute('width', '38');
    expect(svg).toHaveAttribute('height', '16');
    expect(container.querySelector('.dl-hgrid__measure')).toBeNull();
    expect(container.querySelector('.dl-m--none')).toHaveAttribute('x', '13');
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('writes out a hidden character in a run’s detail in the readout', () => {
    const runs = RUNS.map((r) => ({ ...r, detail: `main${String.fromCodePoint(0x202e)}` }));
    render(<HistoryGrid runs={runs} rows={[{ nodeid: NODE, outcomes: ['passed'] }]} />);
    expect(screen.getByRole('slider')).toHaveAttribute(
      'aria-valuetext',
      '1a9e0c4d (main⟨U+202E⟩): passed',
    );
  });

  it('writes out a hidden character in the node id it names the slider by', () => {
    const node = `tests/test_a.py::test_x[${String.fromCodePoint(0x202e)}gnp.exe]`;
    render(<HistoryGrid stacked runs={RUNS} rows={[{ nodeid: node, outcomes: ['passed'] }]} />);
    expect(
      screen.getByRole('slider', { name: 'History of tests/test_a.py::test_x[⟨U+202E⟩gnp.exe]' }),
    ).toBeInTheDocument();
  });

  it('names a change against the baseline in the readout and hides a note on request', () => {
    const { container } = render(
      <HistoryGrid
        runs={RUNS}
        rows={[
          {
            nodeid: NODE,
            outcomes: ['passed', 'passed', 'passed', 'failed'],
            changes: [null, null, null, 'new-failure'],
            note: false,
          },
        ]}
      />,
    );
    const slider = screen.getByRole('slider');
    expect(slider).toHaveAttribute(
      'aria-valuetext',
      '7f3a2c1e (main at 7aa1c5d): failed, new failure',
    );
    expect(container.querySelector('.dl-hgrid__note')).toHaveClass('dl-sr');
  });

  it('opens the result of the run under the pointer, or under the cursor on Enter', () => {
    const onOpen = vi.fn();
    const runs = RUNS.map((r) => ({ ...r, href: `/runs/${r.id}/result` }));
    render(
      <HistoryGrid
        stacked
        runs={runs}
        onOpen={onOpen}
        rows={[{ nodeid: NODE, outcomes: ['passed', 'passed', 'failed', 'failed'] }]}
      />,
    );
    const slider = screen.getByRole('slider');
    expect(slider).toHaveAttribute('aria-keyshortcuts', 'Enter');
    expect(slider).toHaveClass('dl-hgrid__marks--opens');
    slider.getBoundingClientRect = () => ({ left: 0, width: 38 }) as DOMRect;
    fireEvent.click(slider, { clientX: 12 });
    // The address, and the run it belongs to.
    expect(onOpen).toHaveBeenLastCalledWith('/runs/1aa1c200/result', runs[1]);
    fireEvent.focus(slider);
    fireEvent.keyDown(slider, { key: 'Enter' });
    expect(onOpen).toHaveBeenLastCalledWith('/runs/7f3a2c1e/result', runs[3]);
    fireEvent.keyDown(slider, { key: 'Home' });
    fireEvent.keyDown(slider, { key: 'Enter' });
    expect(onOpen).toHaveBeenLastCalledWith('/runs/1a9e0c4d/result', runs[0]);
    expect(onOpen).toHaveBeenCalledTimes(3);
  });

  it('opens nothing where a run has no address', () => {
    const onOpen = vi.fn();
    render(
      <HistoryGrid
        runs={RUNS}
        onOpen={onOpen}
        rows={[{ nodeid: NODE, outcomes: ['passed', 'passed', 'passed', 'passed'] }]}
      />,
    );
    const slider = screen.getByRole('slider');
    expect(slider).not.toHaveClass('dl-hgrid__marks--opens');
    fireEvent.focus(slider);
    fireEvent.keyDown(slider, { key: 'Enter' });
    expect(onOpen).not.toHaveBeenCalled();
  });
});
