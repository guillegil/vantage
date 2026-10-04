import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type {
  ChangeQueueDetailContext,
  ChangeQueueProps,
  MissingResult,
  QueueResult,
  QueueTotals,
} from '../contract';
import { changeNote } from '../lib/changes';
import { ChangeQueue } from './ChangeQueue';

const BASE = { label: '1adf29af', href: '/runs/1adf29af' };
const PASS = 'test_boot.py::test_cold_boot';
const FIXED = 'test_watchdog.py::test_brownout_recovery';
const NF = 'test_uart.py::test_framing_at_921600';
const STILL = 'test_rails.py::test_rail_under_load[max_load]';
const NEWT = 'test_output.py::test_settle_time[5ms]';
const RAIL = 'test_rails.py::test_rail_ripple[3V3]';
const GONE = 'test_uart.py::test_framing_at_4800';

// One run against its baseline, in collection order.
const RESULTS: QueueResult[] = [
  { nodeid: PASS, outcome: 'passed', seconds: 0.4 },
  { nodeid: FIXED, outcome: 'passed', change: 'fixed', was: 'failed', seconds: 0.91 },
  { nodeid: NF, outcome: 'failed', change: 'new-failure', was: 'passed', seconds: 41.8 },
  {
    nodeid: STILL,
    outcome: 'failed',
    change: 'still-failing',
    was: 'failed',
    streak: { runs: 4, since: '1ad93e49' },
    seconds: 6.2,
  },
  { nodeid: NEWT, outcome: 'skipped', change: 'new-test', seconds: null },
  { nodeid: RAIL, outcome: 'error', change: 'new-failure', was: 'passed', seconds: 5.02 },
];
const MISSING: MissingResult[] = [{ nodeid: GONE, was: 'passed' }];

function detail(r: QueueResult | MissingResult, ctx: ChangeQueueDetailContext): ReactNode {
  return (
    <div data-testid="detail">
      <h2>{r.nodeid}</h2>
      <span data-testid="command">{String(ctx.command)}</span>
      <span data-testid="where">{`${ctx.index} of ${ctx.count} after ${ctx.baseline}`}</span>
    </div>
  );
}

function queue(props: Partial<ChangeQueueProps> = {}) {
  return render(
    <ChangeQueue results={RESULTS} missing={MISSING} baseline={BASE} total={214} {...props} />,
  );
}

// The node ids of the rows on screen, in order.
function ids(): string[] {
  return screen
    .getAllByRole('option')
    .map((o) => o.querySelector('.dl-nodeid')?.getAttribute('title') ?? '');
}

function selectedId(): string | null {
  const o = screen.queryAllByRole('option').find((x) => x.getAttribute('aria-selected') === 'true');
  return o ? (o.querySelector('.dl-nodeid')?.getAttribute('title') ?? '') : null;
}

function option(nodeid: string): HTMLElement {
  const o = screen
    .getAllByRole('option')
    .find((x) => x.querySelector('.dl-nodeid')?.getAttribute('title') === nodeid);
  if (!o) throw new Error(`no row ${nodeid}`);
  return o;
}

// Each group head as its title and its count.
function heads(container: HTMLElement): [string, string][] {
  return Array.from(container.querySelectorAll('.dl-queue__ghead')).map((h) => [
    h.querySelector('span:not(.dl-queue__gcount)')?.textContent ?? '',
    h.querySelector('.dl-queue__gcount')?.textContent ?? '',
  ]);
}

function foot(container: HTMLElement): HTMLElement {
  return container.querySelector('.dl-queue__foot') as HTMLElement;
}

function live(container: HTMLElement): HTMLElement {
  return container.querySelector('[aria-live="polite"]') as HTMLElement;
}

function filterBox(name = 'Filter changed tests by node id'): HTMLInputElement {
  return screen.getByRole('searchbox', { name }) as HTMLInputElement;
}

function stubClipboard(result: 'ok' | 'refused' = 'ok') {
  const writeText = vi.fn(() =>
    result === 'ok' ? Promise.resolve() : Promise.reject(new Error('denied')),
  );
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  return writeText;
}

// The queue's own width, as the browser would measure it.
function atWidth(width: number) {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    return { width: this.classList.contains('dl-queue') ? width : 0, top: 120 } as DOMRect;
  });
}

// n rows of one change, named prefix[000], prefix[001], …
function rows(prefix: string, n: number, extra: Partial<QueueResult> = {}): QueueResult[] {
  return Array.from({ length: n }, (_, i) => ({
    nodeid: `${prefix}[${String(i).padStart(3, '0')}]`,
    outcome: 'failed' as const,
    ...extra,
  }));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  Reflect.deleteProperty(navigator, 'clipboard');
});

describe('groups', () => {
  it('puts the groups in queue order, each in collection order, with Removed closed', () => {
    const { container } = queue();
    expect(heads(container)).toEqual([
      ['New failures', '2'],
      ['Still failing', '1'],
      ['Fixed', '1'],
      ['New tests', '1'],
      ['Removed tests', '1'],
    ]);
    expect(ids()).toEqual([NF, RAIL, STILL, FIXED, NEWT]);
    const toggle = screen.getByRole('button', { name: /^Removed tests/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(ids()).toEqual([NF, RAIL, STILL, FIXED, NEWT, GONE]);
    fireEvent.click(toggle);
    expect(ids()).not.toContain(GONE);
  });

  it('names each group’s list box by its heading', () => {
    queue();
    const box = screen.getByRole('listbox', { name: /^New failures/ });
    expect(box).toHaveAttribute('aria-keyshortcuts', 'j k c Enter');
    expect(within(box).getAllByRole('option')).toHaveLength(2);
    expect(screen.getAllByRole('listbox')).toHaveLength(4);
  });

  it('prints each row’s mark, duration and change in the detail’s own words', () => {
    queue();
    const note = (id: string) => option(id).querySelector('.dl-qopt__note')?.textContent;
    const dur = (id: string) => option(id).querySelector('.dl-qopt__dur')?.textContent;
    expect(note(NF)).toBe('passed in 1adf29af');
    expect(note(STILL)).toBe('failing 4 runs since 1ad93e49');
    expect(note(FIXED)).toBe('failed in 1adf29af');
    expect(note(NEWT)).toBe('not in 1adf29af');
    expect(dur(NF)).toBe('41.8 s');
    expect(dur(RAIL)).toBe('5.02 s');
    expect(dur(NEWT)).toBe('');
    expect(within(option(RAIL)).getByRole('img', { name: 'error' })).toBeInTheDocument();
    expect(option(NF).querySelector('.dl-nodeid')).toHaveClass('dl-nodeid--truncate');
    fireEvent.click(screen.getByRole('button', { name: /^Removed tests/ }));
    // A test this run lacks has no outcome: it draws its change instead.
    expect(within(option(GONE)).queryByRole('img')).toBeNull();
    expect(option(GONE).querySelector('.dl-change__icon')).not.toBeNull();
    expect(note(GONE)).toBe('passed in 1adf29af, not collected in this run');
  });

  it('calls the tests an interrupted run lacks Not reached, and keeps them open', () => {
    const { container } = queue({ interrupted: true });
    expect(heads(container).at(-1)).toEqual(['Not reached', '1']);
    expect(screen.queryByRole('button', { name: /Not reached/ })).toBeNull();
    expect(option(GONE).querySelector('.dl-qopt__note')).toHaveTextContent(
      'passed in 1adf29af; the run stopped first',
    );
  });

  it('shows 50 rows of a group, then the rest of it at once', () => {
    const many = rows('test_m.py::test_v', 120, { change: 'new-failure', was: 'passed' });
    queue({ results: many, missing: [] });
    expect(screen.getAllByRole('option')).toHaveLength(50);
    const more = screen.getByRole('button', { name: 'Show 70 more' });
    // Fed whole, the rest comes at once, so it says nothing about rows not shown.
    expect(screen.queryByText(/not shown/)).toBeNull();
    fireEvent.click(more);
    expect(screen.getAllByRole('option')).toHaveLength(120);
    expect(screen.queryByRole('button', { name: /^Show/ })).toBeNull();
  });

  it('takes another page size', () => {
    const many = rows('test_m.py::test_v', 12, { change: 'fixed', outcome: 'passed' });
    queue({ results: many, missing: [], pageSize: 5 });
    expect(screen.getAllByRole('option')).toHaveLength(5);
    expect(screen.getByRole('button', { name: 'Show 7 more' })).toBeInTheDocument();
  });
});

describe('row notes', () => {
  it('say what each change was against the baseline', () => {
    expect(changeNote({ change: 'new-failure', was: 'passed' }, 'b')).toBe('passed in b');
    expect(changeNote({ change: 'new-failure' }, 'b')).toBe('not in b');
    expect(changeNote({ change: 'new-failure' }, null)).toBe('new test');
    expect(changeNote({ change: 'new-failure', was: 'passed' }, null)).toBe('passed before');
    expect(changeNote({ change: 'still-failing', streak: { runs: 1 } }, 'b')).toBe('failing 1 run');
    expect(changeNote({ change: 'still-failing', was: 'error' }, 'b')).toBe('error in b');
    expect(changeNote({ change: 'fixed' }, 'b')).toBeNull();
    expect(changeNote({ change: 'new-test' }, null)).toBeNull();
    expect(changeNote({ change: 'removed' }, 'b')).toBe('not collected in this run');
    expect(changeNote({ change: 'removed', was: 'skipped' }, null)).toBe(
      'skipped, not collected in this run',
    );
    expect(changeNote({ change: 'not-reached' }, 'b')).toBe('the run stopped first');
    expect(changeNote({ change: 'not-reached', was: 'failed' }, null)).toBe(
      'failed; the run stopped first',
    );
    expect(changeNote({ change: null, was: 'passed' }, 'b')).toBeNull();
  });
});

describe('selection', () => {
  it('selects the first row of the first group until something else is', () => {
    const renderDetail = vi.fn(detail);
    queue({ renderDetail });
    expect(selectedId()).toBe(NF);
    expect(screen.getAllByRole('option').map((o) => o.tabIndex)).toEqual([0, -1, -1, -1, -1]);
    expect(screen.getByRole('region', { name: `Selected test: ${NF}` })).toBeInTheDocument();
    expect(renderDetail).toHaveBeenLastCalledWith(expect.objectContaining({ nodeid: NF }), {
      command: `pytest ${NF}`,
      baseline: '1adf29af',
      index: 0,
      count: 5,
    });
    fireEvent.click(option(STILL));
    expect(selectedId()).toBe(STILL);
    expect(screen.getByTestId('command')).toHaveTextContent(`pytest '${STILL}'`);
    expect(screen.getByTestId('where')).toHaveTextContent('2 of 5 after 1adf29af');
  });

  it('follows the page’s selection and reports each choice', () => {
    const onSelect = vi.fn();
    const { rerender } = queue({ selected: STILL, onSelect, renderDetail: detail });
    expect(selectedId()).toBe(STILL);
    fireEvent.click(option(FIXED));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ nodeid: FIXED }));
    // The page owns it: nothing moves until the page says so.
    expect(selectedId()).toBe(STILL);
    rerender(
      <ChangeQueue
        results={RESULTS}
        missing={MISSING}
        baseline={BASE}
        selected={FIXED}
        onSelect={onSelect}
        renderDetail={detail}
      />,
    );
    expect(selectedId()).toBe(FIXED);
    // A choice made while the page owned it is not the queue's own: let go, it starts again.
    rerender(
      <ChangeQueue results={RESULTS} missing={MISSING} baseline={BASE} renderDetail={detail} />,
    );
    expect(selectedId()).toBe(NF);
  });

  it('names the detail by its node id, hidden characters written as code points', () => {
    const odd = `test_x.py::test_${String.fromCodePoint(0x202e)}gnp.exe`;
    queue({
      results: [{ nodeid: odd, outcome: 'failed', change: 'new-failure', was: 'passed' }],
      missing: [],
      renderDetail: detail,
    });
    expect(
      screen.getByRole('region', { name: 'Selected test: test_x.py::test_⟨U+202E⟩gnp.exe' }),
    ).toBeInTheDocument();
  });

  it('asks the detail to say so when the selected test is not among the rows', () => {
    queue({ selected: 'test_x.py::test_not_loaded', renderDetail: detail });
    expect(selectedId()).toBeNull();
    expect(screen.getByText('Choose a test to see its evidence.')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Selected test' })).toBeInTheDocument();
    // With nothing selected on screen, the first row keeps the tab stop.
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('tabindex', '0');
  });

  it('gives no command for a test this run lacks', () => {
    const renderDetail = vi.fn(detail);
    queue({ renderDetail });
    fireEvent.click(screen.getByRole('button', { name: /^Removed tests/ }));
    fireEvent.click(option(GONE));
    expect(renderDetail).toHaveBeenLastCalledWith(
      expect.objectContaining({ nodeid: GONE, change: 'removed', outcome: null }),
      expect.objectContaining({ command: null, index: 5, count: 6 }),
    );
  });

  it('builds commands with commandFor when given', () => {
    const commandFor = (r: QueueResult) => `python -m pytest ${r.nodeid}`;
    queue({ commandFor, renderDetail: detail });
    expect(screen.getByTestId('command')).toHaveTextContent(`python -m pytest ${NF}`);
  });

  it('keeps the selected row in view as the selection moves, but not on the first render', () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    try {
      queue();
      expect(scroll).not.toHaveBeenCalled();
      fireEvent.keyDown(screen.getAllByRole('listbox')[0] as HTMLElement, { key: 'ArrowDown' });
      expect(scroll).toHaveBeenCalledWith({ block: 'nearest' });
      expect(scroll.mock.contexts.at(-1)).toBe(option(RAIL));
    } finally {
      Reflect.deleteProperty(Element.prototype, 'scrollIntoView');
    }
  });

  it('holds the selection as rows arrive', () => {
    const { rerender } = queue({ results: RESULTS.slice(0, 4), missing: [] });
    fireEvent.click(option(FIXED));
    rerender(<ChangeQueue results={RESULTS} missing={MISSING} baseline={BASE} running />);
    expect(selectedId()).toBe(FIXED);
    expect(ids()).toEqual([NF, RAIL, STILL, FIXED, NEWT]);
  });
});

describe('keys', () => {
  it('moves one selection across the groups with the arrows, j and k, Home and End', () => {
    const onSelect = vi.fn();
    queue({ onSelect });
    const first = option(NF);
    first.focus();
    const press = (key: string) => fireEvent.keyDown(document.activeElement as Element, { key });
    press('ArrowDown');
    expect(selectedId()).toBe(RAIL);
    expect(option(RAIL)).toHaveFocus();
    press('j');
    expect(selectedId()).toBe(STILL);
    expect(option(STILL)).toHaveFocus();
    expect(option(STILL)).toHaveAttribute('tabindex', '0');
    press('k');
    expect(selectedId()).toBe(RAIL);
    press('ArrowUp');
    press('ArrowUp');
    expect(selectedId()).toBe(NF);
    press('End');
    expect(selectedId()).toBe(NEWT);
    expect(option(NEWT)).toHaveFocus();
    press('j');
    expect(selectedId()).toBe(NEWT);
    press('Home');
    expect(selectedId()).toBe(NF);
    expect(option(NF)).toHaveFocus();
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ nodeid: NF }));
  });

  it('ignores keys pressed with a modifier', () => {
    queue();
    const box = screen.getAllByRole('listbox')[0] as HTMLElement;
    fireEvent.keyDown(box, { key: 'j', ctrlKey: true });
    fireEvent.keyDown(box, { key: 'ArrowDown', altKey: true });
    fireEvent.keyDown(box, { key: 'End', metaKey: true });
    expect(selectedId()).toBe(NF);
  });

  it('opens the result page with Enter beside the detail', () => {
    const onOpen = vi.fn();
    queue({ onOpen, renderDetail: detail });
    fireEvent.click(option(STILL));
    fireEvent.keyDown(option(STILL), { key: 'Enter' });
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ nodeid: STILL }));
  });

  it('copies the selected test’s rerun command with c, and the foot says what', async () => {
    const writeText = stubClipboard();
    const { container } = queue();
    fireEvent.click(option(RAIL));
    await act(async () => {
      fireEvent.keyDown(option(RAIL), { key: 'c' });
    });
    expect(writeText).toHaveBeenCalledWith(`pytest '${RAIL}'`);
    expect(foot(container)).toHaveTextContent(`Copied pytest '${RAIL}'`);
    expect(foot(container)).toHaveAttribute('role', 'status');
  });

  it('names the keys in the foot again a few seconds after copying', async () => {
    stubClipboard();
    vi.useFakeTimers();
    const { container } = queue();
    await act(async () => {
      fireEvent.keyDown(option(NF), { key: 'c' });
    });
    expect(foot(container)).toHaveTextContent(`Copied pytest ${NF}`);
    act(() => {
      vi.advanceTimersByTime(3999);
    });
    expect(foot(container)).toHaveTextContent('Copied');
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(foot(container)).not.toHaveTextContent('Copied');
    expect(foot(container)).toHaveTextContent('j k move');
  });

  it('shows the command to copy by hand when the clipboard refuses', async () => {
    stubClipboard('refused');
    const { container } = queue();
    await act(async () => {
      fireEvent.keyDown(option(NF), { key: 'c' });
    });
    expect(foot(container)).toHaveTextContent(
      `The clipboard refused; copy it from here: pytest ${NF}`,
    );
    expect(foot(container).querySelector('code.dl-queue__cmd')).toHaveTextContent(`pytest ${NF}`);
  });

  it('shows the command to copy by hand where there is no clipboard', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    const { container } = queue();
    await act(async () => {
      fireEvent.keyDown(option(NF), { key: 'c' });
    });
    expect(foot(container)).toHaveTextContent('The clipboard refused');
  });

  it('copies nothing for a test this run lacks', async () => {
    const writeText = stubClipboard();
    const { container } = queue();
    fireEvent.click(screen.getByRole('button', { name: /^Removed tests/ }));
    fireEvent.click(option(GONE));
    await act(async () => {
      fireEvent.keyDown(option(GONE), { key: 'c' });
    });
    expect(writeText).not.toHaveBeenCalled();
    expect(foot(container)).not.toHaveTextContent('Copied');
  });

  it('names its keys in the foot', () => {
    const { container, unmount } = queue({ renderDetail: detail });
    const keys = () =>
      Array.from(foot(container).querySelectorAll('.dl-queue__key')).map((k) => k.textContent);
    expect(keys()).toEqual(['j k move', 'Enter opens the result', 'c copies its rerun command']);
    expect(foot(container).querySelectorAll('kbd.dl-kbd')).toHaveLength(4);
    unmount();
    const again = queue({ shortcuts: true });
    expect(
      Array.from(foot(again.container).querySelectorAll('.dl-queue__key')).map(
        (k) => k.textContent,
      ),
    ).toEqual(['j k move', 'Enter opens the result', 'c copies its rerun command', '/ filters']);
  });

  it('with shortcuts, takes j, k and c anywhere on the page and / to the filter', async () => {
    const writeText = stubClipboard();
    queue({ shortcuts: true });
    expect(filterBox()).toHaveAttribute('aria-keyshortcuts', '/');
    fireEvent.keyDown(document.body, { key: 'j' });
    expect(selectedId()).toBe(RAIL);
    // Page-wide, the selection moves and focus stays where it was.
    expect(document.body).toHaveFocus();
    fireEvent.keyDown(document.body, { key: 'j' });
    fireEvent.keyDown(document.body, { key: 'k' });
    expect(selectedId()).toBe(RAIL);
    await act(async () => {
      fireEvent.keyDown(document.body, { key: 'c' });
    });
    expect(writeText).toHaveBeenCalledWith(`pytest '${RAIL}'`);
    fireEvent.keyDown(document.body, { key: '/' });
    await waitFor(() => expect(filterBox()).toHaveFocus());
  });

  it('without shortcuts, leaves the page’s keys alone', () => {
    queue();
    expect(filterBox()).not.toHaveAttribute('aria-keyshortcuts');
    fireEvent.keyDown(document.body, { key: 'j' });
    expect(selectedId()).toBe(NF);
  });

  it('stops listening to the page once shortcuts are off', () => {
    const { rerender } = queue({ shortcuts: true });
    rerender(<ChangeQueue results={RESULTS} missing={MISSING} baseline={BASE} />);
    fireEvent.keyDown(document.body, { key: 'j' });
    expect(selectedId()).toBe(NF);
  });

  it('fires no key inside a text field, a menu or a dialog', async () => {
    const writeText = stubClipboard();
    render(
      <>
        <input aria-label="field" />
        <textarea aria-label="area" />
        <select aria-label="choice">
          <option>one</option>
        </select>
        <div data-testid="editable" contentEditable suppressContentEditableWarning>
          text
        </div>
        <div role="menu">
          <button type="button" role="menuitem">
            Item
          </button>
        </div>
        <dialog open>
          <button type="button">In a dialog</button>
        </dialog>
        <button
          type="button"
          onKeyDown={(e) => {
            e.preventDefault();
          }}
        >
          Handles its own keys
        </button>
        <ChangeQueue results={RESULTS} missing={MISSING} baseline={BASE} shortcuts />
      </>,
    );
    // jsdom computes no isContentEditable; a browser does.
    Object.defineProperty(screen.getByTestId('editable'), 'isContentEditable', { value: true });
    const targets = [
      screen.getByRole('textbox', { name: 'field' }),
      screen.getByRole('textbox', { name: 'area' }),
      screen.getByRole('combobox', { name: 'choice' }),
      screen.getByTestId('editable'),
      screen.getByRole('menuitem', { name: 'Item' }),
      screen.getByRole('button', { name: 'In a dialog' }),
      screen.getByRole('button', { name: 'Handles its own keys' }),
      filterBox(),
    ];
    for (const t of targets) {
      for (const key of ['j', 'k', 'c', '/']) fireEvent.keyDown(t, { key });
    }
    expect(selectedId()).toBe(NF);
    // The filter would take focus on the next tick.
    await act(() => new Promise((done) => setTimeout(done, 5)));
    expect(writeText).not.toHaveBeenCalled();
    expect(filterBox()).not.toHaveFocus();
    fireEvent.keyDown(document.body, { key: 'j', ctrlKey: true });
    expect(selectedId()).toBe(NF);
  });
});

describe('the filter', () => {
  it('matches node ids whatever their case, and each heading counts what matched', () => {
    const { container } = queue();
    fireEvent.change(filterBox(), { target: { value: ' RAIL ' } });
    expect(ids()).toEqual([RAIL, STILL]);
    expect(heads(container)).toEqual([
      ['New failures', '1 of 2'],
      ['Still failing', '1 of 1'],
    ]);
    // While filtering, a group with no match goes, and the notes with it.
    fireEvent.change(filterBox(), { target: { value: 'framing' } });
    expect(heads(container)).toEqual([
      ['New failures', '1 of 2'],
      ['Removed tests', '1 of 1'],
    ]);
    // A closed group with a match opens, with no toggle while filtering.
    expect(ids()).toEqual([NF, GONE]);
    expect(screen.queryByRole('button', { name: /Removed tests/ })).toBeNull();
  });

  it('says when nothing matches, and Escape clears it', () => {
    queue();
    const box = filterBox();
    fireEvent.change(box, { target: { value: 'zzz' } });
    expect(screen.queryAllByRole('option')).toHaveLength(0);
    const note = screen.getByText(/No test here matches/);
    expect(note).toHaveTextContent('No test here matches zzz.');
    expect(note.querySelector('code')).toHaveTextContent('zzz');
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(box).toHaveValue('');
    expect(ids()).toEqual([NF, RAIL, STILL, FIXED, NEWT]);
  });

  it('moves focus from the filter to the selected row with the down arrow', () => {
    queue();
    fireEvent.click(option(STILL));
    filterBox().focus();
    fireEvent.keyDown(filterBox(), { key: 'ArrowDown' });
    expect(option(STILL)).toHaveFocus();
  });

  it('moves focus to the first row when the selected one is filtered out', () => {
    queue();
    fireEvent.click(option(FIXED));
    fireEvent.change(filterBox(), { target: { value: 'rail' } });
    fireEvent.keyDown(filterBox(), { key: 'ArrowDown' });
    expect(selectedId()).toBe(RAIL);
    expect(option(RAIL)).toHaveFocus();
  });
});

// The server's queue: totals per group and the rows loaded so far, in queue order.
const UART_SF = new Set([7, 31]);
const P_NF = rows('test_nf.py::test_v', 3, { change: 'new-failure', was: 'passed' });
const P_SF = rows('test_sf.py::test_v', 50, {
  change: 'still-failing',
  was: 'failed',
  streak: { runs: 3, since: '1ad93e49' },
}).map((r, i) => (UART_SF.has(i) ? { ...r, nodeid: r.nodeid.replace('test_v', 'test_uart') } : r));
const P_FX = rows('test_fx.py::test_v', 17, { change: 'fixed', outcome: 'passed', was: 'failed' });
const P_TOTALS: QueueTotals = {
  'new-failure': 3,
  'still-failing': 180,
  fixed: 64,
  'new-test': 420,
  removed: 12,
};
const P_RESULTS = [...P_NF, ...P_SF, ...P_FX];
const P_REMOVED = rows('test_old.py::test_v', 12, {
  change: 'removed',
  outcome: null,
  was: 'passed',
});

function paged(props: Partial<ChangeQueueProps> = {}) {
  return render(
    <ChangeQueue
      results={P_RESULTS}
      totals={P_TOTALS}
      baseline={BASE}
      total={12480}
      onMore={vi.fn()}
      {...props}
    />,
  );
}

describe('paged by the server', () => {
  it('prints each group’s total and shows a group with none of it loaded', () => {
    const { container } = paged();
    expect(heads(container)).toEqual([
      ['New failures', '3'],
      ['Still failing', '180'],
      ['Fixed', '64'],
      ['New tests', '420'],
      ['Removed tests', '12'],
    ]);
    expect(screen.getByRole('listbox', { name: /^Still failing/ })).toBeInTheDocument();
    expect(screen.queryByRole('listbox', { name: /New tests/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Show 200' })).toBeInTheDocument();
  });

  it('shows 50 rows, then asks for more past those loaded', () => {
    const onMore = vi.fn();
    const { container, rerender } = paged({ onMore });
    const sf = screen.getByRole('listbox', { name: /^Still failing/ });
    expect(within(sf).getAllByRole('option')).toHaveLength(50);
    const group = sf.closest('.dl-queue__group') as HTMLElement;
    expect(group).toHaveTextContent('130 results not shown');
    fireEvent.click(within(group).getByRole('button', { name: 'Show 130 more' }));
    expect(onMore).toHaveBeenCalledWith('still-failing');
    // Its next rows are on their way: the button is busy and the rows shown stay.
    rerender(
      <ChangeQueue
        results={P_RESULTS}
        totals={P_TOTALS}
        baseline={BASE}
        onMore={onMore}
        loading={['still-failing']}
      />,
    );
    const busy = within(group).getByRole('button', { name: 'Loading' });
    expect(busy).toHaveAttribute('aria-busy', 'true');
    expect(group).toHaveAttribute('aria-busy', 'true');
    expect(within(sf).getAllByRole('option')).toHaveLength(50);
    fireEvent.click(busy);
    expect(onMore).toHaveBeenCalledTimes(1);
    // The rows arrive: 180 shown now, as the Show more asked for 250.
    const sfAll = rows('test_sf.py::test_v', 180, { change: 'still-failing', was: 'failed' });
    rerender(
      <ChangeQueue
        results={[...P_NF, ...sfAll, ...P_FX]}
        totals={P_TOTALS}
        baseline={BASE}
        onMore={onMore}
      />,
    );
    expect(
      within(screen.getByRole('listbox', { name: /^Still failing/ })).getAllByRole('option'),
    ).toHaveLength(180);
    expect(group).not.toHaveAttribute('aria-busy');
    expect(heads(container)[1]).toEqual(['Still failing', '180']);
  });

  it('asks for no rows it already has', () => {
    const onMore = vi.fn();
    const sf = rows('test_sf.py::test_v', 100, { change: 'still-failing' });
    render(
      <ChangeQueue
        results={sf}
        totals={{ 'still-failing': 180 }}
        baseline={BASE}
        onMore={onMore}
        pageSize={20}
        allPageSize={30}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Show 30 more' }));
    expect(screen.getAllByRole('option')).toHaveLength(50);
    expect(onMore).not.toHaveBeenCalled();
    expect(screen.getByText('130 results not shown')).toBeInTheDocument();
  });

  it('asks for no rows of a group already on their way as it opens', () => {
    const onMore = vi.fn();
    paged({ onMore, loading: ['removed'] });
    fireEvent.click(screen.getByRole('button', { name: /^Removed tests/ }));
    expect(onMore).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Loading' })).toHaveAttribute('aria-busy', 'true');
  });

  it('asks for a group’s first rows as it opens, and says it is loading', () => {
    const onMore = vi.fn();
    const { rerender } = paged({ onMore });
    const toggle = screen.getByRole('button', { name: /^Removed tests/ });
    fireEvent.click(toggle);
    expect(onMore).toHaveBeenCalledWith('removed');
    const group = toggle.closest('.dl-queue__group') as HTMLElement;
    // Until its rows come, it offers them all, as a group with none shown does.
    expect(within(group).getByRole('button', { name: 'Show 12' })).toBeInTheDocument();
    expect(group).not.toHaveTextContent('not shown');
    rerender(
      <ChangeQueue
        results={P_RESULTS}
        totals={P_TOTALS}
        baseline={BASE}
        onMore={onMore}
        loading={['removed']}
      />,
    );
    const busy = within(group).getByRole('button', { name: 'Loading' });
    expect(busy).toHaveAttribute('aria-busy', 'true');
    expect(busy.querySelector('.dl-btn__cursor')).not.toBeNull();
    expect(group).toHaveAttribute('aria-busy', 'true');
    rerender(
      <ChangeQueue
        results={[...P_RESULTS, ...P_REMOVED]}
        totals={P_TOTALS}
        baseline={BASE}
        onMore={onMore}
      />,
    );
    expect(within(group).getAllByRole('option')).toHaveLength(12);
    // Closed and opened again, with all of it loaded, it asks for nothing.
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(onMore).toHaveBeenCalledTimes(1);
  });

  it('takes removed and not-reached tests from the results, with no outcome', async () => {
    const writeText = stubClipboard();
    const renderDetail = vi.fn(detail);
    // Whatever outcome a removed row carries, it has none in this run.
    const removed = { ...(P_REMOVED[0] as QueueResult), outcome: 'passed' as const };
    render(
      <ChangeQueue
        results={[...P_NF, removed]}
        totals={{ 'new-failure': 3, removed: 1 }}
        baseline={BASE}
        renderDetail={renderDetail}
        missing={[]}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^Removed tests/ }));
    const row = option(removed.nodeid);
    expect(within(row).queryByRole('img')).toBeNull();
    expect(row.querySelector('.dl-qopt__note')).toHaveTextContent(
      'passed in 1adf29af, not collected in this run',
    );
    fireEvent.click(row);
    expect(renderDetail).toHaveBeenLastCalledWith(
      expect.objectContaining({ nodeid: removed.nodeid, outcome: null }),
      expect.objectContaining({ command: null }),
    );
    await act(async () => {
      fireEvent.keyDown(row, { key: 'c' });
    });
    expect(writeText).not.toHaveBeenCalled();
  });

  it('brings a selection loaded past its group’s rows into view, and moves on from it', () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    try {
      const nf = rows('test_nf.py::test_many', 68, { change: 'new-failure', was: 'passed' });
      const onSelect = vi.fn();
      const wanted = nf[55]?.nodeid as string;
      const { container } = render(
        <ChangeQueue
          results={nf}
          totals={{ 'new-failure': 68 }}
          baseline={BASE}
          selected={wanted}
          onSelect={onSelect}
          renderDetail={detail}
        />,
      );
      // The group shows its rows down to it, as Show 18 more would, and it is the selected row.
      expect(screen.getAllByRole('option')).toHaveLength(68);
      expect(selectedId()).toBe(wanted);
      expect(container).not.toHaveTextContent('not shown');
      expect(scroll).toHaveBeenCalledWith({ block: 'nearest' });
      expect(scroll.mock.contexts.at(-1)).toBe(option(wanted));
      fireEvent.keyDown(screen.getByRole('listbox'), { key: 'j' });
      expect(onSelect).toHaveBeenLastCalledWith(
        expect.objectContaining({ nodeid: nf[56]?.nodeid }),
      );
    } finally {
      Reflect.deleteProperty(Element.prototype, 'scrollIntoView');
    }
  });

  it('brings a selection that arrives later into view, a step at a time', () => {
    const sf = rows('test_sf.py::test_v', 260, { change: 'still-failing', was: 'failed' });
    const wanted = sf[230]?.nodeid as string;
    const props = { totals: { 'still-failing': 260 }, baseline: BASE, selected: wanted };
    const { rerender } = render(<ChangeQueue results={sf.slice(0, 200)} {...props} />);
    expect(screen.getAllByRole('option')).toHaveLength(50);
    expect(selectedId()).toBeNull();
    rerender(<ChangeQueue results={sf} {...props} />);
    // 50, then 200 more, the step Show more takes.
    expect(screen.getAllByRole('option')).toHaveLength(250);
    expect(selectedId()).toBe(wanted);
  });

  it('opens a closed group holding the selection, once', () => {
    const { container } = render(
      <ChangeQueue
        results={[...P_NF, ...P_REMOVED]}
        totals={{ 'new-failure': 3, removed: 12 }}
        baseline={BASE}
        selected={P_REMOVED[4]?.nodeid}
      />,
    );
    const toggle = screen.getByRole('button', { name: /^Removed tests/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(selectedId()).toBe(P_REMOVED[4]?.nodeid);
    // Closed again by hand, it stays closed.
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(within(container).getAllByRole('option')).toHaveLength(3);
  });

  it('calls the missing tests Not reached when only those are counted', () => {
    const nr = rows('test_late.py::test_v', 2, { change: 'not-reached', outcome: null });
    const { container, rerender } = render(
      <ChangeQueue
        results={[...P_NF, ...nr]}
        totals={{ 'new-failure': 3, 'not-reached': 5 }}
        baseline={BASE}
      />,
    );
    expect(heads(container).at(-1)).toEqual(['Not reached', '5']);
    expect(screen.queryByRole('button', { name: /Not reached/ })).toBeNull();
    expect(ids()).toContain(nr[0]?.nodeid);
    rerender(
      <ChangeQueue
        results={P_NF}
        totals={{ 'new-failure': 3, 'not-reached': 5, removed: 2 }}
        baseline={BASE}
      />,
    );
    expect(heads(container).at(-1)).toEqual(['Removed tests', '2']);
  });

  it('searches only what has loaded, and says so', () => {
    const onMore = vi.fn();
    const { container } = paged({ onMore });
    fireEvent.change(filterBox(), { target: { value: 'UART' } });
    expect(heads(container)).toEqual([
      ['Still failing', '2 of 50 loaded · 180 in all'],
      ['Fixed', '0 of 17 loaded · 64 in all'],
      ['New tests', 'none loaded · 420 in all'],
    ]);
    expect(ids()).toEqual(['test_sf.py::test_uart[007]', 'test_sf.py::test_uart[031]']);
    const sf = screen
      .getByRole('listbox', { name: /Still failing/ })
      .closest('.dl-queue__group') as HTMLElement;
    expect(sf).toHaveTextContent('130 not loaded, so not searched');
    fireEvent.click(within(sf).getByRole('button', { name: 'Load 130 more' }));
    expect(onMore).toHaveBeenCalledWith('still-failing');
    expect(screen.getByRole('button', { name: 'Load 47 more' })).toBeInTheDocument();
    expect(screen.getByText('47 not loaded, so not searched')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load 200' }));
    expect(onMore).toHaveBeenLastCalledWith('new-test');
    expect(screen.getByText('420 not loaded, so not searched')).toBeInTheDocument();
    expect(screen.queryByText(/matches/)).toBeNull();
  });

  it('shows more loaded matches before it offers to load', () => {
    const many = rows('test_sf.py::test_uart', 60, { change: 'still-failing' });
    const { container } = render(
      <ChangeQueue
        results={many}
        totals={{ 'still-failing': 180 }}
        baseline={BASE}
        onMore={vi.fn()}
      />,
    );
    fireEvent.change(filterBox(), { target: { value: 'uart' } });
    expect(heads(container)).toEqual([['Still failing', '60 of 60 loaded · 180 in all']]);
    expect(screen.getAllByRole('option')).toHaveLength(50);
    expect(screen.getByText('10 results not shown')).toBeInTheDocument();
    expect(screen.queryByText(/not loaded, so not searched/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show 10 more' }));
    expect(screen.getAllByRole('option')).toHaveLength(60);
    expect(screen.getByRole('button', { name: 'Load 120 more' })).toBeInTheDocument();
  });

  it('says how much went unsearched when nothing loaded matches', () => {
    const nf = rows('test_nf.py::test_v', 2, { change: 'new-failure' });
    const sf = rows('test_sf.py::test_v', 50, { change: 'still-failing' });
    render(
      <ChangeQueue
        results={[...nf, ...sf]}
        totals={{ 'new-failure': 2, 'still-failing': 180, 'new-test': 180 }}
        baseline={BASE}
        onMore={vi.fn()}
      />,
    );
    fireEvent.change(filterBox(), { target: { value: 'uart' } });
    expect(
      screen.getByText((_, el) => el?.classList.contains('dl-queue__note') ?? false),
    ).toHaveTextContent('No loaded test here matches uart; 310 not loaded were not searched.');
  });

  it('counts failures from the totals for a run with nothing to compare with', () => {
    const fails = rows('test_f.py::test_v', 20, {});
    const { container } = render(
      <ChangeQueue results={fails} totals={{ failures: 75 }} baseline={null} running />,
    );
    expect(heads(container)).toEqual([['Failures', '75']]);
    expect(live(container)).toHaveTextContent('75 failures so far');
  });
});

describe('the whole run', () => {
  const COUNTS = { failed: 300, passed: 12000, skipped: 160, error: 20 };
  const ALL = rows('test_all.py::test_v', 200, { outcome: 'passed' });

  it('asks for its first rows as it opens, then pages by 200', () => {
    const onMore = vi.fn();
    const { rerender } = paged({ onMore, outcomeCounts: COUNTS, allResults: [] });
    fireEvent.click(screen.getByRole('button', { name: 'All 12,480 results' }));
    expect(onMore).toHaveBeenCalledWith('all');
    const props = {
      results: P_RESULTS,
      totals: P_TOTALS,
      baseline: BASE,
      total: 12480,
      onMore,
      outcomeCounts: COUNTS,
    };
    rerender(<ChangeQueue {...props} allResults={[]} loading={['all']} />);
    expect(screen.getByRole('button', { name: 'Loading' })).toHaveAttribute('aria-busy', 'true');
    rerender(<ChangeQueue {...props} allResults={ALL} />);
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('12,480 results');
    const box = screen.getByRole('listbox', { name: 'Results' });
    expect(within(box).getAllByRole('option')).toHaveLength(200);
    // The whole run prints no change notes.
    expect(box.querySelector('.dl-qopt__note')).toBeNull();
    expect(screen.getByText('12,280 results not shown')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^All .* results$/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show 200 more' }));
    expect(onMore).toHaveBeenCalledTimes(2);
    expect(onMore).toHaveBeenLastCalledWith('all');
    expect(filterBox('Filter results by node id')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Changed tests' }));
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Changed since 1adf29af');
  });

  it('says it is loading an outcome it has no count for', () => {
    const props = {
      results: P_RESULTS,
      totals: P_TOTALS,
      baseline: BASE,
      total: 12480,
      outcomeCounts: COUNTS,
      defaultView: 'all' as const,
      outcome: 'xpassed' as const,
    };
    const { container, rerender } = render(
      <ChangeQueue {...props} allResults={[]} loading={['all']} />,
    );
    const group = container.querySelector('.dl-queue__group') as HTMLElement;
    const status = within(group).getByRole('status');
    expect(status).toHaveTextContent(/^Loading$/);
    expect(status.querySelector('.dl-btn__cursor')).toHaveAttribute('aria-hidden', 'true');
    expect(group).toHaveAttribute('aria-busy', 'true');
    rerender(<ChangeQueue {...props} allResults={[]} />);
    expect(within(container).queryByText('Loading')).toBeNull();
  });

  it('asks for nothing when it opens with rows loaded, busy, or an empty run', () => {
    const onMore = vi.fn();
    const { unmount } = paged({ onMore, allResults: ALL });
    fireEvent.click(screen.getByRole('button', { name: 'All 12,480 results' }));
    unmount();
    const busy = paged({ onMore, allResults: [], loading: ['all'] });
    fireEvent.click(screen.getByRole('button', { name: 'All 12,480 results' }));
    busy.unmount();
    paged({ onMore, allResults: [], total: 0 });
    fireEvent.click(screen.getByRole('button', { name: 'All 0 results' }));
    expect(onMore).not.toHaveBeenCalled();
  });

  it('filters by outcome on the server, every outcome and its count in view', () => {
    const onOutcome = vi.fn();
    const props = {
      results: P_RESULTS,
      totals: P_TOTALS,
      baseline: BASE,
      total: 12480,
      onMore: vi.fn(),
      outcomeCounts: COUNTS,
      onOutcome,
      defaultView: 'all' as const,
    };
    const { rerender } = render(<ChangeQueue {...props} allResults={ALL} outcome="all" />);
    const group = screen.getByRole('radiogroup', { name: 'Outcome' });
    expect(group).toHaveClass('dl-seg', 'dl-seg--wrap');
    expect(
      within(group)
        .getAllByRole('radio')
        .map((r) => r.textContent),
    ).toEqual(['All 12,480', 'failed 300', 'passed 12,000', 'skipped 160', 'error 20']);
    fireEvent.click(within(group).getByRole('radio', { name: 'failed 300' }));
    expect(onOutcome).toHaveBeenCalledWith('failed');
    // The page owns the outcome: nothing changes until it says so.
    expect(within(group).getByRole('radio', { name: 'All 12,480' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    const failed = rows('test_all.py::test_f', 200, {});
    rerender(<ChangeQueue {...props} allResults={failed} outcome="failed" />);
    expect(within(group).getByRole('radio', { name: 'failed 300' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    // The total for one outcome is the server's count of it.
    expect(screen.getByText('100 results not shown')).toBeInTheDocument();
  });

  it('fed whole, counts and filters the run itself, 200 at a time', () => {
    const whole: QueueResult[] = [
      ...rows('test_w.py::test_p', 420, { outcome: 'passed' }),
      ...rows('test_w.py::test_f', 30, {}),
    ];
    render(<ChangeQueue results={whole} baseline={BASE} defaultView="all" />);
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('450 results');
    expect(screen.getAllByRole('option')).toHaveLength(200);
    expect(screen.getByText('250 results not shown')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show 200 more' }));
    expect(screen.getAllByRole('option')).toHaveLength(400);
    fireEvent.click(screen.getByRole('button', { name: 'Show 50 more' }));
    expect(screen.getAllByRole('option')).toHaveLength(450);
    const group = screen.getByRole('radiogroup', { name: 'Outcome' });
    expect(
      within(group)
        .getAllByRole('radio')
        .map((r) => r.textContent),
    ).toEqual(['All 450', 'failed 30', 'passed 420']);
    fireEvent.click(within(group).getByRole('radio', { name: 'passed 420' }));
    // A new outcome starts again from its first page.
    expect(screen.getAllByRole('option')).toHaveLength(200);
    fireEvent.click(within(group).getByRole('radio', { name: 'failed 30' }));
    expect(screen.getAllByRole('option')).toHaveLength(30);
    expect(screen.queryByText(/not shown/)).toBeNull();
    fireEvent.change(filterBox('Filter results by node id'), { target: { value: 'zzz' } });
    expect(screen.getByText(/No result matches/)).toHaveTextContent('No result matches zzz.');
  });

  it('prints no change notes over the whole run', () => {
    const { container } = render(
      <ChangeQueue results={RESULTS} missing={MISSING} baseline={BASE} defaultView="all" />,
    );
    expect(ids()).toEqual([PASS, FIXED, NF, STILL, NEWT, RAIL]);
    expect(container.querySelector('.dl-qopt__note')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Changed tests' }));
    expect(container.querySelector('.dl-qopt__note')).not.toBeNull();
  });

  it('offers no outcome filter for a run of one outcome', () => {
    render(
      <ChangeQueue
        results={rows('test_w.py::test_p', 3, { outcome: 'passed' })}
        baseline={BASE}
        defaultView="all"
      />,
    );
    expect(screen.queryByRole('radiogroup')).toBeNull();
  });
});

describe('states', () => {
  it('says nothing changed, and that nothing fails', () => {
    const same = RESULTS.map((r) => ({ ...r, change: null, outcome: 'passed' as const }));
    const { container } = render(<ChangeQueue results={same} baseline={BASE} />);
    const note = container.querySelector('.dl-queue__note') as HTMLElement;
    expect(note).toHaveTextContent('Nothing changed since 1adf29af.No failures.');
    expect(note.querySelector('code')).toHaveTextContent('1adf29af');
    expect(heads(container)).toEqual([]);
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Changed since 1adf29af');
  });

  it('says nothing changed, then lists what still fails', () => {
    const still = RESULTS.filter((r) => r.change === 'still-failing');
    const { container } = render(<ChangeQueue results={still} baseline={BASE} />);
    expect(container.querySelector('.dl-queue__note')).toHaveTextContent(
      /^Nothing changed since 1adf29af\.$/,
    );
    expect(heads(container)).toEqual([['Still failing', '1']]);
  });

  it('with nothing to compare with, says so and lists the failures', () => {
    const first = RESULTS.map((r) => ({ ...r, change: null, was: null }));
    const { container } = render(
      <ChangeQueue results={first} baseline={null} renderDetail={detail} />,
    );
    expect(container.querySelector('.dl-queue__note')).toHaveTextContent(
      /^Nothing to compare with yet\.$/,
    );
    expect(heads(container)).toEqual([['Failures', '3']]);
    expect(ids()).toEqual([NF, STILL, RAIL]);
    // With no baseline, a row has no change to note.
    expect(container.querySelector('.dl-qopt__note')).toBeNull();
    expect(container.querySelector('.dl-queue__title')).toHaveTextContent(/^This run$/);
    expect(screen.getByRole('button', { name: 'All 6 results' })).toBeInTheDocument();
  });

  it('takes the page’s own words for a run with no baseline, and says when nothing fails', () => {
    const passing = [{ nodeid: PASS, outcome: 'passed' as const }];
    const { container } = render(
      <ChangeQueue
        results={passing}
        baseline={null}
        baselineNote="The failures so far. Changes show once the session ends."
      />,
    );
    expect(container.querySelector('.dl-queue__note')).toHaveTextContent(
      'The failures so far. Changes show once the session ends.No failures.',
    );
    expect(screen.getByRole('region', { name: 'Failures' })).toBeInTheDocument();
  });

  it('while running, counts in a polite live region what has failed so far', () => {
    const { container, rerender } = queue({ running: true });
    expect(live(container)).toHaveTextContent('2 new failures so far');
    rerender(<ChangeQueue results={RESULTS} baseline={null} running />);
    expect(live(container)).toHaveTextContent('3 failures so far');
    rerender(
      <ChangeQueue results={P_RESULTS} totals={{ 'new-failure': 1 }} baseline={BASE} running />,
    );
    expect(live(container)).toHaveTextContent('1 new failure so far');
    rerender(<ChangeQueue results={RESULTS} baseline={BASE} />);
    expect(live(container)).toHaveTextContent(/^$/);
  });

  it('counts the run’s results under the groups', () => {
    queue();
    expect(screen.getByRole('button', { name: 'All 214 results' })).toBeInTheDocument();
    render(<ChangeQueue results={RESULTS.slice(0, 1)} baseline={BASE} />);
    expect(screen.getByRole('button', { name: 'All 1 result' })).toBeInTheDocument();
  });
});

describe('layout', () => {
  it('lists alone without a detail, named for what it lists', () => {
    const { container } = queue({ className: 'mine' });
    const section = container.querySelector('section') as HTMLElement;
    expect(section).toHaveAttribute('data-band', 'list');
    expect(section).toHaveClass('dl-queue', 'mine');
    expect(screen.getByRole('region', { name: 'Changed tests' })).toBe(section);
    expect(screen.queryByRole('region', { name: /Selected test/ })).toBeNull();
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Changed since 1adf29af');
  });

  it('puts the queue beside the detail from 900px of its own width', () => {
    atWidth(900);
    const { container } = queue({ renderDetail: detail, label: 'Run triage' });
    const section = screen.getByRole('region', { name: 'Run triage' });
    expect(section).toHaveAttribute('data-band', 'split');
    expect(section).toHaveClass('dl-queue--split');
    const list = container.querySelector('.dl-queue__list') as HTMLElement;
    expect(list).not.toHaveAttribute('hidden');
    expect(screen.getByRole('region', { name: `Selected test: ${NF}` })).not.toHaveAttribute(
      'hidden',
    );
    expect(container.querySelector('.dl-queue__back')).toBeNull();
    // It fits the viewport below where it starts: 768 − 120 − 16.
    expect(list.style.maxHeight).toBe('632px');
    expect(foot(container)).toHaveTextContent('Enter opens the result');
  });

  it('shows one pane at a time when narrower', () => {
    atWidth(899);
    const onOpen = vi.fn();
    const { container } = queue({ renderDetail: detail, onOpen });
    const section = screen.getByRole('region', { name: 'Triage' });
    expect(section).toHaveAttribute('data-band', 'single');
    expect(section).toHaveClass('dl-queue--single');
    const list = container.querySelector('.dl-queue__list') as HTMLElement;
    const pane = container.querySelector('.dl-queue__detail') as HTMLElement;
    expect(pane).toHaveAttribute('hidden');
    expect(list.style.maxHeight).toBe('');
    expect(foot(container)).toHaveTextContent('Enter shows the test');
    // Choosing a test shows its detail, and focus moves into it.
    fireEvent.click(option(RAIL));
    expect(list).toHaveAttribute('hidden');
    expect(pane).not.toHaveAttribute('hidden');
    expect(pane).toHaveFocus();
    expect(screen.getByTestId('detail')).toHaveTextContent(RAIL);
    const backBtn = screen.getByRole('button', { name: 'Changed tests (2 of 5)' });
    fireEvent.click(screen.getByRole('button', { name: 'Next test' }));
    expect(screen.getByTestId('detail')).toHaveTextContent(STILL);
    expect(backBtn).toHaveTextContent('Changed tests (3 of 5)');
    fireEvent.click(screen.getByRole('button', { name: 'Previous test' }));
    fireEvent.click(screen.getByRole('button', { name: 'Previous test' }));
    fireEvent.click(screen.getByRole('button', { name: 'Previous test' }));
    expect(backBtn).toHaveTextContent('Changed tests (1 of 5)');
    // Back to the queue, and focus back to the row.
    fireEvent.click(backBtn);
    expect(list).not.toHaveAttribute('hidden');
    expect(pane).toHaveAttribute('hidden');
    expect(option(NF)).toHaveFocus();
    // Enter shows the test rather than opening its page.
    fireEvent.keyDown(option(NF), { key: 'Enter' });
    expect(pane).not.toHaveAttribute('hidden');
    expect(pane).toHaveFocus();
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('goes back to the queue to filter with /', async () => {
    atWidth(560);
    const { container } = queue({ renderDetail: detail, shortcuts: true });
    fireEvent.click(option(STILL));
    const list = container.querySelector('.dl-queue__list') as HTMLElement;
    expect(list).toHaveAttribute('hidden');
    fireEvent.keyDown(document.body, { key: '/' });
    expect(list).not.toHaveAttribute('hidden');
    await waitFor(() => expect(filterBox()).toHaveFocus());
  });

  it('names the way back by the list it returns to', () => {
    atWidth(560);
    render(
      <ChangeQueue
        results={RESULTS.map((r) => ({ ...r, change: null }))}
        baseline={null}
        renderDetail={detail}
      />,
    );
    fireEvent.click(option(RAIL));
    expect(screen.getByRole('button', { name: 'Failures (3 of 3)' })).toBeInTheDocument();
  });
});
