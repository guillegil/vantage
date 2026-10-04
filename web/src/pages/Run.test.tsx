// The run page's result tables lead to each result.
import type { Query } from '@tanstack/react-query';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resultHref } from '../adapt';
import { clearSessionEnded } from '../app/sessionEnd';
import { json, refuse, renderAt, stubServer } from '../testing/server';

const ID = '0123abcd0123abcd0123abcd0123abcd';
const RLO = String.fromCodePoint(0x202e);

const RUN = {
  id: ID,
  started_at: '2026-09-27T09:00:00Z',
  finished_at: '2026-09-27T09:00:05Z',
  exit_status: 1,
  interrupted: false,
  interrupt_reason: null,
  presentation: 'finished',
  vcs: null,
  recorded_by: 'alice',
  project: 'default',
  counts: { passed: 1, failed: 1, error: 0, skipped: 0, xfailed: 0, xpassed: 0 },
  comparison: { state: 'none', baseline: null, counts: null },
};

function item(node: string, outcome: string, message: string | null) {
  return {
    node_id: node,
    file_path: 'tests/test_a.py',
    class_name: null,
    function_name: 'test_x',
    param_id: null,
    outcome,
    duration: 0.1,
    started_at: null,
    finished_at: null,
    setup_outcome: null,
    call_outcome: null,
    teardown_outcome: null,
    setup_duration: null,
    call_duration: null,
    teardown_duration: null,
    worker_id: null,
    failure: message
      ? {
          failure_type: 'AssertionError',
          failure_message: message,
          failure_message_truncated: false,
          failure_path: null,
          failure_lineno: null,
          skip_reason: null,
          xfail_reason: null,
        }
      : null,
  };
}

const FAILING = 'tests/test_a.py::test_fails';
const PASSING = 'tests/test_a.py::test_passes';

const OTHER = '9876fedc9876fedc9876fedc9876fedc';

// A finished run of default, `id`, with one failing and one passing result.
function answer(id: string, path: string, query: URLSearchParams) {
  if (path === `/runs/${id}`) return json(200, { ...RUN, id });
  if (path === `/runs/${id}/outcomes`) return json(200, { outcomes: 'F.', changes: null });
  if (path === `/runs/${id}/metadata`) return json(200, { items: [], files: [] });
  if (path === `/runs/${id}/results`) {
    const failing = item(FAILING, 'failed', 'AssertionError: boom');
    const items = query.getAll('outcome').length
      ? [failing]
      : [failing, item(PASSING, 'passed', null)];
    return json(200, { items, has_more: false });
  }
  return undefined;
}

beforeEach(() => {
  clearSessionEnded();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

it('links every result in both tables to its page, and shows hidden characters', async () => {
  stubServer((_m, path, query) => {
    if (path === `/runs/${ID}`) return json(200, RUN);
    if (path === `/runs/${ID}/outcomes`) return json(200, { outcomes: 'F.', changes: null });
    if (path === `/runs/${ID}/metadata`) return json(200, { items: [], files: [] });
    if (path === `/runs/${ID}/results`) {
      const failing = item(FAILING, 'failed', `AssertionError: ${RLO}evil`);
      const items = query.getAll('outcome').length
        ? [failing]
        : [failing, item(PASSING, 'passed', null)];
      return json(200, { items, has_more: false });
    }
    return undefined;
  });
  const { router } = renderAt(`/runs/${ID}`);
  const notPassing = (
    await screen.findByRole('heading', { level: 2, name: 'Not passing' })
  ).closest('section') as HTMLElement;
  const all = screen
    .getByRole('heading', { level: 2, name: 'All results' })
    .closest('section') as HTMLElement;
  await waitFor(() => expect(within(all).getAllByRole('link')).toHaveLength(2));
  const [failing, passing] = within(all).getAllByRole('link');
  expect(failing).toHaveAttribute('href', resultHref(ID, FAILING));
  expect(passing).toHaveAttribute('href', resultHref(ID, PASSING));
  const link = await within(notPassing).findByRole('link');
  expect(link).toHaveAttribute('href', resultHref(ID, FAILING));
  expect(notPassing).toHaveTextContent('AssertionError: U+202Eevil');
  expect(notPassing.textContent).not.toContain(RLO);
  await userEvent.click(link);
  await waitFor(() => expect(router.state.location.pathname).toBe(`/runs/${ID}/result`));
});

it('shows a hidden character in why the run was interrupted as its code point', async () => {
  stubServer((_m, path) => {
    if (path === `/runs/${ID}`) {
      return json(200, {
        ...RUN,
        interrupted: true,
        interrupt_reason: `KeyboardInterrupt ${RLO}tpurretni`,
        presentation: 'interrupted',
      });
    }
    if (path === `/runs/${ID}/outcomes`) return json(200, { outcomes: 'F.', changes: null });
    if (path === `/runs/${ID}/metadata`) return json(200, { items: [], files: [] });
    if (path === `/runs/${ID}/results`) return json(200, { items: [], has_more: false });
    return undefined;
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

it('keeps everything it shows when the session ends, and says so', async () => {
  let revoked = false;
  stubServer((_m, path, query) => {
    if (revoked && path !== '/session') return refuse(401, 'unauthenticated');
    return answer(ID, path, query);
  });
  const { queryClient } = renderAt(`/runs/${ID}`);
  await screen.findByRole('heading', { level: 2, name: 'Not passing' });
  revoked = true;
  await act(async () => {
    await queryClient.refetchQueries({ queryKey: ['run', ID] });
  });
  expect(await screen.findByText(/^Your session ended at \d\d:\d\d UTC$/)).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 2, name: 'Not passing' })).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 2, name: 'All results' })).toBeInTheDocument();
  expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toBeInTheDocument();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('keeps its heading focused when it follows a link to a run it has not read yet', async () => {
  let release: () => void = () => undefined;
  const held = new Promise<Response>((resolve) => {
    release = () => resolve(json(200, { ...RUN, id: OTHER }));
  });
  stubServer((_m, path, query) => {
    if (path === `/runs/${OTHER}`) return held;
    return answer(ID, path, query) ?? answer(OTHER, path, query);
  });
  const { router } = renderAt(`/runs/${ID}`);
  await screen.findByRole('heading', { level: 2, name: 'Not passing' });
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

// How long what a query read stays fresh, as the page's hook asked for it.
function staleTimeOf(query: Query | undefined): unknown {
  const time = query?.observers[0]?.options.staleTime;
  return typeof time === 'function' && query ? time(query) : time;
}

describe('what the run was compared with', () => {
  const BASE = '1adf29af1adf29af1adf29af1adf29af';
  const COMPARED = {
    ...RUN,
    vcs: {
      commit: '7f3a2c1e9b0d',
      branch: 'main',
      commit_subject: 'Tune the rails',
      commit_subject_truncated: false,
      dirty: false,
    },
    comparison: {
      state: 'branch',
      baseline: { id: BASE, started_at: '2026-09-27T08:18:00Z', branch: 'main' },
      counts: {
        new_failure: 1,
        still_failing: 0,
        fixed: 1,
        new_test: 0,
        removed: 0,
        not_reached: 0,
      },
    },
  };

  function serve(run: object, outcomes: object) {
    stubServer((_m, path, query) => {
      if (path === `/runs/${ID}`) return json(200, run);
      if (path === `/runs/${ID}/outcomes`) return json(200, outcomes);
      return answer(ID, path, query);
    });
  }

  it('names its baseline, linking to that run, and how much earlier it started', async () => {
    serve(COMPARED, { outcomes: 'F.', changes: 'nf' });
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
    serve(
      {
        ...COMPARED,
        vcs: { ...COMPARED.vcs, branch: null, commit: `${RLO}ab12cd99` },
        comparison: {
          ...COMPARED.comparison,
          state: 'project',
          baseline: { ...COMPARED.comparison.baseline, branch: hebrew },
        },
      },
      { outcomes: 'F.', changes: 'nf' },
    );
    renderAt(`/runs/${ID}`);
    const link = await screen.findByRole('link', { name: '1adf29af' });
    const said = link.closest('.dl-runhead__base') as HTMLElement;
    expect(said).toHaveTextContent(
      `No branch recorded (detached HEAD at U+202Eab12cd); compared with 1adf29af on ${hebrew}, 42 min earlier`,
    );
    expect(said.textContent).not.toContain(RLO);
    const isolated = [...said.querySelectorAll('bdi')].map((b) => b.textContent);
    expect(isolated).toEqual(['U+202Eab12cd', hebrew]);
  });

  it('marks the new failure and the fix in the run’s line, and says so', async () => {
    serve(COMPARED, { outcomes: 'F.', changes: 'nf' });
    renderAt(`/runs/${ID}`);
    const line = await screen.findByRole('img', {
      name: 'Results in the order pytest reported them: 1 failed, 1 passed; 1 new failure, 1 fixed',
    });
    // A new failure stands above the track; a fixed result wears a ring.
    const bar = line.querySelector('.dl-m--failed');
    expect(Number(bar?.getAttribute('y'))).toBeLessThan(0);
    expect(line.querySelector('.dl-ring--passed')).not.toBeNull();
  });

  it('draws a plain line for a run with nothing to compare with, and says so', async () => {
    serve(RUN, { outcomes: 'F.', changes: null });
    renderAt(`/runs/${ID}`);
    const said = await screen.findByText('Nothing to compare with yet.');
    expect(said.closest('.dl-runhead__base')).not.toBeNull();
    const line = await screen.findByRole('img', {
      name: 'Results in the order pytest reported them: 1 failed, 1 passed',
    });
    expect(Number(line.querySelector('.dl-m--failed')?.getAttribute('y'))).toBe(0);
    expect(line.querySelector('.dl-ring')).toBeNull();
  });

  const PENDING = {
    ...RUN,
    finished_at: null,
    exit_status: null,
    comparison: { state: 'pending', baseline: null, counts: null },
  };

  it('says a running run is compared once its session ends', async () => {
    serve({ ...PENDING, presentation: 'running' }, { outcomes: 'F.', changes: null });
    renderAt(`/runs/${ID}`);
    expect(
      await screen.findByText('Compared with its baseline once the session ends.'),
    ).toBeInTheDocument();
  });

  it('says an abandoned run was not compared, and reads it again once stale', async () => {
    serve({ ...PENDING, presentation: 'abandoned' }, { outcomes: 'F.', changes: null });
    const { queryClient } = renderAt(`/runs/${ID}`);
    expect(await screen.findByText('Not compared: no end was recorded.')).toBeInTheDocument();
    // vantage push may still deliver its end, and with it its comparison.
    for (const key of [
      ['run', ID],
      ['run', ID, 'outcomes', 'pending'],
    ]) {
      const query = queryClient.getQueryCache().find({ queryKey: key, exact: true });
      expect(staleTimeOf(query)).toBe(30_000);
    }
  });

  it('reads the line again once the run it showed running has its end, and marks it', async () => {
    let ended = false;
    let release: () => void = () => undefined;
    const held = new Promise<Response>((resolve) => {
      release = () => resolve(json(200, { outcomes: 'F.', changes: 'nf' }));
    });
    stubServer((_m, path, query) => {
      if (path === `/runs/${ID}`)
        return json(200, ended ? COMPARED : { ...PENDING, presentation: 'running' });
      if (path === `/runs/${ID}/outcomes`) {
        return ended ? held : json(200, { outcomes: 'F.', changes: null });
      }
      return answer(ID, path, query);
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
    await act(async () => release());
    await waitFor(() => expect(line()?.querySelector('.dl-ring--passed')).not.toBeNull());
    expect(Number(line()?.querySelector('.dl-m--failed')?.getAttribute('y'))).toBeLessThan(0);
  });

  it('keeps what it read of a run with an exit status for good', async () => {
    serve(COMPARED, { outcomes: 'F.', changes: 'nf' });
    const { queryClient } = renderAt(`/runs/${ID}`);
    await screen.findByRole('link', { name: '1adf29af' });
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
    ]) {
      const query = queryClient.getQueryCache().find({ queryKey: key, exact: true });
      expect(staleTimeOf(query)).toBe(Number.POSITIVE_INFINITY);
    }
  });
});
