import type { ReactNode } from 'react';
import { useOutletContext, useParams, useSearchParams } from 'react-router';
import {
  FAILURE_TEXT_NOTICE,
  historyHref,
  historyStrip,
  metaItems,
  resultEvidence,
  resultPhases,
  runHead,
  runHref,
  runLabel,
  runsHref,
} from '../adapt';
import { isApiError } from '../api/client';
import {
  type ResultDetail,
  type RunDetail,
  type Session,
  useRecentHistory,
  useResult,
  useRun,
  useRunMetadata,
} from '../api/queries';
import { useGo } from '../app/go';
import { usePage } from '../app/page';
import {
  CommitRef,
  DurationSpark,
  EmptyState,
  Evidence,
  fmtSeconds,
  HistoryGrid,
  Icon,
  MetaList,
  NodeId,
  Notice,
  OutcomeBadge,
  Panel,
  PhaseTimeline,
  RunStatus,
  UserChip,
  visible,
} from '../ds';
import { FailureNotice } from './Failure';
import { NotFoundPage } from './NotFound';

const RUN_ID = /^[0-9a-f]{32}$/;

function Loading({ children = 'Loading…' }: { children?: string }) {
  return (
    <p className="dl-caption" role="status">
      {children}
    </p>
  );
}

// Everything the result holds that a person reads: its failure, its reasons
// and its output, each where pytest would print it.
function EvidenceColumn({ result }: { result: ResultDetail }) {
  const ev = resultEvidence(result);
  if (!ev.recorded) {
    return (
      <p className="dl-caption">
        Failure text was not recorded; run with{' '}
        <code className="dl-mono">--vantage-failure-text</code>
      </p>
    );
  }
  return (
    <>
      {ev.blocks.map((b) => (
        <Evidence
          key={b.key}
          title={b.title}
          text={b.text}
          kind={b.kind}
          meta={b.meta}
          truncated={b.truncated}
          notice={FAILURE_TEXT_NOTICE}
          level={2}
        />
      ))}
      {ev.dropped.map((title) => (
        <Notice key={title} tone="warning" title={`${title} was not kept`}>
          This session’s failure text passed its 512 KiB limit, which is spent on failed and errored
          results first.
        </Notice>
      ))}
      {ev.silent ? <p className="dl-caption">Nothing was printed to stdout or stderr.</p> : null}
    </>
  );
}

// The test's latest runs in the run's project, and a way to all of them.
function HistoryPanel({ project, nodeId }: { project: string; nodeId: string }) {
  const recent = useRecentHistory(project, nodeId);
  const go = useGo();
  let body: ReactNode;
  if (recent.isError) {
    body = isApiError(recent.error, 401) ? null : (
      <FailureNotice error={recent.error} retry={() => recent.refetch()} busy={recent.isFetching} />
    );
  } else if (!recent.data) {
    body = <Loading />;
  } else if (!recent.data.items.length) {
    body = <p className="dl-caption">{`No run of ${project} has reported this test.`}</p>;
  } else {
    const strip = historyStrip(recent.data.items, nodeId);
    body = (
      <div className="dl-stack dl-stack--tight">
        <HistoryGrid
          stacked
          runs={strip.runs}
          rows={[{ nodeid: nodeId, outcomes: strip.outcomes }]}
          onOpen={go}
        />
        <DurationSpark values={strip.durations} width={150} />
      </div>
    );
  }
  return (
    <Panel
      title="History"
      actions={
        <a className="dl-link" href={historyHref(project, nodeId)}>
          Full history
        </a>
      }
    >
      {body}
    </Panel>
  );
}

function PhasesPanel({ result }: { result: ResultDetail }) {
  const phases = resultPhases(result);
  return (
    <Panel title="Phases">
      <div className="dl-stack dl-stack--tight">
        {phases.length ? (
          <PhaseTimeline phases={phases} />
        ) : (
          <p className="dl-caption">No phase durations were recorded.</p>
        )}
        {result.worker_id ? (
          <p className="dl-caption">
            Ran on xdist worker <code className="dl-mono">{visible(result.worker_id)}</code>
          </p>
        ) : null}
      </div>
    </Panel>
  );
}

function SessionPanel({ runId, finished }: { runId: string; finished: boolean }) {
  const metadata = useRunMetadata(runId, finished);
  return (
    <Panel title="Session">
      {metadata.isError ? (
        isApiError(metadata.error, 401) ? null : (
          <FailureNotice error={metadata.error} retry={() => metadata.refetch()} />
        )
      ) : metadata.data ? (
        <MetaList items={metaItems(metadata.data)} emptyText="This run reported no metadata" />
      ) : (
        <Loading />
      )}
    </Panel>
  );
}

function ResultBody({
  detail,
  nodeId,
  result,
}: {
  detail: RunDetail;
  nodeId: string;
  result: ReturnType<typeof useResult>;
}) {
  const finished = detail.presentation !== 'running';
  let main: ReactNode;
  if (result.isError) {
    main = isApiError(result.error, 401) ? null : (
      <FailureNotice error={result.error} retry={() => result.refetch()} busy={result.isFetching} />
    );
  } else if (!result.data) {
    main = <Loading>Loading the result…</Loading>;
  } else {
    main = <EvidenceColumn result={result.data} />;
  }
  return (
    <div className="dl-split">
      <div className="dl-stack">{main}</div>
      <div className="dl-stack">
        <HistoryPanel project={detail.project} nodeId={nodeId} />
        {result.data ? <PhasesPanel result={result.data} /> : null}
        <SessionPanel runId={detail.id} finished={finished} />
      </div>
    </div>
  );
}

function ResultView({ runId, nodeId }: { runId: string; nodeId: string }) {
  const session = useOutletContext<Session>();
  const run = useRun(runId);
  const detail = run.data;
  const result = useResult(
    runId,
    nodeId,
    detail !== undefined && detail.presentation !== 'running',
  );
  const label = runLabel(runId);
  const heading = usePage(
    detail
      ? `${nodeId} · ${label} · ${detail.project} · vantage`
      : `${nodeId} · ${label} · vantage`,
  );
  const title = (
    <h1 className="dl-pagehead__node app-heading" ref={heading} tabIndex={-1}>
      <NodeId value={nodeId} size="lg" />
    </h1>
  );
  const outcome = result.data ? <OutcomeBadge outcome={result.data.outcome} /> : null;

  // Who may read the run is answered before what the run holds.
  if (run.isError) {
    let body: ReactNode = null;
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
    return (
      <div className="dl-stack dl-stack--loose">
        {title}
        {body}
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="dl-stack">
        {title}
        <Loading />
      </div>
    );
  }
  const head = runHead(detail);
  const crumbs = (
    <nav className="dl-crumbs" aria-label="Breadcrumb">
      <a className="dl-link" href={runsHref(detail.project)}>
        Runs
      </a>
      <Icon name="chevron-right" size={14} />
      <a className="dl-link dl-mono" href={runHref(runId)} title={runId}>
        {label}
      </a>
      <Icon name="chevron-right" size={14} />
      <span aria-current="page">result</span>
    </nav>
  );
  if (isApiError(result.error, 404, 'unknown_result')) {
    return (
      <div className="dl-stack dl-stack--loose">
        <div className="dl-stack dl-stack--tight">
          {crumbs}
          {title}
        </div>
        <EmptyState
          title={`Run ${label} has no result for this test`}
          action={
            <a className="dl-link" href={runHref(runId)}>
              {`Go to run ${label}`}
            </a>
          }
        >
          The run did not report this node id: the test was not collected in it, or the address is
          mistyped.
        </EmptyState>
      </div>
    );
  }
  return (
    <div className="dl-stack">
      <div className="dl-stack dl-stack--tight">
        {crumbs}
        <div className="dl-pagehead__row">
          {title}
          {outcome}
        </div>
        <div className="dl-pagehead__sub">
          <RunStatus {...head.status} />
          {head.reason ? <span>{head.reason}</span> : null}
          <CommitRef {...head.commit} />
          {head.recordedBy ? (
            <UserChip
              name={head.recordedBy}
              you={head.recordedBy === session.user?.name}
              size="sm"
            />
          ) : (
            <span>recorded without a token</span>
          )}
          {result.data?.duration != null ? (
            <span className="dl-num">{fmtSeconds(result.data.duration)}</span>
          ) : null}
        </div>
      </div>
      <ResultBody detail={detail} nodeId={nodeId} result={result} />
    </div>
  );
}

export function ResultPage() {
  const { runId = '' } = useParams();
  const [search] = useSearchParams();
  const nodeId = search.get('node_id');
  // Checked before any request: an address that names no run or no test is simply not a page.
  if (!RUN_ID.test(runId) || !nodeId) return <NotFoundPage />;
  return <ResultView key={`${runId} ${nodeId}`} runId={runId} nodeId={nodeId} />;
}
