// The result page against a stand-in server: its evidence, its phases, the
// test's latest runs, and every way the answer can be refused.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resultHref } from '../adapt';
import { clearSessionEnded } from '../app/sessionEnd';
import { type Handler, json, refuse, renderAt, stubServer } from '../testing/server';

const ID = '0123abcd0123abcd0123abcd0123abcd';
const OLD = '9876fedc9876fedc9876fedc9876fedc';
const NODE = 'tests/power/test_rails.py::test_rail_under_load[max_load]';
const RLO = String.fromCodePoint(0x202e);

const RUN = {
  id: ID,
  started_at: '2026-09-27T09:00:00Z',
  finished_at: '2026-09-27T09:00:05Z',
  exit_status: 1,
  interrupted: false,
  interrupt_reason: null,
  presentation: 'finished',
  vcs: {
    commit: '7f3a2c1e9b0d',
    branch: 'main',
    commit_subject: 'Tune the rails',
    commit_subject_truncated: false,
    dirty: false,
  },
  recorded_by: 'alice',
  project: 'firmware',
  counts: { passed: 3, failed: 1, error: 0, skipped: 0, xfailed: 0, xpassed: 0 },
};

const TRACEBACK = [
  'tests/power/test_rails.py:88: in test_rail_under_load',
  '>   assert vout == 3.3',
  'E   AssertionError: expected 3.3V, got 3.38V',
].join('\n');

const RESULT = {
  node_id: NODE,
  file_path: 'tests/power/test_rails.py',
  class_name: null,
  function_name: 'test_rail_under_load',
  param_id: 'max_load',
  outcome: 'failed',
  duration: 7.4,
  started_at: '2026-09-27T09:00:01Z',
  finished_at: '2026-09-27T09:00:04Z',
  setup_outcome: 'passed',
  call_outcome: 'failed',
  teardown_outcome: 'passed',
  setup_duration: 0.9,
  call_duration: 6.2,
  teardown_duration: 0.3,
  worker_id: 'gw3',
  failure_type: 'AssertionError',
  failure_message: 'AssertionError: expected 3.3V, got 3.38V',
  failure_message_truncated: false,
  failure_path: 'tests/power/test_rails.py',
  failure_lineno: 88,
  failure_repr: "AssertionError('expected 3.3V, got 3.38V')",
  failure_repr_truncated: false,
  traceback: TRACEBACK,
  traceback_truncated: true,
  skip_reason: null,
  skip_reason_truncated: false,
  xfail_reason: null,
  xfail_reason_truncated: false,
  captured_stdout: 'measuring vout\n',
  captured_stdout_truncated: false,
  captured_stderr: null,
  captured_stderr_truncated: true,
};

const NOTHING = {
  failure_type: null,
  failure_message: null,
  failure_path: null,
  failure_lineno: null,
  failure_repr: null,
  traceback: null,
  traceback_truncated: false,
  captured_stdout: null,
  captured_stderr: null,
  captured_stderr_truncated: false,
};

function entry(runId: string, outcome: string, startedAt: string, duration: number) {
  return { run_id: runId, started_at: startedAt, finished_at: null, outcome, duration, vcs: null };
}

const HISTORY = {
  items: [
    entry(ID, 'failed', '2026-09-27T09:00:00Z', 7.4),
    entry(OLD, 'passed', '2026-09-26T09:00:00Z', 6.1),
  ],
  has_more: true,
  next_cursor: 'next',
};

const METADATA = {
  items: [
    {
      key: 'rig',
      name: null,
      value: 'bench-02',
      status: 'captured',
      source: 'session',
      source_file: null,
      declared: false,
    },
  ],
  files: [],
};

// A finished run of firmware holding the result, unless a handler says otherwise.
function server(overrides: Handler = () => undefined) {
  return stubServer((method, path, query, request) => {
    const given = overrides(method, path, query, request);
    if (given) return given;
    if (path === `/runs/${ID}`) return json(200, RUN);
    if (path === `/runs/${ID}/result` && query.get('node_id') === NODE) return json(200, RESULT);
    if (path === `/runs/${ID}/result`) return refuse(404, 'unknown_result');
    if (path === `/runs/${ID}/metadata`) return json(200, METADATA);
    if (path === '/projects/firmware/tests/history') return json(200, HISTORY);
    return undefined;
  });
}

const HERE = resultHref(ID, NODE);

beforeEach(() => {
  clearSessionEnded();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('a result', () => {
  it('heads the page with the test, its outcome, and the run it belongs to', async () => {
    server();
    renderAt(HERE);
    const heading = await screen.findByRole('heading', { level: 1 });
    expect(heading).toHaveTextContent(NODE);
    await waitFor(() =>
      expect(document.querySelector('.dl-badge--failed')).toHaveTextContent('failed'),
    );
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' });
    expect(within(crumbs).getByRole('link', { name: 'Runs' })).toHaveAttribute(
      'href',
      '/p/firmware/runs',
    );
    expect(within(crumbs).getByRole('link', { name: '0123abcd' })).toHaveAttribute(
      'href',
      `/runs/${ID}`,
    );
    // The app bar names the run's project.
    expect(screen.getByRole('link', { name: 'Runs', current: 'page' })).toHaveAttribute(
      'href',
      '/p/firmware/runs',
    );
    expect(screen.getByText('7f3a2c1')).toBeInTheDocument();
    expect(document.title).toBe(`${NODE} · 0123abcd · firmware · vantage`);
  });

  it('shows every piece of evidence recorded, what was cut, and what was not kept', async () => {
    server();
    renderAt(HERE);
    const traceback = await screen.findByRole('heading', { level: 2, name: 'Traceback' });
    const section = traceback.closest('section') as HTMLElement;
    expect(within(section).getByText('call phase · tests/power/test_rails.py:88')).toBeTruthy();
    expect(section.querySelector('pre')?.textContent).toBe(TRACEBACK);
    expect(within(section).getByText('truncated at capture')).toBeInTheDocument();
    expect(within(section).getByText('Only the first 64 KiB was kept.')).toBeInTheDocument();
    expect(
      within(section).getByText(
        'May contain any value a test printed or asserted, credentials included.',
      ),
    ).toBeInTheDocument();
    for (const title of ['Message', 'Exception repr', 'Captured stdout']) {
      expect(screen.getByRole('heading', { level: 2, name: title })).toBeInTheDocument();
    }
    expect(screen.getByText('measuring vout')).toBeInTheDocument();
    expect(screen.getByText('Captured stderr was not kept')).toBeInTheDocument();
    expect(screen.getAllByText(/credentials included/)).toHaveLength(4);
  });

  it('draws the phases and the session it ran in', async () => {
    server();
    renderAt(HERE);
    expect(
      await screen.findByRole('img', { name: 'setup 900 ms, call 6.20 s failed, teardown 300 ms' }),
    ).toBeInTheDocument();
    expect(screen.getByText('gw3')).toBeInTheDocument();
    expect(await screen.findByText('bench-02')).toBeInTheDocument();
  });

  it('draws the test’s latest runs, oldest first, each opening its result', async () => {
    server();
    const { router } = renderAt(HERE);
    const slider = await screen.findByRole('slider', { name: `History of ${NODE}` });
    expect(slider).toHaveAttribute('aria-valuetext', '0123abcd: failed');
    expect(slider).toHaveAccessibleDescription('failing 1 run since 0123abcd');
    expect(screen.getByRole('img', { name: /^Duration over 2 runs/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Full history' })).toHaveAttribute(
      'href',
      `/p/firmware/tests/history?node_id=${encodeURIComponent(NODE)}`,
    );
    fireEvent.focus(slider);
    fireEvent.keyDown(slider, { key: 'Home' });
    fireEvent.keyDown(slider, { key: 'Enter' });
    await waitFor(() => expect(router.state.location.pathname).toBe(`/runs/${OLD}/result`));
    expect(router.state.location.search).toBe(`?node_id=${encodeURIComponent(NODE)}`);
  });

  it('asks for the latest runs of the run’s project only', async () => {
    const calls = server();
    renderAt(HERE);
    await screen.findByRole('slider');
    const asked = calls
      .filter((c) => c.startsWith('GET /projects/'))
      .map((c) => new URL(c.slice(4), 'http://h'));
    expect(asked).toHaveLength(1);
    expect(asked[0]?.pathname).toBe('/projects/firmware/tests/history');
    expect(asked[0]?.searchParams.get('node_id')).toBe(NODE);
    expect(asked[0]?.searchParams.get('limit')).toBe('24');
  });

  it('keeps a finished run’s result for good', async () => {
    server();
    const { queryClient } = renderAt(HERE);
    await screen.findByRole('heading', { level: 2, name: 'Traceback' });
    const query = queryClient.getQueryCache().find({ queryKey: ['run', ID, 'result', NODE] });
    expect(query?.observers[0]?.options.staleTime).toBe(Number.POSITIVE_INFINITY);
  });

  it('keeps a running run’s result for a while only', async () => {
    server((_m, path) =>
      path === `/runs/${ID}` ? json(200, { ...RUN, presentation: 'running' }) : undefined,
    );
    const { queryClient } = renderAt(HERE);
    await screen.findByRole('heading', { level: 2, name: 'Traceback' });
    const query = queryClient.getQueryCache().find({ queryKey: ['run', ID, 'result', NODE] });
    expect(query?.observers[0]?.options.staleTime).toBe(30_000);
  });

  it('says failure text was not recorded when the run did not record it', async () => {
    server((_m, path) =>
      path === `/runs/${ID}/result` ? json(200, { ...RESULT, ...NOTHING }) : undefined,
    );
    renderAt(HERE);
    expect(await screen.findByText(/^Failure text was not recorded; run with/)).toHaveTextContent(
      'Failure text was not recorded; run with --vantage-failure-text',
    );
    expect(screen.queryByText(/credentials included/)).toBeNull();
  });

  it('gives a skip its reason, and says when nothing was printed', async () => {
    server((_m, path) =>
      path === `/runs/${ID}/result`
        ? json(200, {
            ...RESULT,
            ...NOTHING,
            outcome: 'skipped',
            skip_reason: 'Skipped: no rig attached',
          })
        : undefined,
    );
    renderAt(HERE);
    expect(await screen.findByRole('heading', { level: 2, name: 'Skip reason' })).toBeTruthy();
    expect(screen.getByText('Skipped: no rig attached')).toBeInTheDocument();
    vi.unstubAllGlobals();
    server((_m, path) =>
      path === `/runs/${ID}/result`
        ? json(200, {
            ...RESULT,
            ...NOTHING,
            outcome: 'passed',
            captured_stdout: '',
            captured_stderr: '',
          })
        : undefined,
    );
    renderAt(HERE);
    expect(await screen.findByText('Nothing was printed to stdout or stderr.')).toBeTruthy();
  });

  it('shows a bidi control in the evidence and the node id as its code point', async () => {
    const node = `tests/test_a.py::test_x[${RLO}gnp.exe]`;
    server((_m, path, query) =>
      path === `/runs/${ID}/result` && query.get('node_id') === node
        ? json(200, { ...RESULT, node_id: node, traceback: `E   assert "${RLO}cba" == ""` })
        : undefined,
    );
    renderAt(resultHref(ID, node));
    const traceback = await screen.findByRole('heading', { level: 2, name: 'Traceback' });
    const body = traceback.closest('section')?.querySelector('pre') as HTMLElement;
    expect(body.textContent).toBe('E   assert "U+202Ecba" == ""');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      'tests/test_a.py::test_x[U+202Egnp.exe]',
    );
    expect(document.querySelector('main')?.textContent).not.toContain(RLO);
  });
});

describe('a result that cannot be shown', () => {
  it('says the run has no result for this test', async () => {
    server();
    renderAt(resultHref(ID, 'tests/test_gone.py::test_x'));
    expect(await screen.findByText('Run 0123abcd has no result for this test')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Go to run 0123abcd' })).toHaveAttribute(
      'href',
      `/runs/${ID}`,
    );
  });

  it('says no run has this id, without claiming it was deleted', async () => {
    server((_m, path) => (path.startsWith(`/runs/${ID}`) ? refuse(404, 'unknown_run') : undefined));
    renderAt(HERE);
    expect(await screen.findByText('No run with this id on this server')).toBeTruthy();
    expect(screen.getByText(/never deletes a run/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(NODE);
  });

  it('says the reader is not a member of the run’s project', async () => {
    server((_m, path) =>
      path.startsWith(`/runs/${ID}`) ? refuse(403, 'not_a_member') : undefined,
    );
    renderAt(HERE);
    expect(
      await screen.findByText('You are not a member of this run’s project'),
    ).toBeInTheDocument();
  });

  it('shows the danger notice when the server fails', async () => {
    server((_m, path) =>
      path === `/runs/${ID}/result` ? refuse(500, 'internal', 'Something broke.') : undefined,
    );
    renderAt(HERE);
    // A server error is tried once more before it is shown.
    expect(await screen.findByRole('alert', {}, { timeout: 5000 })).toHaveTextContent(
      'The server answered 500',
    );
  });

  it('shows the danger notice when the server does not answer', async () => {
    server((_m, path) => {
      if (path === `/runs/${ID}`) throw new TypeError('Failed to fetch');
      return undefined;
    });
    renderAt(HERE);
    expect(await screen.findByRole('alert', {}, { timeout: 5000 })).toHaveTextContent(
      /^Can’t reach/,
    );
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(NODE);
  });

  it('keeps the page when the session ends, and says so', async () => {
    let revoked = false;
    server((_m, path) =>
      revoked && path !== '/session' ? refuse(401, 'unauthenticated') : undefined,
    );
    const { queryClient } = renderAt(HERE);
    await screen.findByRole('heading', { level: 2, name: 'Traceback' });
    revoked = true;
    await act(() => queryClient.refetchQueries({ queryKey: ['history'] }));
    expect(await screen.findByText(/^Your session ended at \d\d:\d\d UTC$/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Traceback' })).toBeInTheDocument();
  });

  it('is no page without a test', async () => {
    const calls = server();
    renderAt(`/runs/${ID}/result`);
    expect(await screen.findByText('Nothing at this address')).toBeInTheDocument();
    expect(calls.some((c) => c.includes('/result'))).toBe(false);
  });

  it('is no page with a run id the API could never hold', async () => {
    const calls = server();
    renderAt('/runs/not-a-run/result?node_id=x');
    expect(await screen.findByText('Nothing at this address')).toBeInTheDocument();
    expect(calls.some((c) => c.startsWith('GET /runs/'))).toBe(false);
  });
});
