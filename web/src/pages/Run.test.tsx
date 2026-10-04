// The run page against a stand-in server: a queue to work down, the line, the baseline, the
// address that keeps the selection, and every state a run can be in.
import type { Query } from '@tanstack/react-query';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resultHref } from '../adapt';
import { clearSessionEnded } from '../app/sessionEnd';
import { type Handler, json, refuse, renderAt, stubServer } from '../testing/server';

const ID = '0123abcd0123abcd0123abcd0123abcd';
const BASE = '1adf29af1adf29af1adf29af1adf29af';
const SINCE = '1ad93e491ad93e491ad93e491ad93e49';
const OTHER = '9876fedc9876fedc9876fedc9876fedc';
const RLO = String.fromCodePoint(0x202e);

type Outcome = 'passed' | 'failed' | 'error' | 'skipped' | 'xfailed' | 'xpassed';
type Change = 'new_failure' | 'still_failing' | 'fixed' | 'new_test';
type Missing = 'removed' | 'not_reached';

// One result the run holds, with how it changed against the baseline.
interface Res {
  node: string;
  outcome: Outcome;
  change?: Change;
  was?: Outcome | null;
  streak?: { runs: number; since: string };
}

// A test the baseline holds and the run lacks.
interface Gone {
  node: string;
  was: Outcome | null;
}

const CHAR: Record<Outcome, string> = {
  passed: '.',
  failed: 'F',
  error: 'E',
  skipped: 's',
  xfailed: 'x',
  xpassed: 'X',
};
const CHANGE_CHAR: Record<Change, string> = {
  new_failure: 'n',
  still_failing: 's',
  fixed: 'f',
  new_test: 't',
};
const QUEUE_ORDER: Change[] = ['new_failure', 'still_failing', 'fixed', 'new_test'];

const VCS = {
  commit: '7f3a2c1e9b0d',
  branch: 'main',
  commit_subject: 'Tune the rails',
  commit_subject_truncated: false,
  dirty: false,
};

interface World {
  results: Res[];
  gone?: Gone[];
  // How the run ended, as its detail says; a finished run on main by default.
  run?: Record<string, unknown>;
  // 'branch' or 'project' compares it with BASE; 'none' and 'pending' do not.
  state?: 'branch' | 'project' | 'none' | 'pending';
  missing?: Missing;
  metadata?: { key: string; value: string }[];
}

function tally<K extends string>(keys: readonly K[], values: (K | undefined)[]): Record<K, number> {
  const out = Object.fromEntries(keys.map((k) => [k, 0])) as Record<K, number>;
  for (const v of values) if (v) out[v] += 1;
  return out;
}

const OUTCOMES: Outcome[] = ['passed', 'failed', 'error', 'skipped', 'xfailed', 'xpassed'];

function runOf(w: World) {
  const state = w.state ?? 'branch';
  const compared = state === 'branch' || state === 'project';
  const changeCounts = {
    ...tally(
      QUEUE_ORDER,
      w.results.map((r) => r.change),
    ),
    removed: 0,
    not_reached: 0,
  };
  changeCounts[w.missing ?? 'removed'] = (w.gone ?? []).length;
  return {
    id: ID,
    started_at: '2026-09-27T09:00:00Z',
    finished_at: state === 'pending' ? null : '2026-09-27T09:00:05Z',
    exit_status: state === 'pending' ? null : 1,
    interrupted: false,
    interrupt_reason: null,
    presentation: state === 'pending' ? 'abandoned' : 'finished',
    vcs: VCS,
    recorded_by: 'alice',
    project: 'firmware',
    counts: tally(
      OUTCOMES,
      w.results.map((r) => r.outcome),
    ),
    comparison: compared
      ? {
          state,
          baseline: { id: BASE, started_at: '2026-09-27T08:18:00Z', branch: 'main' },
          counts: changeCounts,
        }
      : { state, baseline: null, counts: null },
    ...w.run,
  };
}

function names(r: { node: string }) {
  const [file = '', fn = ''] = r.node.split('::');
  return { node_id: r.node, file_path: file, class_name: null, function_name: fn, param_id: null };
}

function changeItem(w: World, r: Res | Gone, change: string) {
  const res = 'outcome' in r ? r : null;
  return {
    ...names(r),
    change,
    outcome: res ? res.outcome : null,
    was: r.was ?? null,
    duration: res ? 0.25 : null,
    position: res ? w.results.indexOf(res) : null,
    streak: res?.streak ?? null,
  };
}

function failing(o: Outcome) {
  return o === 'failed' || o === 'error';
}

function listItem(r: Res) {
  return {
    ...names(r),
    outcome: r.outcome,
    duration: 0.25,
    started_at: null,
    finished_at: null,
    setup_outcome: null,
    call_outcome: null,
    teardown_outcome: null,
    setup_duration: null,
    call_duration: null,
    teardown_duration: null,
    worker_id: null,
    failure: failing(r.outcome)
      ? {
          failure_type: 'AssertionError',
          failure_message: `AssertionError: ${r.node} broke`,
          failure_message_truncated: false,
          failure_path: null,
          failure_lineno: null,
          skip_reason: null,
          xfail_reason: null,
        }
      : null,
  };
}

function detailOf(w: World, r: Res) {
  const compared = (w.state ?? 'branch') === 'branch' || w.state === 'project';
  const bad = failing(r.outcome);
  return {
    ...listItem(r),
    failure: undefined,
    failure_type: bad ? 'AssertionError' : null,
    failure_message: bad ? `AssertionError: ${r.node} broke` : null,
    failure_message_truncated: false,
    failure_path: null,
    failure_lineno: null,
    failure_repr: null,
    failure_repr_truncated: false,
    traceback: bad ? `E   AssertionError: ${r.node} broke` : null,
    traceback_truncated: false,
    skip_reason: null,
    skip_reason_truncated: false,
    xfail_reason: null,
    xfail_reason_truncated: false,
    captured_stdout: null,
    captured_stdout_truncated: false,
    captured_stderr: null,
    captured_stderr_truncated: false,
    position: w.results.indexOf(r),
    change: compared ? (r.change ?? null) : null,
    was: compared ? (r.was ?? null) : null,
    streak: compared ? (r.streak ?? null) : null,
  };
}

function page<T>(items: T[], query: URLSearchParams) {
  const limit = Math.min(200, Number(query.get('limit') ?? 50));
  const offset = Number(query.get('offset') ?? 0);
  return { items: items.slice(offset, offset + limit), has_more: offset + limit < items.length };
}

// Answers every read the run page makes of world `w`; `before` answers first, when it does.
function answer(w: World, before?: Handler): Handler {
  const run = runOf(w);
  const compared = run.comparison.state === 'branch' || run.comparison.state === 'project';
  return (method, path, query, request) => {
    const first = before?.(method, path, query, request);
    if (first) return first;
    if (path === `/runs/${ID}`) return json(200, run);
    if (path === `/runs/${ID}/outcomes`) {
      return json(200, {
        outcomes: w.results.map((r) => CHAR[r.outcome]).join(''),
        changes: compared
          ? w.results.map((r) => (r.change ? CHANGE_CHAR[r.change] : '-')).join('')
          : null,
      });
    }
    if (path === `/runs/${ID}/metadata`) {
      return json(200, {
        items: (w.metadata ?? []).map((m) => ({
          key: m.key,
          name: null,
          value: m.value,
          status: 'captured',
          source: 'session',
          source_file: null,
          declared: false,
        })),
        files: [],
      });
    }
    if (path === `/runs/${ID}/changes`) {
      const wanted = query.getAll('change');
      const items = compared
        ? [
            ...QUEUE_ORDER.flatMap((c) =>
              w.results.filter((r) => r.change === c).map((r) => changeItem(w, r, c)),
            ),
            ...(w.gone ?? []).map((g) => changeItem(w, g, w.missing ?? 'removed')),
          ].filter((i) => !wanted.length || wanted.includes(i.change))
        : [];
      return json(200, page(items, query));
    }
    if (path === `/runs/${ID}/results`) {
      const wanted = query.getAll('outcome');
      const items = w.results.filter((r) => !wanted.length || wanted.includes(r.outcome));
      return json(200, page(items.map(listItem), query));
    }
    if (path === `/runs/${ID}/result`) {
      const r = w.results.find((x) => x.node === query.get('node_id'));
      return r ? json(200, detailOf(w, r)) : refuse(404, 'unknown_result');
    }
    if (path === '/projects/firmware/tests/history') {
      const r = w.results.find((x) => x.node === query.get('node_id'));
      const items = r
        ? [
            {
              run_id: ID,
              started_at: run.started_at,
              finished_at: run.finished_at,
              outcome: r.outcome,
              duration: 0.25,
              vcs: null,
              change: r.change ?? null,
            },
          ]
        : [];
      return json(200, { items, has_more: false, next_cursor: null });
    }
    return undefined;
  };
}

function serve(w: World, before?: Handler): string[] {
  return stubServer(answer(w, before));
}

// n results of one kind, named prefix[000], prefix[001], …
function many(prefix: string, n: number, extra: Omit<Res, 'node'>): Res[] {
  return Array.from({ length: n }, (_, i) => ({
    node: `${prefix}[${String(i).padStart(3, '0')}]`,
    ...extra,
  }));
}

const NF1 = 'tests/test_uart.py::test_framing_at_921600';
const NF2 = 'tests/test_rails.py::test_rail_ripple';
const SF = 'tests/test_rails.py::test_rail_under_load';
const FX = 'tests/test_watchdog.py::test_brownout_recovery';
const PASS = 'tests/test_boot.py::test_cold_boot';
const NT = 'tests/test_output.py::test_settle_time';
const GONE = 'tests/test_uart.py::test_framing_at_4800';

// A run on main compared with 1adf29af: two new failures, one still failing, one fixed, a new test,
// a pass that did not change, and a test it no longer has.
const TRIAGE: World = {
  results: [
    { node: PASS, outcome: 'passed' },
    { node: NF1, outcome: 'failed', change: 'new_failure', was: 'passed' },
    { node: FX, outcome: 'passed', change: 'fixed', was: 'failed' },
    {
      node: SF,
      outcome: 'failed',
      change: 'still_failing',
      was: 'failed',
      streak: { runs: 4, since: SINCE },
    },
    { node: NF2, outcome: 'error', change: 'new_failure', was: 'passed' },
    { node: NT, outcome: 'passed', change: 'new_test', was: null },
  ],
  gone: [{ node: GONE, was: 'passed' }],
  metadata: [
    { key: 'rig', value: 'bench-02' },
    { key: 'firmware', value: '7f3a2c1' },
  ],
};

function detailRegion() {
  return screen.getByRole('region', { name: /^Selected test/ });
}

async function selectedHeading() {
  return within(await screen.findByRole('region', { name: /^Selected test/ })).findByRole(
    'heading',
    { level: 2 },
  );
}

function group(name: RegExp) {
  return screen.findByRole('listbox', { name });
}

function selectedOption() {
  return document.querySelector('[role="option"][aria-selected="true"]');
}

function cursorAt(): number | null {
  const mark = document.querySelector('.dl-dotline__cursor');
  return mark ? Number(mark.getAttribute('x')) : null;
}

beforeEach(() => {
  clearSessionEnded();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the queue', () => {
  it('opens on the first new failure without touching the address', async () => {
    serve(TRIAGE);
    const { router } = renderAt(`/runs/${ID}`);
    const heading = await selectedHeading();
    expect(heading).toHaveTextContent(NF1);
    const detail = detailRegion();
    expect(within(detail).getByText('New failure')).toBeInTheDocument();
    expect(detail).toHaveTextContent('New failure: passed in 1adf29af');
    expect(router.state.location.search).toBe('');
    // The groups in queue order, removed tests closed until opened.
    const heads = [...document.querySelectorAll('.dl-queue__ghead')].map((h) => h.textContent);
    expect(heads).toEqual([
      'New failures2',
      'Still failing1',
      'Fixed1',
      'New tests1',
      'Removed tests1',
    ]);
    expect(screen.getByRole('button', { name: /Removed tests/ })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    expect(selectedOption()).toHaveTextContent('test_framing_at_921600');
    // Its place in the run's line: the second result, one 4px mark each.
    await waitFor(() => expect(cursorAt()).toBe(4 - 1.5));
  });

  it('moves with j and k, writing the address in place, and keeps focus where it is', async () => {
    serve(TRIAGE);
    const user = userEvent.setup();
    const { router } = renderAt(`/runs/${ID}`);
    await selectedHeading();
    const before = document.activeElement;
    await user.keyboard('j');
    await waitFor(() =>
      expect(router.state.location.search).toBe(`?node_id=${encodeURIComponent(NF2)}`),
    );
    expect(router.state.historyAction).toBe('REPLACE');
    expect(await selectedHeading()).toHaveTextContent(NF2);
    expect(selectedOption()).toHaveTextContent('test_rail_ripple');
    // A selection is not a navigation: the page's heading does not take focus.
    expect(document.activeElement).toBe(before);
    await waitFor(() => expect(cursorAt()).toBe(4 * 4 - 1.5));
    await user.keyboard('j');
    await waitFor(() =>
      expect(router.state.location.search).toBe(`?node_id=${encodeURIComponent(SF)}`),
    );
    expect(await selectedHeading()).toHaveTextContent(SF);
    expect(detailRegion()).toHaveTextContent('Still failing: 4 runs, since 1ad93e49');
    await user.keyboard('k');
    await waitFor(() =>
      expect(router.state.location.search).toBe(`?node_id=${encodeURIComponent(NF2)}`),
    );
    // Back leaves the run: every move replaced the one entry.
    expect(router.state.historyAction).toBe('REPLACE');
  });

  it('moves at once, so a key straight after j acts on the test it moved to', async () => {
    serve(TRIAGE);
    renderAt(`/runs/${ID}`);
    await selectedHeading();
    // Dispatched as a browser would, outside the test library's act(), which would flush a
    // transition before anyone could look.
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'j', bubbles: true }));
    expect(selectedOption()).toHaveTextContent('test_rail_ripple');
    errors.mockRestore();
  });

  it('copies the selected test’s rerun command with c', async () => {
    serve(TRIAGE);
    const user = userEvent.setup();
    renderAt(`/runs/${ID}`);
    await selectedHeading();
    await user.keyboard('c');
    await waitFor(async () => expect(await navigator.clipboard.readText()).toBe(`pytest ${NF1}`));
    expect(await screen.findByText(/^Copied/)).toHaveTextContent(`Copied pytest ${NF1}`);
    // The detail names the same command, and the key that copies it.
    const detail = detailRegion();
    expect(within(detail).getByRole('button', { name: /Copy rerun command/ })).toHaveAttribute(
      'aria-keyshortcuts',
      'c',
    );
    expect(within(detail).getByRole('link', { name: 'Open result page' })).toHaveAttribute(
      'href',
      resultHref(ID, NF1),
    );
  });

  it('opens the result page with Enter, and the baseline’s result for a test the run lacks', async () => {
    serve(TRIAGE);
    const user = userEvent.setup();
    const { router } = renderAt(`/runs/${ID}`);
    await selectedHeading();
    await user.click(screen.getByRole('option', { name: /test_framing_at_921600/ }));
    await user.keyboard('{Enter}');
    await waitFor(() => expect(router.state.location.pathname).toBe(`/runs/${ID}/result`));
    expect(router.state.location.search).toBe(`?node_id=${encodeURIComponent(NF1)}`);
    expect(router.state.historyAction).toBe('PUSH');

    await act(() => router.navigate(`/runs/${ID}`));
    await selectedHeading();
    await user.click(screen.getByRole('button', { name: /Removed tests/ }));
    const gone = await screen.findByRole('option', { name: /test_framing_at_4800/ });
    await user.click(gone);
    expect(await selectedHeading()).toHaveTextContent(GONE);
    expect(detailRegion()).toHaveTextContent(
      'Removed: passed in 1adf29af, not collected in this run',
    );
    // A test the run lacks has no command and no place in its line.
    expect(within(detailRegion()).queryByRole('link', { name: 'Open result page' })).toBeNull();
    expect(cursorAt()).toBeNull();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(router.state.location.pathname).toBe(`/runs/${BASE}/result`));
    expect(router.state.location.search).toBe(`?node_id=${encodeURIComponent(GONE)}`);
  });

  it('shows the selected test’s history and its evidence', async () => {
    serve(TRIAGE);
    renderAt(`/runs/${ID}`);
    await selectedHeading();
    const detail = detailRegion();
    // The change line already says it is new, so the strip keeps its note for screen readers.
    await waitFor(() => expect(detail.querySelector('.dl-hgrid__note')).not.toBeNull());
    expect(detail.querySelector('.dl-hgrid__note')).toHaveClass('dl-sr');
    expect(
      await within(detail).findByRole('heading', { level: 3, name: /Traceback/ }),
    ).toBeInTheDocument();
    expect(detail).toHaveTextContent(`E AssertionError: ${NF1} broke`);
  });

  it('shows a fixed test without evidence, its change from its row', async () => {
    const calls = serve(TRIAGE);
    const user = userEvent.setup();
    renderAt(`/runs/${ID}`);
    await selectedHeading();
    await user.click(await screen.findByRole('option', { name: /test_brownout_recovery/ }));
    expect(await selectedHeading()).toHaveTextContent(FX);
    const detail = detailRegion();
    expect(detail).toHaveTextContent('Fixed: failed in 1adf29af');
    expect(within(detail).getByText('passed')).toBeInTheDocument();
    // Its strip says what its history was, in sight.
    await waitFor(() => expect(detail.querySelector('.dl-hgrid__note')).not.toBeNull());
    expect(detail.querySelector('.dl-hgrid__note')).not.toHaveClass('dl-sr');
    expect(within(detail).queryByRole('heading', { level: 3 })).toBeNull();
    // Its row says all there is to show, so its result is not read.
    expect(calls).not.toContain(`GET /runs/${ID}/result?node_id=${encodeURIComponent(FX)}`);
  });

  it('opens the whole run, and takes a result’s change from /result, never from the line', async () => {
    const world: World = {
      results: [
        ...many('tests/test_a.py::test_still', 250, {
          outcome: 'failed',
          change: 'still_failing',
          was: 'failed',
          streak: { runs: 3, since: SINCE },
        }),
        { node: PASS, outcome: 'passed' },
      ],
    };
    const calls = serve(world);
    const user = userEvent.setup();
    const { router } = renderAt(`/runs/${ID}`);
    await selectedHeading();
    await user.click(screen.getByRole('button', { name: 'All 251 results' }));
    // The whole run is read only once its view is open, 200 at a time.
    await waitFor(() => expect(calls).toContain(`GET /runs/${ID}/results?limit=200&offset=0`));
    const all = await screen.findByRole('listbox', { name: 'Results' });
    const late = 'tests/test_a.py::test_still[220]';
    await user.click(await screen.findByRole('button', { name: 'Show 51 more' }));
    await user.click(await within(all).findByRole('option', { name: /test_still\[220\]/ }));
    await waitFor(() =>
      expect(router.state.location.search).toBe(`?node_id=${encodeURIComponent(late)}`),
    );
    // Its still-failing group's page holding it was never read: /result says how it changed.
    expect(calls.some((c) => c.includes('change=still_failing') && c.includes('offset=200'))).toBe(
      false,
    );
    expect(await within(detailRegion()).findByText(/3 runs/)).toBeInTheDocument();
    expect(detailRegion()).toHaveTextContent('Still failing: 3 runs, since 1ad93e49');
    // Its place comes from its index in the unfiltered whole run.
    expect(cursorAt()).not.toBeNull();
  });

  it('puts the metadata under the queue, closed, titled with its key count', async () => {
    serve(TRIAGE);
    renderAt(`/runs/${ID}`);
    const summary = await screen.findByText('Metadata (2 keys)');
    const details = summary.closest('details') as HTMLDetailsElement;
    expect(details).toHaveClass('dl-disclosure');
    expect(details.open).toBe(false);
    expect(within(details).getByText('bench-02')).toBeInTheDocument();
    const queue = screen.getByRole('region', { name: 'Triage' });
    expect(queue.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('the rerun of the new failures', () => {
  it('copies one command for a few, naming each', async () => {
    serve(TRIAGE);
    const user = userEvent.setup();
    renderAt(`/runs/${ID}`);
    const name = /Copy rerun of 2 new failures/;
    await waitFor(() =>
      expect(screen.getByRole('button', { name })).not.toHaveAttribute('aria-busy'),
    );
    const button = screen.getByRole('button', { name });
    expect(button.closest('.dl-runhead__base')).not.toBeNull();
    await user.click(button);
    await waitFor(async () =>
      expect(await navigator.clipboard.readText()).toBe(`pytest ${NF1} ${NF2}`),
    );
  });

  it('waits, busy, until every page of them is read', async () => {
    const world: World = {
      results: many('tests/test_a.py::test_new', 250, {
        outcome: 'failed',
        change: 'new_failure',
        was: 'passed',
      }),
    };
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const calls = serve(world, (_m, path, query) => {
      if (path === `/runs/${ID}/changes` && query.get('offset') === '200') {
        return held.then(
          () => answer(world)('GET', path, query, new Request('http://x')) as Response,
        );
      }
      return undefined;
    });
    renderAt(`/runs/${ID}`);
    // Past 20 node ids it becomes a download; until the second page arrives it is busy.
    const busy = await screen.findByRole('button', { name: /Download 250 node ids/ });
    expect(busy).toHaveAttribute('aria-busy', 'true');
    await waitFor(() =>
      expect(calls).toContain(`GET /runs/${ID}/changes?change=new_failure&limit=200&offset=200`),
    );
    expect(screen.getByRole('button', { name: /Download 250 node ids/ })).toHaveAttribute(
      'aria-busy',
      'true',
    );
    await act(async () => release());
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Download 250 node ids/ })).not.toHaveAttribute(
        'aria-busy',
      ),
    );
    expect(screen.getByText('pytest @new-failures.txt')).toBeInTheDocument();
  });
});

describe('an address naming a test', () => {
  it('reads a changed test’s group page by page until it holds the test', async () => {
    const world: World = {
      results: [
        { node: NF1, outcome: 'failed', change: 'new_failure', was: 'passed' },
        ...many('tests/test_a.py::test_still', 250, {
          outcome: 'failed',
          change: 'still_failing',
          was: 'failed',
          streak: { runs: 4, since: SINCE },
        }),
      ],
    };
    const late = 'tests/test_a.py::test_still[230]';
    const calls = serve(world);
    const user = userEvent.setup();
    const { router } = renderAt(`/runs/${ID}?node_id=${encodeURIComponent(late)}`);
    expect(await selectedHeading()).toHaveTextContent(late);
    await waitFor(() =>
      expect(calls).toContain(`GET /runs/${ID}/changes?change=still_failing&limit=200&offset=200`),
    );
    expect(detailRegion()).toHaveTextContent('Still failing: 4 runs, since 1ad93e49');
    // Loaded, it is past the rows the group shows; shown, it is the selected row.
    expect(selectedOption()).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Show 200 more' }));
    await waitFor(() => expect(selectedOption()).toHaveTextContent('test_still[230]'));
    expect(router.state.location.search).toBe(`?node_id=${encodeURIComponent(late)}`);
  });

  it('shows a test that did not change as a result, with no row selected', async () => {
    const calls = serve(TRIAGE);
    const { router } = renderAt(`/runs/${ID}?node_id=${encodeURIComponent(PASS)}`);
    expect(await selectedHeading()).toHaveTextContent(PASS);
    const detail = detailRegion();
    expect(await within(detail).findByText('passed')).toBeInTheDocument();
    expect(detail.querySelector('.dl-change')).toBeNull();
    expect(selectedOption()).toBeNull();
    expect(calls).toContain(`GET /runs/${ID}/result?node_id=${encodeURIComponent(PASS)}`);
    // Its place from /result: the first result.
    await waitFor(() => expect(cursorAt()).toBe(-1.5));
    expect(router.state.location.search).toBe(`?node_id=${encodeURIComponent(PASS)}`);
  });

  it('shows the test as loading until /result answers, and says when it could not', async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let broken = true;
    serve(TRIAGE, (method, path, query, request) => {
      if (path !== `/runs/${ID}/result` || query.get('node_id') !== PASS) return undefined;
      return held.then(() =>
        broken
          ? refuse(500, 'internal_error', 'No.')
          : (answer(TRIAGE)(method, path, query, request) as Response),
      );
    });
    const user = userEvent.setup();
    renderAt(`/runs/${ID}?node_id=${encodeURIComponent(PASS)}`);
    expect(await selectedHeading()).toHaveTextContent(PASS);
    expect(within(detailRegion()).getByText('Loading the result…')).toBeInTheDocument();
    await act(async () => release());
    const notice = await within(detailRegion()).findByText(
      'The server answered 500',
      {},
      { timeout: 4000 },
    );
    broken = false;
    await user.click(
      within(notice.closest('.dl-notice') as HTMLElement).getByRole('button', {
        name: 'Try again',
      }),
    );
    expect(await within(detailRegion()).findByText('passed')).toBeInTheDocument();
    expect(within(detailRegion()).queryByText('Loading the result…')).toBeNull();
  });

  it('says the run has no result for a test it lacks, and opens on the first new failure', async () => {
    serve(TRIAGE);
    const wanted = `tests/test_x.py::test_${RLO}gone`;
    const { router } = renderAt(`/runs/${ID}?node_id=${encodeURIComponent(wanted)}`);
    const notice = await screen.findByText(/^This run has no result for/);
    expect(notice).toHaveTextContent('This run has no result for tests/test_x.py::test_U+202Egone');
    expect(notice.textContent).not.toContain(RLO);
    expect(await selectedHeading()).toHaveTextContent(NF1);
    // The address stays as it was until something is chosen.
    expect(router.state.location.search).toBe(`?node_id=${encodeURIComponent(wanted)}`);
  });
});

describe('states', () => {
  it('waits for a running run’s results, with no queue', async () => {
    serve({
      results: [],
      state: 'pending',
      run: { presentation: 'running' },
    });
    renderAt(`/runs/${ID}`);
    expect(
      await screen.findByText('Results arrive when the session finishes.'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Compared with its baseline once the session ends.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Triage' })).toBeNull();
  });

  it('lists the failures of a run part-way through its finish, and counts them aloud', async () => {
    serve({
      results: [
        { node: PASS, outcome: 'passed' },
        { node: NF1, outcome: 'failed' },
      ],
      state: 'pending',
      run: { presentation: 'running' },
    });
    renderAt(`/runs/${ID}`);
    expect(
      await screen.findByText('The failures so far. Changes show once the session ends.'),
    ).toBeInTheDocument();
    expect(await group(/^Failures/)).toBeInTheDocument();
    expect(await selectedHeading()).toHaveTextContent(NF1);
    expect(screen.getByText('1 failure so far')).toBeInTheDocument();
  });

  it('lists the failures of an abandoned run, which was not compared', async () => {
    serve({
      results: [
        { node: NF1, outcome: 'error' },
        { node: PASS, outcome: 'passed' },
      ],
      state: 'pending',
    });
    renderAt(`/runs/${ID}`);
    expect(await screen.findByText('Not compared: no end was recorded.')).toBeInTheDocument();
    expect(
      screen.getByText('The failures it recorded. Changes show only if its end arrives.'),
    ).toBeInTheDocument();
    expect(await selectedHeading()).toHaveTextContent(NF1);
    expect(detailRegion().querySelector('.dl-change')).toBeNull();
  });

  it('lists the tests a stopped run never reached as not reached', async () => {
    serve({
      results: TRIAGE.results,
      gone: [{ node: GONE, was: 'passed' }],
      missing: 'not_reached',
      run: {
        finished_at: null,
        interrupted: true,
        interrupt_reason: 'KeyboardInterrupt',
        presentation: 'interrupted',
        exit_status: 2,
      },
    });
    renderAt(`/runs/${ID}`);
    const status = await screen.findByText('KeyboardInterrupt');
    expect(status).toHaveClass('dl-status__reason');
    const reached = await screen.findByRole('listbox', { name: /^Not reached/ });
    expect(await within(reached).findByRole('option')).toHaveTextContent('test_framing_at_4800');
    expect(screen.queryByText(/Removed tests/)).toBeNull();
  });

  it('says there is nothing to compare with, then lists the failures', async () => {
    serve({
      results: TRIAGE.results.map(({ node, outcome }) => ({ node, outcome })),
      state: 'none',
    });
    renderAt(`/runs/${ID}`);
    expect(await screen.findAllByText('Nothing to compare with yet.')).toHaveLength(2);
    const failures = await screen.findByRole('listbox', { name: /^Failures/ });
    expect(within(failures).getAllByRole('option')).toHaveLength(3);
    expect(await selectedHeading()).toHaveTextContent(NF1);
    // A failure's list does not say where it ran; /result does: the second result.
    await waitFor(() => expect(cursorAt()).toBe(4 - 1.5));
    // No baseline, so no rerun of new failures.
    expect(screen.queryByRole('button', { name: /new failure/ })).toBeNull();
  });

  it('says there were no failures when nothing failed and nothing compares', async () => {
    serve({ results: [{ node: PASS, outcome: 'passed' }], state: 'none' });
    renderAt(`/runs/${ID}`);
    expect(await screen.findByText('No failures.')).toBeInTheDocument();
  });

  it('says nothing changed, then lists what still fails', async () => {
    serve({
      results: [
        { node: PASS, outcome: 'passed' },
        {
          node: SF,
          outcome: 'failed',
          change: 'still_failing',
          was: 'failed',
          streak: { runs: 2, since: BASE },
        },
      ],
    });
    renderAt(`/runs/${ID}`);
    const said = await screen.findByText(/^Nothing changed since/);
    expect(said).toHaveTextContent('Nothing changed since 1adf29af.');
    expect(await group(/^Still failing/)).toBeInTheDocument();
    expect(await selectedHeading()).toHaveTextContent(SF);
    expect(screen.queryByRole('button', { name: /new failure/ })).toBeNull();
  });

  it('says what a failed group read was, in place, and tries again', async () => {
    let broken = true;
    serve(TRIAGE, (_m, path, query) => {
      if (broken && path === `/runs/${ID}/changes` && query.get('change') === 'fixed') {
        return refuse(500, 'internal_error', 'The server failed.');
      }
      return undefined;
    });
    const user = userEvent.setup();
    renderAt(`/runs/${ID}`);
    const notice = await screen.findByText('The server answered 500', {}, { timeout: 4000 });
    broken = false;
    await user.click(
      within(notice.closest('.dl-notice') as HTMLElement).getByRole('button', {
        name: 'Try again',
      }),
    );
    await waitFor(() => expect(screen.queryByText('The server answered 500')).toBeNull(), {
      timeout: 4000,
    });
    expect(await screen.findByRole('listbox', { name: /^Fixed/ })).toBeInTheDocument();
  });

  it('says what a failed detail read was, in the detail, and tries again', async () => {
    let broken = true;
    serve(TRIAGE, (_m, path) => {
      if (broken && path === `/runs/${ID}/result`) return refuse(500, 'internal_error', 'No.');
      return undefined;
    });
    const user = userEvent.setup();
    renderAt(`/runs/${ID}`);
    await selectedHeading();
    const notice = await within(detailRegion()).findByText(
      'The server answered 500',
      {},
      { timeout: 4000 },
    );
    broken = false;
    await user.click(
      within(notice.closest('.dl-notice') as HTMLElement).getByRole('button', {
        name: 'Try again',
      }),
    );
    expect(
      await within(detailRegion()).findByRole('heading', { level: 3, name: /Traceback/ }),
    ).toBeInTheDocument();
  });

  it('says there is no such run', async () => {
    stubServer((_m, path) => (path.startsWith('/runs/') ? refuse(404, 'unknown_run') : undefined));
    renderAt(`/runs/${ID}`);
    expect(await screen.findByText('No run with this id on this server')).toBeInTheDocument();
  });

  it('says the reader is not a member of the run’s project', async () => {
    stubServer((_m, path) => (path === `/runs/${ID}` ? refuse(403, 'not_a_member') : undefined));
    renderAt(`/runs/${ID}`);
    expect(
      await screen.findByText('You are not a member of this run’s project'),
    ).toBeInTheDocument();
  });

  it('offers to try again when the server does not answer', async () => {
    let down = true;
    stubServer((method, path, query, request) => {
      if (down && path === `/runs/${ID}`) return Promise.reject(new TypeError('offline'));
      return answer(TRIAGE)(method, path, query, request);
    });
    const user = userEvent.setup();
    renderAt(`/runs/${ID}`);
    const retry = await screen.findByRole('button', { name: 'Try again' }, { timeout: 4000 });
    down = false;
    await user.click(retry);
    expect(await selectedHeading()).toHaveTextContent(NF1);
  });

  it('keeps everything it shows when the session ends, and says so', async () => {
    let revoked = false;
    serve(TRIAGE, (_m, path) => {
      if (revoked && path !== '/session') return refuse(401, 'unauthenticated');
      return undefined;
    });
    const { queryClient } = renderAt(`/runs/${ID}`);
    await selectedHeading();
    await screen.findByRole('listbox', { name: /^Fixed/ });
    revoked = true;
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['run', ID] });
    });
    expect(await screen.findByText(/^Your session ended at \d\d:\d\d UTC$/)).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Triage' })).toBeInTheDocument();
    expect(await group(/^New failures/)).toBeInTheDocument();
    expect(detailRegion()).toHaveTextContent(NF1);
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toBeInTheDocument();
    expect(screen.queryByText(/The server answered/)).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

it('shows a hidden character in why the run was interrupted as its code point', async () => {
  serve({
    results: TRIAGE.results,
    run: {
      interrupted: true,
      interrupt_reason: `KeyboardInterrupt ${RLO}tpurretni`,
      presentation: 'interrupted',
    },
  });
  renderAt(`/runs/${ID}`);
  const reason = await screen.findByText(/^KeyboardInterrupt/);
  expect(reason).toHaveTextContent('KeyboardInterrupt U+202Etpurretni');
  // The status prints it, and names it in its tooltip.
  expect(reason).toHaveClass('dl-status__reason');
  expect(reason.closest('.dl-status')).toHaveAttribute(
    'title',
    'interrupted: a report arrived saying the session was stopped. Recorded reason: KeyboardInterrupt ⟨U+202E⟩tpurretni',
  );
  expect(reason.closest('.dl-pagehead__sub')?.textContent).not.toContain(RLO);
});

it('keeps its heading focused when it follows a link to a run it has not read yet', async () => {
  let release: () => void = () => undefined;
  const held = new Promise<Response>((resolve) => {
    release = () => resolve(json(200, { ...runOf(TRIAGE), id: OTHER }));
  });
  serve(TRIAGE, (_m, path) => (path === `/runs/${OTHER}` ? held : undefined));
  const { router } = renderAt(`/runs/${ID}`);
  await selectedHeading();
  await act(() => router.navigate(`/runs/${OTHER}`));
  const loading = await screen.findByRole('heading', { level: 1 });
  await waitFor(() => expect(document.activeElement).toBe(loading));
  expect(screen.getByText('Loading…')).toBeInTheDocument();
  await act(async () => release());
  await screen.findByRole('navigation', { name: 'Breadcrumb' });
  const heading = screen.getByRole('heading', { level: 1 });
  expect(heading).toBe(loading);
  expect(document.activeElement).toBe(heading);
});

it('focuses its heading again when Back returns to a test chosen on it', async () => {
  serve(TRIAGE);
  const user = userEvent.setup();
  const { router } = renderAt(`/runs/${ID}`);
  await selectedHeading();
  await user.keyboard('j');
  await waitFor(() => expect(router.state.location.search).toContain('node_id='));
  await user.click(within(detailRegion()).getByRole('link', { name: 'Open result page' }));
  await waitFor(() => expect(router.state.location.pathname).toBe(`/runs/${ID}/result`));
  await act(() => router.navigate(-1));
  await waitFor(() => expect(router.state.location.pathname).toBe(`/runs/${ID}`));
  const heading = await screen.findByRole('heading', { level: 1 });
  await waitFor(() => expect(document.activeElement).toBe(heading));
  expect(await selectedHeading()).toHaveTextContent(NF2);
});

// How long what a query read stays fresh, as the page's hook asked for it.
function staleTimeOf(query: Query | undefined): unknown {
  const time = query?.observers[0]?.options.staleTime;
  return typeof time === 'function' && query ? time(query) : time;
}

describe('what the run was compared with', () => {
  const COMPARED = runOf(TRIAGE);

  it('names its baseline, linking to that run, and how much earlier it started', async () => {
    serve(TRIAGE);
    renderAt(`/runs/${ID}`);
    const link = await screen.findByRole('link', { name: '1adf29af' });
    expect(link).toHaveAttribute('href', `/runs/${BASE}`);
    expect(link).toHaveAttribute('title', BASE);
    expect(link.closest('.dl-runhead__base')).toHaveTextContent(
      'Compared with 1adf29af on main, 42 min earlier',
    );
  });

  it('sets each recorded branch and commit apart, showing its hidden characters', async () => {
    // A right-to-left branch, and a commit that would reverse what follows it.
    const hebrew = 'תיקון';
    serve({
      ...TRIAGE,
      state: 'project',
      run: {
        vcs: { ...VCS, branch: null, commit: `${RLO}ab12cd99` },
        comparison: {
          ...COMPARED.comparison,
          state: 'project',
          baseline: { id: BASE, started_at: '2026-09-27T08:18:00Z', branch: hebrew },
        },
      },
    });
    renderAt(`/runs/${ID}`);
    const link = await screen.findByRole('link', { name: '1adf29af' });
    const said = link.closest('.dl-baseline') as HTMLElement;
    expect(said).toHaveTextContent(
      `No branch recorded (detached HEAD at U+202Eab12cd); compared with 1adf29af on ${hebrew}, 42 min earlier`,
    );
    expect(said.textContent).not.toContain(RLO);
    const isolated = [...said.querySelectorAll('bdi')].map((b) => b.textContent);
    expect(isolated).toEqual(['U+202Eab12cd', hebrew]);
  });

  it('marks the new failures and the fix in the run’s line, and says so', async () => {
    serve(TRIAGE);
    renderAt(`/runs/${ID}`);
    const line = await screen.findByRole('img', {
      name: 'Results in the order pytest reported them: 2 failed, 3 passed, 1 error; 2 new failures, 1 fixed',
    });
    // A new failure stands above the track; a fixed result wears a ring.
    const bar = line.querySelector('.dl-m--failed');
    expect(Number(bar?.getAttribute('y'))).toBeLessThan(0);
    expect(line.querySelector('.dl-ring--passed')).not.toBeNull();
    // pytest's own summary sits under the line, at its measure.
    expect(line.closest('.dl-dotline')?.querySelector('.dl-dotline__under')).not.toBeNull();
  });

  it('draws a plain line for a run with nothing to compare with', async () => {
    serve({
      results: [
        { node: NF1, outcome: 'failed' },
        { node: PASS, outcome: 'passed' },
      ],
      state: 'none',
    });
    renderAt(`/runs/${ID}`);
    const said = await screen.findAllByText('Nothing to compare with yet.');
    expect(said[0]?.closest('.dl-runhead__base')).not.toBeNull();
    const line = await screen.findByRole('img', {
      name: 'Results in the order pytest reported them: 1 failed, 1 passed',
    });
    expect(Number(line.querySelector('.dl-m--failed')?.getAttribute('y'))).toBe(0);
    expect(line.querySelector('.dl-ring')).toBeNull();
  });

  it('says an abandoned run was not compared, and reads it again once stale', async () => {
    serve({ results: [{ node: NF1, outcome: 'failed' }], state: 'pending' });
    const { queryClient } = renderAt(`/runs/${ID}`);
    expect(await screen.findByText('Not compared: no end was recorded.')).toBeInTheDocument();
    await selectedHeading();
    // vantage push may still deliver its end, and with it its comparison.
    for (const key of [
      ['run', ID],
      ['run', ID, 'outcomes', 'pending'],
      ['run', ID, 'results', ['failed', 'error'], 'pending'],
    ]) {
      const query = queryClient.getQueryCache().find({ queryKey: key, exact: true });
      expect(staleTimeOf(query)).toBe(30_000);
    }
  });

  it('reads the page again once the run it showed running has its end, and marks it', async () => {
    let ended = false;
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const running: World = {
      results: TRIAGE.results.map(({ node, outcome }) => ({ node, outcome })),
      state: 'pending',
      run: { presentation: 'running' },
    };
    stubServer((method, path, query, request) => {
      if (!ended) return answer(running)(method, path, query, request);
      if (path === `/runs/${ID}/outcomes`) {
        return held.then(() => answer(TRIAGE)(method, path, query, request) as Response);
      }
      return answer(TRIAGE)(method, path, query, request);
    });
    const { queryClient } = renderAt(`/runs/${ID}`);
    await screen.findByText('Compared with its baseline once the session ends.');
    const line = () => document.querySelector('svg[role="img"]');
    await waitFor(() => expect(line()).not.toBeNull());
    expect(line()?.querySelector('.dl-ring')).toBeNull();
    ended = true;
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['run', ID], exact: true });
    });
    await screen.findByRole('link', { name: '1adf29af' });
    // The line read while it ran stays drawn until the new one arrives.
    expect(line()).not.toBeNull();
    expect(screen.queryByText('Loading results…')).toBeNull();
    // Compared now, the queue lists its changes.
    expect(await screen.findByRole('listbox', { name: /^New failures/ })).toBeInTheDocument();
    await act(async () => release());
    await waitFor(() => expect(line()?.querySelector('.dl-ring--passed')).not.toBeNull());
    expect(Number(line()?.querySelector('.dl-m--failed')?.getAttribute('y'))).toBeLessThan(0);
  });

  it('keeps what it read of a run with an exit status for good', async () => {
    serve(TRIAGE);
    const { queryClient } = renderAt(`/runs/${ID}`);
    await screen.findByRole('link', { name: '1adf29af' });
    await selectedHeading();
    await waitFor(() =>
      expect(
        queryClient
          .getQueryCache()
          .find({ queryKey: ['run', ID, 'outcomes', 'final'], exact: true })?.state.data,
      ).toBeDefined(),
    );
    for (const key of [
      ['run', ID],
      ['run', ID, 'outcomes', 'final'],
      ['run', ID, 'changes', 'new_failure', 'final'],
    ]) {
      const query = queryClient.getQueryCache().find({ queryKey: key, exact: true });
      expect(staleTimeOf(query)).toBe(Number.POSITIVE_INFINITY);
    }
  });
});

it('reads removed tests only once their group is opened', async () => {
  const calls = serve(TRIAGE);
  const user = userEvent.setup();
  renderAt(`/runs/${ID}`);
  await selectedHeading();
  await screen.findByRole('listbox', { name: /^New tests/ });
  expect(calls.some((c) => c.includes('change=removed'))).toBe(false);
  await user.click(screen.getByRole('button', { name: /Removed tests/ }));
  await screen.findByRole('option', { name: /test_framing_at_4800/ });
  expect(calls).toContain(`GET /runs/${ID}/changes?change=removed&limit=200&offset=0`);
});

it('never reads a group with nothing in it', async () => {
  const calls = serve({
    results: [{ node: NF1, outcome: 'failed', change: 'new_failure', was: 'passed' }],
  });
  renderAt(`/runs/${ID}`);
  await selectedHeading();
  const changes = calls.filter((c) => c.includes('/changes'));
  expect(changes).toEqual([`GET /runs/${ID}/changes?change=new_failure&limit=200&offset=0`]);
  fireEvent.keyDown(document.body, { key: 'k' });
  expect(await selectedHeading()).toHaveTextContent(NF1);
});
