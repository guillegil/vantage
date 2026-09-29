// Server state, through TanStack Query alone. Each hook names one read of
// /api/v1; pages never call the client directly.
import {
  MutationCache,
  type Query,
  QueryCache,
  QueryClient,
  useInfiniteQuery,
  useQueries,
  useQuery,
} from '@tanstack/react-query';
import { ApiError, api, isApiError, type Schemas, unwrap } from './client';

export type Session = Schemas['SessionResponse'];
export type Project = Schemas['ProjectResponse'];
export type RunListItem = Schemas['RunListItem'];
export type RunDetail = Schemas['RunDetailResponse'];
export type ResultItem = Schemas['ResultListItem'];
export type RunMetadata = Schemas['RunMetadataResponse'];
export type ResultDetail = Schemas['ResultDetailResponse'];
export type HistoryEntry = Schemas['HistoryEntry'];
export type RunOutcomes = Schemas['RunOutcomesResponse'];
export type ChangeWord = Schemas['ChangeItem']['change'];
export type OutcomeWord = ResultItem['outcome'];

export const RUNS_PAGE = 50;
export const RESULTS_PAGE = 100;
export const HISTORY_PAGE = 50;
// A result page's side column draws this many of the test's latest runs, as
// the design's result page does.
export const RECENT_HISTORY = 24;
const LIST_STALE = 30_000;

export const NOT_PASSING: OutcomeWord[] = ['failed', 'error', 'xpassed'];

// A refusal is final; no answer, or a server error, is tried once more.
export function retry(failures: number, error: unknown): boolean {
  if (isApiError(error) && error.status >= 400 && error.status < 500) return false;
  return failures < 1;
}

export interface Unauthorized {
  // A 401 on who is asking: the browser is not signed in.
  onSignedOut: () => void;
  // A 401 on anything else: a session that was working has ended.
  onSessionEnded: () => void;
}

const SESSION_KEY = ['session'] as const;

function onError(handlers: Unauthorized, error: unknown, key: readonly unknown[] | undefined) {
  if (!isApiError(error, 401)) return;
  if (key && key[0] === SESSION_KEY[0]) handlers.onSignedOut();
  else handlers.onSessionEnded();
}

export function makeQueryClient(handlers: Unauthorized): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query: Query<unknown, unknown, unknown>) =>
        onError(handlers, error, query.queryKey),
    }),
    mutationCache: new MutationCache({
      onError: (error, _v, _c, mutation) => onError(handlers, error, mutation.options.mutationKey),
    }),
    defaultOptions: {
      queries: { retry, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

// A run is final once it has an exit status: nothing it holds changes after
// that, its comparison included, so what was read of it stays true. An
// abandoned run is not final, since vantage push may still deliver its end.
export function isFinal(run: { exit_status: number | null } | undefined): boolean {
  return run !== undefined && run.exit_status !== null;
}

export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: ({ signal }) => unwrap(api.GET('/session', { signal })),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useProjects(enabled = true) {
  return useQuery({
    queryKey: ['projects'],
    queryFn: ({ signal }) => unwrap(api.GET('/projects', { signal })),
    staleTime: LIST_STALE,
    enabled,
  });
}

export function useRuns(project: string) {
  return useInfiniteQuery({
    queryKey: ['runs', project],
    queryFn: ({ pageParam, signal }) =>
      unwrap(
        api.GET('/projects/{project}/runs', {
          params: {
            path: { project },
            query: pageParam ? { limit: RUNS_PAGE, cursor: pageParam } : { limit: RUNS_PAGE },
          },
          signal,
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (last.has_more ? last.next_cursor : null),
    staleTime: LIST_STALE,
  });
}

export function useRun(runId: string, enabled = true) {
  return useQuery({
    enabled,
    queryKey: ['run', runId],
    queryFn: ({ signal }) =>
      unwrap(
        api.GET('/runs/{run_id}', {
          params: { path: { run_id: runId } },
          signal,
        }),
      ),
    staleTime: (query) => (isFinal(query.state.data) ? Number.POSITIVE_INFINITY : LIST_STALE),
  });
}

function outcomesQuery(runId: string, finished: boolean) {
  return {
    queryKey: ['run', runId, 'outcomes'],
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      unwrap(
        api.GET('/runs/{run_id}/outcomes', {
          params: { path: { run_id: runId } },
          signal,
        }),
      ),
    staleTime: finished ? Number.POSITIVE_INFINITY : LIST_STALE,
  };
}

export function useRunOutcomes(runId: string, finished: boolean, enabled = true) {
  return useQuery({ ...outcomesQuery(runId, finished), enabled });
}

// The outcomes of every listed run in view, and their changes, by run id.
export function useOutcomesOf(runs: { id: string; finished: boolean }[]) {
  return useQueries({
    queries: runs.map((r) => outcomesQuery(r.id, r.finished)),
    combine: (answers) => {
      const byId = new Map<string, RunOutcomes>();
      answers.forEach((a, i) => {
        const run = runs[i];
        if (run && a.data) byId.set(run.id, a.data);
      });
      return byId;
    },
  });
}

function resultsQuery(runId: string, outcomes: OutcomeWord[] | null, finished: boolean) {
  return {
    queryKey: ['run', runId, 'results', outcomes ?? 'all'],
    queryFn: ({ pageParam, signal }: { pageParam: number; signal: AbortSignal }) =>
      unwrap(
        api.GET('/runs/{run_id}/results', {
          params: {
            path: { run_id: runId },
            query: outcomes
              ? { limit: RESULTS_PAGE, offset: pageParam, outcome: outcomes }
              : { limit: RESULTS_PAGE, offset: pageParam },
          },
          signal,
        }),
      ),
    initialPageParam: 0,
    getNextPageParam: (last: Schemas['ResultsResponse'], pages: Schemas['ResultsResponse'][]) =>
      last.has_more ? pages.reduce((n, page) => n + page.items.length, 0) : null,
    staleTime: finished ? Number.POSITIVE_INFINITY : LIST_STALE,
  };
}

export function useNotPassing(runId: string, finished: boolean, enabled = true) {
  return useInfiniteQuery({
    ...resultsQuery(runId, NOT_PASSING, finished),
    enabled,
  });
}

export function useResults(runId: string, finished: boolean, enabled = true) {
  return useInfiniteQuery({ ...resultsQuery(runId, null, finished), enabled });
}

export function useRunMetadata(runId: string, finished: boolean, enabled = true) {
  return useQuery({
    queryKey: ['run', runId, 'metadata'],
    queryFn: ({ signal }) =>
      unwrap(
        api.GET('/runs/{run_id}/metadata', {
          params: { path: { run_id: runId } },
          signal,
        }),
      ),
    staleTime: finished ? Number.POSITIVE_INFINITY : LIST_STALE,
    enabled,
  });
}

// One result of a run, in full. A final run's results never change.
export function useResult(runId: string, nodeId: string, finished: boolean) {
  return useQuery({
    queryKey: ['run', runId, 'result', nodeId],
    queryFn: ({ signal }) =>
      unwrap(
        api.GET('/runs/{run_id}/result', {
          params: { path: { run_id: runId }, query: { node_id: nodeId } },
          signal,
        }),
      ),
    staleTime: finished ? Number.POSITIVE_INFINITY : LIST_STALE,
  });
}

// A test's latest runs in a project, newest first: a result page's side column.
export function useRecentHistory(project: string, nodeId: string, enabled = true) {
  return useQuery({
    queryKey: ['history', project, nodeId, 'recent'],
    queryFn: ({ signal }) =>
      unwrap(
        api.GET('/projects/{project}/tests/history', {
          params: { path: { project }, query: { node_id: nodeId, limit: RECENT_HISTORY } },
          signal,
        }),
      ),
    staleTime: LIST_STALE,
    enabled,
  });
}

// A test's whole history in a project, newest first, a page at a time by cursor.
export function useHistory(project: string, nodeId: string) {
  return useInfiniteQuery({
    queryKey: ['history', project, nodeId],
    queryFn: ({ pageParam, signal }) =>
      unwrap(
        api.GET('/projects/{project}/tests/history', {
          params: {
            path: { project },
            query: pageParam
              ? { node_id: nodeId, limit: HISTORY_PAGE, cursor: pageParam }
              : { node_id: nodeId, limit: HISTORY_PAGE },
          },
          signal,
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (last.has_more ? last.next_cursor : null),
    staleTime: LIST_STALE,
  });
}

export async function signIn(name: string, password: string): Promise<Session> {
  return unwrap(
    api.POST('/session', {
      body: { name, password },
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}

export async function signOut(): Promise<void> {
  await unwrap(api.DELETE('/session'));
}

export { ApiError };
