// The run page's result tables lead to each result.
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { resultHref } from '../adapt';
import { json, renderAt, stubServer } from '../testing/server';

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
