import { type ReactNode, useMemo } from 'react';
import { useParams, useSearchParams } from 'react-router';
import { type HistoryRow, historyRow, historyStrip, runsHref } from '../adapt';
import { isApiError } from '../api/client';
import { HISTORY_PAGE, useHistory } from '../api/queries';
import { useGo } from '../app/go';
import { useNow } from '../app/now';
import { usePage } from '../app/page';
import {
  CommitRef,
  DataTable,
  type DataTableColumn,
  DurationSpark,
  EmptyState,
  fmtSeconds,
  HistoryGrid,
  Icon,
  NodeId,
  Notice,
  OutcomeMark,
  Pager,
  Panel,
  Time,
  visibleText,
} from '../ds';
import { FailureNotice } from './Failure';
import { NotFoundPage } from './NotFound';

function columns(now: Date): DataTableColumn<HistoryRow>[] {
  return [
    {
      key: 'outcome',
      label: 'Outcome',
      width: 88,
      render: (r) => <OutcomeMark outcome={r.outcome} />,
    },
    {
      key: 'run',
      label: 'Run',
      width: 112,
      render: (r) => (
        <a className="dl-link dl-mono" href={r.href} title={r.runId}>
          {r.label}
        </a>
      ),
    },
    { key: 'commit', label: 'Commit', render: (r) => <CommitRef {...r.commit} /> },
    {
      key: 'startedAt',
      label: 'Started',
      width: 128,
      render: (r) => <Time value={r.startedAt} now={now} />,
    },
    {
      key: 'seconds',
      label: 'Duration',
      align: 'num',
      width: 96,
      render: (r) => fmtSeconds(r.seconds),
    },
  ];
}

function HistoryView({ project, nodeId }: { project: string; nodeId: string }) {
  // A string sink: a bidi control in the node id would reorder the rest of the tab's title.
  const heading = usePage(`${visibleText(nodeId)} · history · ${project} · vantage`);
  const now = useNow();
  const go = useGo();
  const history = useHistory(project, nodeId);
  const entries = useMemo(
    () => history.data?.pages.flatMap((page) => page.items) ?? [],
    [history.data],
  );
  const rows = useMemo(() => entries.map((e) => historyRow(e, nodeId)), [entries, nodeId]);
  const cols = useMemo(() => columns(now), [now]);
  const head = (
    <div className="dl-stack dl-stack--tight">
      <nav className="dl-crumbs" aria-label="Breadcrumb">
        <a className="dl-link" href={runsHref(project)}>
          Runs
        </a>
        <Icon name="chevron-right" size={14} />
        <span aria-current="page">history</span>
      </nav>
      <h1 className="dl-pagehead__node app-heading" ref={heading} tabIndex={-1}>
        <NodeId value={nodeId} size="lg" />
      </h1>
      <div className="dl-pagehead__sub">
        <span>{`This test’s results in ${project}, newest first`}</span>
      </div>
    </div>
  );

  // A refusal, or no answer, shows in place of the list, never beside a stale copy; a
  // session that ended keeps what is shown.
  if (history.isError && !(isApiError(history.error, 401) && history.data)) {
    let body: ReactNode = null;
    if (isApiError(history.error, 404)) {
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
    } else if (isApiError(history.error, 403, 'not_a_member')) {
      body = (
        <Notice tone="warning" title={`You are not a member of ${project}`}>
          Its owners or an admin of this server can add you.
        </Notice>
      );
    } else if (!isApiError(history.error, 401)) {
      body = (
        <FailureNotice
          error={history.error}
          retry={() => history.refetch()}
          busy={history.isFetching}
        />
      );
    }
    return (
      <div className="dl-stack dl-stack--loose">
        {head}
        {body}
      </div>
    );
  }

  if (!history.data) {
    return (
      <div className="dl-stack dl-stack--loose">
        {head}
        <Panel flush>
          <p className="dl-pager" role="status">
            Loading results…
          </p>
        </Panel>
      </div>
    );
  }

  if (!entries.length) {
    return (
      <div className="dl-stack dl-stack--loose">
        {head}
        <EmptyState
          title={`No run of ${project} has reported this test`}
          action={
            <a className="dl-link" href={runsHref(project)}>
              {`Go to ${project}’s runs`}
            </a>
          }
        >
          The node id may be mistyped, or the test may belong to another project.
        </EmptyState>
      </div>
    );
  }

  const strip = historyStrip(entries, nodeId);
  return (
    <div className="dl-stack dl-stack--loose">
      {head}
      <Panel>
        <div className="dl-stack dl-stack--tight">
          {/* Stacked: the heading already names the test the label column would repeat. */}
          <HistoryGrid
            stacked
            runs={strip.runs}
            rows={[{ nodeid: nodeId, outcomes: strip.outcomes, changes: strip.changes }]}
            onOpen={go}
          />
          <DurationSpark values={strip.durations} width={150} />
        </div>
      </Panel>
      <Panel flush>
        <DataTable
          columns={cols}
          rows={rows}
          rowKey="key"
          caption="This test’s results, newest first"
          dense
        />
        <Pager
          shown={entries.length}
          hasMore={history.hasNextPage}
          pageSize={HISTORY_PAGE}
          noun="results"
          loading={history.isFetchingNextPage}
          onMore={() => history.fetchNextPage()}
        />
      </Panel>
    </div>
  );
}

export function HistoryPage() {
  const { project = 'default' } = useParams();
  const [search] = useSearchParams();
  const nodeId = search.get('node_id');
  // Checked before any request: an address that names no test is not a page.
  if (!nodeId) return <NotFoundPage />;
  return <HistoryView key={`${project} ${nodeId}`} project={project} nodeId={nodeId} />;
}
