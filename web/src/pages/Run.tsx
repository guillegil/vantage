import { type ReactNode, useMemo } from 'react';
import { useOutletContext, useParams } from 'react-router';
import {
  baselineLine,
  dotlineLabel,
  lineResults,
  metaItems,
  type ResultRow,
  resultRow,
  runHead,
  runsHref,
} from '../adapt';
import { isApiError } from '../api/client';
import {
  isFinal,
  RESULTS_PAGE,
  type RunDetail,
  type Session,
  useNotPassing,
  useResults,
  useRun,
  useRunMetadata,
  useRunOutcomes,
} from '../api/queries';
import { usePage } from '../app/page';
import {
  CommitRef,
  DataTable,
  type DataTableColumn,
  Dotline,
  EmptyState,
  fmtSeconds,
  Icon,
  MetaList,
  NodeId,
  Notice,
  OutcomeMark,
  Pager,
  Panel,
  RunStatus,
  SummaryLine,
  Time,
  UserChip,
  visible,
} from '../ds';
import { FailureNotice } from './Failure';
import { NotFoundPage } from './NotFound';

const RUN_ID = /^[0-9a-f]{32}$/;
const ORDER = 'in the order pytest reported them';

const COLUMNS: DataTableColumn<ResultRow>[] = [
  {
    key: 'outcome',
    label: 'Outcome',
    width: 88,
    render: (r) => <OutcomeMark outcome={r.outcome} />,
  },
  { key: 'nodeId', label: 'Test', render: (r) => <NodeId value={r.nodeId} href={r.href} /> },
  {
    key: 'message',
    label: 'Why',
    render: (r) =>
      r.message ? (
        <span className="dl-mono">{visible(r.message)}</span>
      ) : (
        <span className="dl-meta__none">—</span>
      ),
  },
  {
    key: 'seconds',
    label: 'Duration',
    align: 'num',
    width: 96,
    render: (r) => fmtSeconds(r.seconds),
  },
];

function ResultsPanel({
  runId,
  title,
  caption,
  pages,
  hasMore,
  loading,
  onMore,
  error,
  retry,
}: {
  runId: string;
  title: string;
  caption: string;
  pages: { items: Parameters<typeof resultRow>[0][] }[] | undefined;
  hasMore: boolean;
  loading: boolean;
  onMore: () => void;
  error: unknown;
  retry: () => void;
}) {
  const rows = useMemo(
    () => (pages ?? []).flatMap((page) => page.items).map((item, i) => resultRow(item, i, runId)),
    [pages, runId],
  );
  return (
    <Panel title={title} flush>
      {error && !isApiError(error, 401) ? (
        <FailureNotice error={error} retry={retry} busy={loading} />
      ) : pages === undefined ? (
        <p className="dl-pager" role="status">
          Loading results…
        </p>
      ) : (
        <>
          <DataTable columns={COLUMNS} rows={rows} rowKey="key" caption={caption} dense />
          <Pager
            shown={rows.length}
            hasMore={hasMore}
            pageSize={RESULTS_PAGE}
            noun="results"
            order={ORDER}
            loading={loading}
            onMore={onMore}
          />
        </>
      )}
    </Panel>
  );
}

// What the run was compared with, its label linking to that run, or why it was not.
function BaselineSentence({ detail }: { detail: RunDetail }) {
  const line = baselineLine(detail);
  return (
    <div className="dl-runhead__base">
      <span>
        {line.lead}
        {line.baseline ? (
          <a className="dl-link dl-mono" href={line.baseline.href} title={line.baseline.id}>
            {line.baseline.label}
          </a>
        ) : null}
        {line.tail}
      </span>
    </div>
  );
}

function RunBody({ detail }: { detail: RunDetail }) {
  const head = runHead(detail);
  const finished = isFinal(detail);
  const outcomes = useRunOutcomes(detail.id, finished);
  const c = detail.counts;
  const notPassingCount = c.failed + c.error + c.xpassed;
  const notPassing = useNotPassing(detail.id, finished, notPassingCount > 0);
  const results = useResults(detail.id, finished);
  const metadata = useRunMetadata(detail.id, finished);
  const total = c.passed + c.failed + c.error + c.skipped + c.xfailed + c.xpassed;
  const list = outcomes.data ? lineResults(outcomes.data.outcomes, outcomes.data.changes) : null;
  const summary = <SummaryLine counts={c} seconds={head.seconds} running={head.running} />;
  return (
    <div className="dl-split">
      <div className="dl-stack">
        <div className="dl-runhead__line">
          {/* pytest's own closing line, then every result in the order it reported them. A
              short run's line is only a few marks wide, too narrow to set the summary under. */}
          {summary}
          {head.running && total === 0 ? (
            <p className="dl-caption">Results arrive when the session finishes.</p>
          ) : list ? (
            <Dotline
              results={list}
              width="auto"
              running={head.running}
              label={dotlineLabel(c, head.running, detail.comparison.counts)}
            />
          ) : outcomes.isError && !isApiError(outcomes.error, 401) ? (
            <FailureNotice error={outcomes.error} retry={() => outcomes.refetch()} />
          ) : (
            <p className="dl-caption" role="status">
              Loading results…
            </p>
          )}
        </div>
        <BaselineSentence detail={detail} />
        {notPassingCount > 0 ? (
          <ResultsPanel
            runId={detail.id}
            title="Not passing"
            caption="Results that failed, errored or passed unexpectedly"
            pages={notPassing.data?.pages}
            hasMore={notPassing.hasNextPage}
            loading={notPassing.isFetchingNextPage || notPassing.isFetching}
            onMore={() => notPassing.fetchNextPage()}
            error={notPassing.error}
            retry={() => notPassing.refetch()}
          />
        ) : null}
        <ResultsPanel
          runId={detail.id}
          title="All results"
          caption={`Every result, ${ORDER}`}
          pages={results.data?.pages}
          hasMore={results.hasNextPage}
          loading={results.isFetchingNextPage || results.isFetching}
          onMore={() => results.fetchNextPage()}
          error={results.error}
          retry={() => results.refetch()}
        />
      </div>
      <Panel title="Metadata">
        {metadata.isError && !isApiError(metadata.error, 401) ? (
          <FailureNotice error={metadata.error} retry={() => metadata.refetch()} />
        ) : metadata.data ? (
          <MetaList items={metaItems(metadata.data)} emptyText="This run reported no metadata" />
        ) : (
          <p className="dl-caption" role="status">
            Loading…
          </p>
        )}
      </Panel>
    </div>
  );
}

function RunView({ runId }: { runId: string }) {
  const session = useOutletContext<Session>();
  const run = useRun(runId);
  const detail = run.data;
  const label = runId.slice(0, 8);
  const heading = usePage(detail ? `${label} · ${detail.project} · vantage` : `${label} · vantage`);
  const title = (
    <h1 className="dl-pagehead__title app-heading" ref={heading} tabIndex={-1}>
      Run{' '}
      <span className="dl-mono" title={runId}>
        {label}
      </span>
    </h1>
  );
  let crumbs: ReactNode = null;
  let sub: ReactNode = null;
  let body: ReactNode = null;
  let loose = false;
  // A session that ended keeps what is shown.
  if (run.isError && !(isApiError(run.error, 401) && detail)) {
    loose = true;
    if (isApiError(run.error, 404)) {
      body = (
        <EmptyState
          title="No run with this id on this server"
          action={
            <a className="dl-link" href="/">
              Go to the runs
            </a>
          }
        >
          This server never deletes a run, so the address may be mistyped or from another server.
        </EmptyState>
      );
    } else if (isApiError(run.error, 403, 'not_a_member')) {
      body = (
        <Notice tone="warning" title="You are not a member of this run’s project">
          Its owners or an admin of this server can add you.
        </Notice>
      );
    } else if (!isApiError(run.error, 401)) {
      body = <FailureNotice error={run.error} retry={() => run.refetch()} busy={run.isFetching} />;
    }
  } else if (!detail) {
    body = (
      <p className="dl-caption" role="status">
        Loading…
      </p>
    );
  } else {
    const head = runHead(detail);
    crumbs = (
      <nav className="dl-crumbs" aria-label="Breadcrumb">
        <a className="dl-link" href={runsHref(detail.project)}>
          Runs
        </a>
        <Icon name="chevron-right" size={14} />
        <span className="dl-mono" aria-current="page">
          {head.label}
        </span>
      </nav>
    );
    sub = (
      <div className="dl-pagehead__sub">
        <RunStatus {...head.status} />
        {head.reason ? <span>{visible(head.reason)}</span> : null}
        <CommitRef {...head.commit} />
        {head.recordedBy ? (
          <UserChip name={head.recordedBy} you={head.recordedBy === session.user?.name} size="sm" />
        ) : (
          <span>recorded without a token</span>
        )}
        <span>
          started <Time value={head.startedAt} mode="absolute" />
        </span>
        {head.finishedAt ? (
          <span>
            finished <Time value={head.finishedAt} mode="absolute" />
          </span>
        ) : null}
        {head.seconds !== undefined ? (
          <span className="dl-num">{fmtSeconds(head.seconds)}</span>
        ) : null}
      </div>
    );
    body = <RunBody detail={detail} />;
  }
  // One head in every state: the heading focused after a navigation, while the run loads,
  // is the element that stays once it has, so focus is not dropped to the page's body.
  return (
    <div className={loose ? 'dl-stack dl-stack--loose' : 'dl-stack'}>
      {crumbs}
      <div className="dl-pagehead">
        <div className="dl-pagehead__main">
          {title}
          {sub}
        </div>
      </div>
      {body}
    </div>
  );
}

export function RunPage() {
  const { runId = '' } = useParams();
  // Checked before any request: an id the API could never hold is simply not a page.
  if (!RUN_ID.test(runId)) return <NotFoundPage />;
  return <RunView key={runId} runId={runId} />;
}
