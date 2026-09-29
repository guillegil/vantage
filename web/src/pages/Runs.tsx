import { type ReactNode, type RefObject, useEffect, useMemo, useRef, useState } from 'react';
import { useOutletContext, useParams } from 'react-router';
import { runItem, runLabel } from '../adapt';
import { isApiError } from '../api/client';
import {
  RUNS_PAGE,
  type RunListItem,
  type Session,
  useOutcomesOf,
  useProjects,
  useRuns,
} from '../api/queries';
import { writeLastProject } from '../app/lastProject';
import { useNow } from '../app/now';
import { usePage } from '../app/page';
import {
  EmptyState,
  fmtRelative,
  Notice,
  Pager,
  Panel,
  RecordingGuide,
  RoleBadge,
  RunList,
  SummaryLine,
} from '../ds';
import { FailureNotice } from './Failure';

function seconds(run: RunListItem): number | undefined {
  if (!run.finished_at) return undefined;
  return (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000;
}

// The ids of the rows in view, so each row's outcomes are read only once it
// can be seen. Rows are found by their link, whose title is the run's id.
function useRowsInView(container: RefObject<HTMLDivElement | null>, count: number) {
  const [inView, setInView] = useState<ReadonlySet<string>>(() => new Set());
  // biome-ignore lint/correctness/useExhaustiveDependencies: rows are observed again whenever more are listed.
  useEffect(() => {
    const root = container.current;
    if (!root) return undefined;
    const rows = Array.from(root.querySelectorAll<HTMLElement>('.dl-run'));
    const idOf = (row: Element) => row.querySelector('.dl-run__id')?.getAttribute('title') ?? '';
    if (typeof IntersectionObserver === 'undefined') {
      setInView(new Set(rows.map(idOf)));
      return undefined;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const seen = entries.filter((e) => e.isIntersecting).map((e) => idOf(e.target));
        if (!seen.length) return;
        setInView((prev) => {
          const next = new Set(prev);
          for (const id of seen) next.add(id);
          return next.size === prev.size ? prev : next;
        });
      },
      { rootMargin: '200px 0px' },
    );
    for (const row of rows) observer.observe(row);
    return () => observer.disconnect();
  }, [count]);
  return inView;
}

export function RunsPage() {
  const { project = 'default' } = useParams();
  const session = useOutletContext<Session>();
  const heading = usePage(`${project} · runs · vantage`);
  const now = useNow();
  const runs = useRuns(project);
  const projects = useProjects();
  const list = useRef<HTMLDivElement | null>(null);
  const items = useMemo(() => runs.data?.pages.flatMap((page) => page.items) ?? [], [runs.data]);
  const inView = useRowsInView(list, items.length);
  const wanted = useMemo(
    () =>
      items
        .filter((r) => inView.has(r.id))
        .map((r) => ({ id: r.id, finished: r.presentation !== 'running' })),
    [items, inView],
  );
  const outcomes = useOutcomesOf(wanted);
  const found = runs.isSuccess;
  useEffect(() => {
    if (found) writeLastProject(project);
  }, [found, project]);

  const role = projects.data?.items.find((p) => p.name === project)?.role ?? null;
  const known = !isApiError(runs.error, 404);
  const head = (
    <div className="dl-pagehead">
      <div className="dl-pagehead__main">
        <h1 className="dl-pagehead__title app-heading" ref={heading} tabIndex={-1}>
          {project}
        </h1>
        {known ? (
          <div className="dl-pagehead__sub">
            <RoleBadge role={role} />
            {project === 'default' ? (
              <span>Runs that name no project join default</span>
            ) : (
              <span>
                Runs join when a session names it:{' '}
                <code className="dl-mono">{`--vantage-project ${project}`}</code>
              </span>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
  const openNotice = session.open ? (
    <Notice>
      This server has no users: anyone who can reach it can read and record runs.{' '}
      <code className="dl-mono">vantage user add NAME --admin</code> closes it.
    </Notice>
  ) : null;

  if (runs.isError) {
    let body: ReactNode;
    if (isApiError(runs.error, 404)) {
      body = (
        <EmptyState
          title={`No project named ${project} on this server`}
          action={
            <a className="dl-link" href="/">
              Go to your projects
            </a>
          }
        >
          Projects are never renamed or deleted, so the name may be mistyped. An admin of this
          server creates projects.
        </EmptyState>
      );
    } else if (isApiError(runs.error, 403, 'not_a_member')) {
      body = (
        <Notice tone="warning" title={`You are not a member of ${project}`}>
          Its owners or an admin of this server can add you.
        </Notice>
      );
    } else if (isApiError(runs.error, 401)) {
      body = null;
    } else {
      body = (
        <FailureNotice error={runs.error} retry={() => runs.refetch()} busy={runs.isFetching} />
      );
    }
    return (
      <div className="dl-stack dl-stack--loose">
        {head}
        {body}
      </div>
    );
  }

  const latest = items.find((r) => r.presentation === 'finished');
  const sessionUser = session.user?.name ?? null;
  const shown = items.map((item) =>
    runItem(item, { sessionUser, outcomes: outcomes.get(item.id) }),
  );
  const origin = window.location.origin;

  return (
    <div className="dl-stack dl-stack--loose">
      {head}
      {openNotice}
      {latest ? (
        <div className="dl-stack dl-stack--tight">
          <span className="dl-caption">
            {`Latest finished run, ${runLabel(latest.id)}, ${fmtRelative(latest.finished_at ?? latest.started_at, now)}`}
          </span>
          <SummaryLine counts={latest.counts} seconds={seconds(latest)} />
        </div>
      ) : null}
      {runs.isPending ? (
        <Panel flush>
          <p className="dl-pager" role="status">
            Loading runs…
          </p>
        </Panel>
      ) : (
        <Panel flush>
          <div ref={list}>
            <RunList
              runs={shown}
              now={now}
              label={`Runs of ${project}`}
              empty={
                <EmptyState title="No runs yet">
                  <RecordingGuide
                    project={project}
                    server={origin}
                    open={session.open}
                    canRecord={role !== 'viewer'}
                    title={false}
                  />
                </EmptyState>
              }
            />
          </div>
          {items.length ? (
            <Pager
              shown={items.length}
              hasMore={runs.hasNextPage}
              pageSize={RUNS_PAGE}
              noun="runs"
              loading={runs.isFetchingNextPage}
              onMore={() => runs.fetchNextPage()}
            />
          ) : null}
        </Panel>
      )}
    </div>
  );
}
