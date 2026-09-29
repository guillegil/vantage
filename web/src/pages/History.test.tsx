// A test's history against a stand-in server: paged by cursor, each row
// opening its run's result, and every way the answer can be refused.
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { historyHref, resultHref } from '../adapt';
import { clearSessionEnded } from '../app/sessionEnd';
import { type Handler, json, refuse, renderAt, stubServer } from '../testing/server';

const NEW = '0123abcd0123abcd0123abcd0123abcd';
const OLD = '9876fedc9876fedc9876fedc9876fedc';
const NODE = 'tests/power/test_rails.py::test_rail_under_load[max_load]';
const HERE = historyHref('firmware', NODE);

function entry(runId: string, outcome: string, startedAt: string) {
  return {
    run_id: runId,
    started_at: startedAt,
    finished_at: null,
    outcome,
    duration: 1.5,
    vcs: {
      commit: '7aa1c5d9e0',
      branch: 'main',
      commit_subject: null,
      commit_subject_truncated: false,
      dirty: false,
    },
    change: null,
  };
}

const FIRST = {
  items: [entry(NEW, 'failed', '2026-09-27T10:00:00Z')],
  has_more: true,
  next_cursor: 'past-new',
};
const SECOND = {
  items: [entry(OLD, 'passed', '2026-09-26T10:00:00Z')],
  has_more: false,
  next_cursor: null,
};

function server(overrides: Handler = () => undefined) {
  return stubServer((method, path, query, request) => {
    const given = overrides(method, path, query, request);
    if (given) return given;
    if (path === '/projects/firmware/tests/history' && query.get('node_id') === NODE) {
      return json(200, query.get('cursor') === 'past-new' ? SECOND : FIRST);
    }
    return undefined;
  });
}

beforeEach(() => {
  clearSessionEnded();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('a test’s history', () => {
  it('lists its results newest first, a page at a time, never inventing a total', async () => {
    const calls = server();
    renderAt(HERE);
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(NODE);
    expect(await screen.findByText('1 result shown, newest first. More exist.')).toBeTruthy();
    const table = screen.getByRole('table', { name: 'This test’s results, newest first' });
    expect(within(table).getByRole('link', { name: '0123abcd' })).toHaveAttribute(
      'href',
      resultHref(NEW, NODE),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Load 50 more' }));
    expect(await screen.findByText('All 2 results shown.')).toBeInTheDocument();
    const rows = within(table).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('0123abcd');
    expect(rows[2]).toHaveTextContent('9876fedc');
    const asked = calls
      .filter((c) => c.startsWith('GET /projects/'))
      .map((c) => new URL(c.slice(4), 'http://h').searchParams);
    expect(asked.map((q) => [q.get('node_id'), q.get('limit'), q.get('cursor')])).toEqual([
      [NODE, '50', null],
      [NODE, '50', 'past-new'],
    ]);
    expect(screen.getByRole('link', { name: 'Runs', current: 'page' })).toHaveAttribute(
      'href',
      '/p/firmware/runs',
    );
  });

  it('draws what it listed oldest first, each run opening its result', async () => {
    server((_m, path) =>
      path === '/projects/firmware/tests/history'
        ? json(200, { ...FIRST, items: [...FIRST.items, ...SECOND.items], has_more: false })
        : undefined,
    );
    const { router } = renderAt(HERE);
    const slider = await screen.findByRole('slider', { name: `History of ${NODE}` });
    expect(slider).toHaveAccessibleDescription('failing 1 run since 0123abcd');
    slider.getBoundingClientRect = () => ({ left: 0, width: 18 }) as DOMRect;
    fireEvent.click(slider, { clientX: 2 });
    await waitFor(() => expect(router.state.location.pathname).toBe(`/runs/${OLD}/result`));
  });

  it('opens a run’s result from its row within the page', async () => {
    server();
    const { router } = renderAt(HERE);
    await userEvent.click(await screen.findByRole('link', { name: '0123abcd' }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/runs/${NEW}/result`));
    expect(new URLSearchParams(router.state.location.search).get('node_id')).toBe(NODE);
  });

  it('heads the page while its results load', async () => {
    server((_m, path) =>
      path === '/projects/firmware/tests/history'
        ? new Promise<Response>(() => undefined)
        : undefined,
    );
    renderAt(HERE);
    expect(await screen.findByText('Loading results…')).toHaveAttribute('role', 'status');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(NODE);
    expect(document.title).toBe(`${NODE} · history · firmware · vantage`);
  });

  it('writes out a hidden character in the node id in the window’s title', async () => {
    const node = `tests/test_a.py::test_x[${String.fromCodePoint(0x202e)}gnp.exe]`;
    server((_m, path) =>
      path === '/projects/firmware/tests/history'
        ? json(200, { items: [], has_more: false, next_cursor: null })
        : undefined,
    );
    renderAt(historyHref('firmware', node));
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(
      'tests/test_a.py::test_x[U+202Egnp.exe]',
    );
    await waitFor(() =>
      expect(document.title).toBe(
        'tests/test_a.py::test_x[⟨U+202E⟩gnp.exe] · history · firmware · vantage',
      ),
    );
  });

  it('says so when no run of the project reported the test', async () => {
    server((_m, path) =>
      path === '/projects/firmware/tests/history'
        ? json(200, { items: [], has_more: false, next_cursor: null })
        : undefined,
    );
    renderAt(HERE);
    expect(await screen.findByText('No run of firmware has reported this test')).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
  });
});

describe('a history that cannot be shown', () => {
  it('says no project has that name', async () => {
    server((_m, path) =>
      path.startsWith('/projects/firmware/') ? refuse(404, 'unknown_project') : undefined,
    );
    renderAt(HERE);
    expect(await screen.findByText('No project named firmware on this server')).toBeTruthy();
  });

  it('says the reader is not a member', async () => {
    server((_m, path) =>
      path.startsWith('/projects/firmware/') ? refuse(403, 'not_a_member') : undefined,
    );
    renderAt(HERE);
    expect(await screen.findByText('You are not a member of firmware')).toBeTruthy();
  });

  it('shows the danger notice in place of the list when the server fails', async () => {
    server((_m, path) =>
      path.startsWith('/projects/firmware/') ? refuse(503, 'busy', 'Busy.') : undefined,
    );
    renderAt(HERE);
    expect(await screen.findByRole('alert', {}, { timeout: 5000 })).toHaveTextContent(
      'The server answered 503',
    );
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('keeps what it listed when the session ends, and says so', async () => {
    server((_m, path, query) =>
      path === '/projects/firmware/tests/history' && query.get('cursor') === 'past-new'
        ? refuse(401, 'unauthenticated')
        : undefined,
    );
    renderAt(HERE);
    await userEvent.click(await screen.findByRole('button', { name: 'Load 50 more' }));
    expect(await screen.findByText(/^Your session ended at \d\d:\d\d UTC$/)).toBeInTheDocument();
    const table = screen.getByRole('table', { name: 'This test’s results, newest first' });
    expect(within(table).getByRole('link', { name: '0123abcd' })).toBeInTheDocument();
    expect(screen.getByRole('slider', { name: `History of ${NODE}` })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('is no page without a test', async () => {
    const calls = server();
    renderAt('/p/firmware/tests/history');
    expect(await screen.findByText('Nothing at this address')).toBeInTheDocument();
    expect(calls.some((c) => c.includes('/tests/history'))).toBe(false);
  });
});
