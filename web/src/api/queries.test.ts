import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { json } from '../testing/server';
import { isFinal, makeQueryClient, useRun } from './queries';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('takes a run as final once it has an exit status, whatever its presentation says', () => {
  expect(isFinal({ exit_status: 0 })).toBe(true);
  expect(isFinal({ exit_status: 2 })).toBe(true);
  // Running, or abandoned: vantage push may still deliver its end.
  expect(isFinal({ exit_status: null })).toBe(false);
  expect(isFinal(undefined)).toBe(false);
});

const ID = '0123abcd0123abcd0123abcd0123abcd';
const BASE = '1adf29af1adf29af1adf29af1adf29af';

const ABANDONED = {
  id: ID,
  started_at: '2026-09-27T09:00:00Z',
  finished_at: null,
  exit_status: null,
  interrupted: false,
  interrupt_reason: null,
  presentation: 'abandoned',
  vcs: null,
  recorded_by: 'alice',
  project: 'default',
  counts: { passed: 1, failed: 1, error: 0, skipped: 0, xfailed: 0, xpassed: 0 },
  comparison: { state: 'pending', baseline: null, counts: null },
};

// vantage push delivered its end at last, and with it its comparison.
const PUSHED = {
  ...ABANDONED,
  finished_at: '2026-09-27T09:00:05Z',
  exit_status: 1,
  presentation: 'finished',
  comparison: {
    state: 'project',
    baseline: { id: BASE, started_at: '2026-09-27T08:00:00Z', branch: null },
    counts: {
      new_failure: 1,
      still_failing: 0,
      fixed: 0,
      new_test: 0,
      removed: 0,
      not_reached: 0,
    },
  },
};

it('reads an abandoned run again once stale, and a run with its end never again', async () => {
  // Only the clock is faked, so what was read grows stale without waiting.
  vi.useFakeTimers({ toFake: ['Date'] });
  let answer: object = ABANDONED;
  const fetch = vi.fn(async () => json(200, answer));
  vi.stubGlobal('fetch', fetch);
  const client = makeQueryClient({ onSignedOut: () => undefined, onSessionEnded: () => undefined });
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children);
  const read = () => renderHook(() => useRun(ID), { wrapper });

  const first = read();
  await waitFor(() => expect(first.result.current.data?.presentation).toBe('abandoned'));
  expect(fetch).toHaveBeenCalledTimes(1);
  // Fresh for 30 s: another reader is answered from what was read.
  read();
  expect(fetch).toHaveBeenCalledTimes(1);

  answer = PUSHED;
  vi.setSystemTime(Date.now() + 31_000);
  const later = read();
  await waitFor(() => expect(later.result.current.data?.comparison.state).toBe('project'));
  expect(fetch).toHaveBeenCalledTimes(2);

  // Final now: nothing it holds changes, however long ago it was read.
  vi.setSystemTime(Date.now() + 86_400_000);
  const much = read();
  expect(much.result.current.data?.exit_status).toBe(1);
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(fetch).toHaveBeenCalledTimes(2);
});
