// The run page's result tables lead to each result.
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
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
  if (path === `/runs/${id}/outcomes`) return json(200, { outcomes: 'F.' });
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
    if (path === `/runs/${ID}/outcomes`) return json(200, { outcomes: 'F.' });
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
    if (path === `/runs/${ID}/outcomes`) return json(200, { outcomes: 'F.' });
    if (path === `/runs/${ID}/metadata`) return json(200, { items: [], files: [] });
    if (path === `/runs/${ID}/results`) return json(200, { items: [], has_more: false });
    return undefined;
  });
  renderAt(`/runs/${ID}`);
  const reason = await screen.findByText(/^KeyboardInterrupt/);
  expect(reason).toHaveTextContent('KeyboardInterrupt U+202Etpurretni');
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
