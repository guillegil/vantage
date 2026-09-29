// A project's runs against a stand-in server: each row's changes against
// its baseline, and its line's change marks.
import type { Query } from '@tanstack/react-query';
import { act, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { clearSessionEnded } from '../app/sessionEnd';
import { json, renderAt, stubServer } from '../testing/server';

const COMPARED = '0123abcd0123abcd0123abcd0123abcd';
const PENDING = '5555eeee5555eeee5555eeee5555eeee';
const FIRST = '1adf29af1adf29af1adf29af1adf29af';
const COUNTS = { passed: 2, failed: 2, error: 0, skipped: 0, xfailed: 0, xpassed: 0 };

function run(id: string, startedAt: string, comparison: object, final = true) {
  return {
    id,
    started_at: startedAt,
    finished_at: final ? startedAt.replace(':00Z', ':05Z') : null,
    exit_status: final ? 1 : null,
    interrupted: false,
    presentation: final ? 'finished' : 'abandoned',
    vcs: null,
    recorded_by: 'alice',
    counts: COUNTS,
    comparison,
  };
}

const RUNS = [
  run(PENDING, '2026-09-27T11:00:00Z', { state: 'pending', baseline: null, counts: null }, false),
  run(COMPARED, '2026-09-27T10:00:00Z', {
    state: 'branch',
    baseline: { id: FIRST, started_at: '2026-09-27T09:00:00Z', branch: 'main' },
    counts: { new_failure: 2, still_failing: 0, fixed: 1, new_test: 0, removed: 0, not_reached: 0 },
  }),
  run(FIRST, '2026-09-27T09:00:00Z', { state: 'none', baseline: null, counts: null }),
];

const OUTCOMES: Record<string, object> = {
  [PENDING]: { outcomes: '.F.F', changes: null },
  [COMPARED]: { outcomes: 'FF.F', changes: 'nnf-' },
  [FIRST]: { outcomes: '..FF', changes: null },
};

function serve() {
  return stubServer((_m, path) => {
    if (path === '/projects/default/runs') {
      return json(200, { items: RUNS, has_more: false, next_cursor: null, metadata_horizon: null });
    }
    const outcomes = /^\/runs\/([0-9a-f]{32})\/outcomes$/.exec(path);
    if (outcomes?.[1]) return json(200, OUTCOMES[outcomes[1]]);
    return undefined;
  });
}

function rowOf(id: string): HTMLElement {
  const link = screen.getByTitle(id);
  return link.closest('.dl-run') as HTMLElement;
}

beforeEach(() => {
  clearSessionEnded();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

it('prints what changed against the baseline in a compared run’s row, and nothing in the rest', async () => {
  serve();
  renderAt('/p/default/runs');
  await screen.findByTitle(COMPARED);
  const changes = rowOf(COMPARED).querySelector('.dl-run__changes') as HTMLElement;
  expect(changes.querySelector('[aria-hidden="true"]')).toHaveTextContent('2 new · 1 fixed');
  expect(changes).toHaveAttribute('title', '2 new failures and 1 fixed, compared with 1adf29af');
  expect(rowOf(PENDING).querySelector('.dl-run__changes')).toBeNull();
  expect(rowOf(FIRST).querySelector('.dl-run__changes')).toBeNull();
});

it('marks each new failure and fix in a compared run’s line, and only there', async () => {
  serve();
  renderAt('/p/default/runs');
  await screen.findByTitle(COMPARED);
  const lifted = (id: string) =>
    [...rowOf(id).querySelectorAll('.dl-m--failed')].filter((r) => Number(r.getAttribute('y')) < 0)
      .length;
  await waitFor(() => expect(lifted(COMPARED)).toBe(2));
  expect(rowOf(COMPARED).querySelectorAll('.dl-ring--passed')).toHaveLength(1);
  expect(rowOf(COMPARED).querySelector('svg[role="img"]')?.getAttribute('aria-label')).toContain(
    '; 2 new failures, 1 fixed',
  );
  for (const id of [PENDING, FIRST]) {
    expect(rowOf(id).querySelectorAll('.dl-m--failed')).toHaveLength(2);
    expect(lifted(id)).toBe(0);
    expect(rowOf(id).querySelector('.dl-ring')).toBeNull();
  }
});

it('reads an abandoned run’s line again once stale, and a final run’s never', async () => {
  serve();
  const { queryClient } = renderAt('/p/default/runs');
  await screen.findByTitle(COMPARED);
  const staleTime = (id: string, read: string) => {
    const query: Query | undefined = queryClient
      .getQueryCache()
      .find({ queryKey: ['run', id, 'outcomes', read], exact: true });
    return query?.observers[0]?.options.staleTime;
  };
  await waitFor(() => expect(staleTime(PENDING, 'pending')).toBe(30_000));
  expect(staleTime(COMPARED, 'final')).toBe(Number.POSITIVE_INFINITY);
});

it('reads a run’s line again on its page once its end has arrived, and marks it', async () => {
  // Abandoned when the list read its line; vantage push then delivers its end.
  let ended = false;
  const endedRun = {
    ...run(PENDING, '2026-09-27T11:00:00Z', {
      state: 'branch',
      baseline: { id: COMPARED, started_at: '2026-09-27T10:00:00Z', branch: 'main' },
      counts: {
        new_failure: 1,
        still_failing: 1,
        fixed: 1,
        new_test: 0,
        removed: 0,
        not_reached: 0,
      },
    }),
    interrupt_reason: null,
    project: 'default',
  };
  stubServer((_m, path) => {
    if (path === '/projects/default/runs') {
      const items = ended ? [endedRun, ...RUNS.slice(1)] : RUNS;
      return json(200, { items, has_more: false, next_cursor: null, metadata_horizon: null });
    }
    if (path === `/runs/${PENDING}`) return json(200, endedRun);
    if (path === `/runs/${PENDING}/outcomes`) {
      return json(200, ended ? { outcomes: '.F.F', changes: 'fs-n' } : OUTCOMES[PENDING]);
    }
    if (path === `/runs/${PENDING}/metadata`) return json(200, { items: [], files: [] });
    if (path === `/runs/${PENDING}/results`) return json(200, { items: [], has_more: false });
    const outcomes = /^\/runs\/([0-9a-f]{32})\/outcomes$/.exec(path);
    if (outcomes?.[1]) return json(200, OUTCOMES[outcomes[1]]);
    return undefined;
  });
  const { router } = renderAt('/p/default/runs');
  await screen.findByTitle(PENDING);
  await waitFor(() => expect(rowOf(PENDING).querySelectorAll('.dl-m--failed')).toHaveLength(2));
  ended = true;
  await act(() => router.navigate(`/runs/${PENDING}`));
  const line = await screen.findByRole('img', { name: /; 1 new failure, 1 fixed$/ });
  await waitFor(() => expect(line.querySelector('.dl-ring--passed')).not.toBeNull());
  const lifted = [...line.querySelectorAll('.dl-m--failed')].filter(
    (r) => Number(r.getAttribute('y')) < 0,
  );
  expect(lifted).toHaveLength(1);
});
