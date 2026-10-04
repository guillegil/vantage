import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router';
import {
  baselineNote,
  CHANGE,
  changeRow,
  dotlineLabel,
  historyHref,
  historyStrip,
  isCompared,
  lineResults,
  metaItems,
  queueTotals,
  resultChange,
  resultDetailRow,
  resultHref,
  resultQueueRow,
  runHead,
  runHref,
  runLabel,
  runsHref,
  type TriageRow,
} from '../adapt';
import { isApiError } from '../api/client';
import {
  isFinal,
  type RunDetail,
  type Session,
  useChanges,
  useFailures,
  useQueueResults,
  useRecentHistory,
  useResult,
  useRun,
  useRunMetadata,
  useRunOutcomes,
} from '../api/queries';
import { useGo } from '../app/go';
import { SAME_PAGE, usePage } from '../app/page';
import {
  BaselineNote,
  Button,
  type Change,
  ChangeBadge,
  ChangeQueue,
  type ChangeQueueDetailContext,
  Command,
  CommitRef,
  Dotline,
  EmptyState,
  fmtSeconds,
  HistoryGrid,
  Icon,
  MetaList,
  NodeId,
  Notice,
  type Outcome,
  OutcomeBadge,
  plural,
  type QueueGroup,
  type QueueResult,
  RerunButton,
  RunStatus,
  SummaryLine,
  Time,
  UserChip,
  visible,
} from '../ds';
import { EvidenceColumn } from './EvidenceColumn';
import { FailureNotice } from './Failure';
import { NotFoundPage } from './NotFound';

const RUN_ID = /^[0-9a-f]{32}$/;

// A node id no row holds, for a selection that names none: no store holds U+0000.
const NONE = '\u0000';

// The groups the queue can open on, in its order: removed tests are closed until opened.
const LEAD: QueueGroup[] = ['new-failure', 'still-failing', 'fixed', 'new-test', 'not-reached'];

// How long the address waits between rewrites. Browsers refuse a page that replaces its address
// too often (Safari past 100 times in 10 s, Firefox past 200), and a key held down moves the
// selection up to 25 times a second.
const ADDRESS_GAP = 350;
// How long it waits after a browser refused a rewrite all the same.
const ADDRESS_RETRY = 2000;

function Loading({ children = 'Loading…' }: { children?: string }) {
  return (
    <p className="dl-caption" role="status">
      {children}
    </p>
  );
}

function isFailing(outcome: Outcome | null | undefined): boolean {
  return outcome === 'failed' || outcome === 'error';
}

// What the page needs of a list read a page at a time.
interface PagedRead {
  data?: { pages: unknown[] } | undefined;
  error: unknown;
  isError: boolean;
  isFetching: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  isFetchNextPageError: boolean;
  fetchNextPage: () => Promise<unknown>;
  refetch: () => Promise<unknown>;
}

// The next page of a list. A list never read, or whose reading again failed -- not its next page --
// is read again from its first; so is one wholly read when more is asked of it, as a run still
// recording has more than when its list was read.
function readMore(read: PagedRead): void {
  if (!read.data || (read.isError && !read.isFetchNextPageError)) void read.refetch();
  else if (read.hasNextPage) {
    if (!read.isFetchingNextPage) void read.fetchNextPage();
  } else if (!read.isFetching) void read.refetch();
}

// Reads every page of a list, one after another, until there are no more or one fails.
function useEveryPage(read: PagedRead | null): void {
  useEffect(() => {
    if (read?.data && read.hasNextPage && !read.isFetching && !read.isError)
      void read.fetchNextPage();
  }, [read]);
}

// The queue's rows, a group at a time from the server: a compared run's changed tests in queue
// order, or the failures of one with no baseline; and the whole run, for the whole-run view.
function useQueue(detail: RunDetail) {
  const runId = detail.id;
  const final = isFinal(detail);
  const comparison = detail.comparison;
  const compared = isCompared(comparison);
  const counts = compared ? comparison.counts : null;
  const baselineId = compared ? comparison.baseline.id : '';
  const failing = detail.counts.failed + detail.counts.error;
  // Removed tests are closed until opened, so they are read once opened, or once the address
  // names a test the run has no result for; the whole run, once its view is.
  const [removedOpened, setRemovedOpened] = useState(false);
  const openRemoved = useCallback(() => setRemovedOpened(true), []);
  const [wholeOpened, setWholeOpened] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | 'all'>('all');
  const newFailure = useChanges(runId, 'new_failure', final, !!counts?.new_failure);
  const stillFailing = useChanges(runId, 'still_failing', final, !!counts?.still_failing);
  const fixed = useChanges(runId, 'fixed', final, !!counts?.fixed);
  const newTest = useChanges(runId, 'new_test', final, !!counts?.new_test);
  const removed = useChanges(runId, 'removed', final, !!counts?.removed && removedOpened);
  const notReached = useChanges(runId, 'not_reached', final, !!counts?.not_reached);
  const failures = useFailures(runId, final, !compared && failing > 0);
  const whole = useQueueResults(runId, outcome === 'all' ? null : outcome, final, wholeOpened);

  const groups: Partial<Record<QueueGroup, PagedRead>> = compared
    ? {
        'new-failure': newFailure,
        'still-failing': stillFailing,
        fixed,
        'new-test': newTest,
        removed,
        'not-reached': notReached,
      }
    : { failures };

  const nfData = newFailure.data;
  const sfData = stillFailing.data;
  const fxData = fixed.data;
  const ntData = newTest.data;
  const rmData = removed.data;
  const nrData = notReached.data;
  const failuresData = failures.data;
  const rows = useMemo<TriageRow[]>(() => {
    if (compared)
      return [nfData, sfData, fxData, ntData, rmData, nrData].flatMap((data) =>
        (data?.pages ?? []).flatMap((page) =>
          page.items.map((item) => changeRow(item, runId, baselineId)),
        ),
      );
    return (failuresData?.pages ?? []).flatMap((page) =>
      page.items.map((item) => resultQueueRow(item, runId, { position: null, compared: false })),
    );
  }, [compared, runId, baselineId, nfData, sfData, fxData, ntData, rmData, nrData, failuresData]);
  const totals = queueTotals(detail, compared ? 0 : rows.length);
  // The queue opens on its first group's first test, the first new failure if any, once that
  // group's first page is read: never on a later group's test that happened to arrive sooner.
  // Until then it selects nothing.
  const lead = (compared ? LEAD : (['failures'] as QueueGroup[])).find((g) => !!totals[g]);
  const first = lead
    ? ((compared ? rows.find((r) => r.change === lead) : rows[0])?.nodeid ?? NONE)
    : undefined;
  // An unfiltered page of the whole run is in stored order, so each row's index is its position.
  const wholeRows = useMemo<TriageRow[]>(
    () =>
      (whole.data?.pages ?? [])
        .flatMap((page) => page.items)
        .map((item, i) =>
          resultQueueRow(item, runId, { position: outcome === 'all' ? i : null, compared }),
        ),
    [whole.data, runId, outcome, compared],
  );

  const loading: (QueueGroup | 'all')[] = [];
  for (const [group, read] of Object.entries(groups) as [QueueGroup, PagedRead][]) {
    if (read.isFetching) loading.push(group);
  }
  if (whole.isFetching) loading.push('all');

  function onMore(group: QueueGroup | 'all') {
    if (group === 'all') {
      if (!wholeOpened) setWholeOpened(true);
      else readMore(whole);
      return;
    }
    if (group === 'removed' && !removedOpened) {
      setRemovedOpened(true);
      return;
    }
    const read = groups[group];
    if (read) readMore(read);
  }

  // The rerun of the new failures names every one, so all their pages are read.
  useEveryPage(newFailure);
  const newFailures = {
    count: counts?.new_failure ?? 0,
    ids: (newFailure.data?.pages ?? []).flatMap((page) => page.items.map((item) => item.node_id)),
    complete: !!newFailure.data && !newFailure.hasNextPage,
    failed: newFailure.isError,
  };

  // Reads that failed for a reason the page does not explain itself; a 401 keeps what is shown.
  const failed = [...Object.values(groups), whole].filter(
    (read): read is PagedRead => !!read && read.isError && !isApiError(read.error, 401),
  );

  return {
    compared,
    groups,
    rows,
    totals,
    first,
    openRemoved,
    wholeRows,
    loading,
    onMore,
    outcome,
    setOutcome,
    newFailures,
    failed,
  };
}

type Queue = ReturnType<typeof useQueue>;

// The test the address names, when no loaded row holds it: /result says whether it changed. A
// changed test's group is read page by page until it holds the test; one that did not change is
// shown as a result with no change, outside every group. One the run has no result for may be one
// its baseline holds, removed or not reached, so those groups are read whole, the removed tests
// opened, before the page says the run lacks it.
function useAddressed(detail: RunDetail, queue: Queue, wanted: string | null) {
  const byNode = useMemo(() => {
    const map = new Map<string, TriageRow>();
    // A changed row says more than the same test's whole-run row, so it is the one kept.
    for (const r of queue.wholeRows) map.set(r.nodeid, r);
    for (const r of queue.rows) map.set(r.nodeid, r);
    return map;
  }, [queue.wholeRows, queue.rows]);
  const held = wanted !== null && byNode.has(wanted);
  const lookup = useResult(detail.id, wanted ?? '', isFinal(detail), wanted !== null && !held);
  const found = !held && wanted !== null ? lookup.data : undefined;
  const lacks = !held && wanted !== null && isApiError(lookup.error, 404);
  const seekRemoved = lacks && !!queue.totals.removed;
  const seekNotReached = lacks && !!queue.totals['not-reached'];
  const { openRemoved } = queue;
  useEffect(() => {
    if (seekRemoved) openRemoved();
  }, [seekRemoved, openRemoved]);
  const removed = queue.groups.removed ?? null;
  const notReached = queue.groups['not-reached'] ?? null;
  useEveryPage(seekRemoved ? removed : null);
  useEveryPage(seekNotReached ? notReached : null);
  const whole = (read: PagedRead | null) => !!read?.data && !read.hasNextPage;
  const missing =
    lacks && (!seekRemoved || whole(removed)) && (!seekNotReached || whole(notReached));
  let group: QueueGroup | null = null;
  if (found) {
    if (queue.compared) group = found.change ? CHANGE[found.change] : null;
    else group = isFailing(found.outcome) ? 'failures' : null;
  }
  useEveryPage(group ? (queue.groups[group] ?? null) : null);
  // What the page knows of the addressed test, and what the queue is handed for it until a group
  // holds it: never a row of any group, so no group's order or count is disturbed by it.
  let entry: TriageRow | undefined;
  let extra: QueueResult | undefined;
  if (wanted !== null && !held && !missing) {
    if (found) {
      entry = resultDetailRow(found, detail.id);
      const inNoGroup = queue.compared || !isFailing(found.outcome);
      extra = { ...entry, change: null, outcome: inNoGroup ? entry.outcome : null };
    } else {
      extra = { nodeid: wanted, outcome: null, change: null };
    }
  }
  return { byNode, entry, extra, missing };
}

// One test of the run, as the queue's detail shows it: its id, its outcome and change, its latest
// runs, its rerun, and for a failure its evidence.
function TriageDetail({
  detail,
  nodeId,
  entry,
  context,
  leave,
}: {
  detail: RunDetail;
  nodeId: string;
  entry: TriageRow | undefined;
  context: ChangeQueueDetailContext;
  leave: (path: string) => void;
}) {
  // /result is read for a test not loaded yet, for a change its row does not say, and for a
  // failure's evidence; a test the run lacks has no result in it.
  const wanted =
    !entry || (entry.outcome !== null && (!entry.changeKnown || isFailing(entry.outcome)));
  const result = useResult(detail.id, nodeId, isFinal(detail), wanted);
  const outcome = entry ? entry.outcome : (result.data?.outcome ?? null);
  // Its change from its row where the row says it, else from /result: never guessed from the
  // line's change characters, which hold nothing for a test the run lacks.
  const change = entry?.changeKnown ? entry : result.data ? resultChange(result.data) : null;
  // The change line already says a new failure and a streak, so the strip leaves its note out.
  const noted = change?.change === 'new-failure' || change?.change === 'still-failing';
  const readFailed = result.isError && !(isApiError(result.error, 401) && result.data);
  let evidence: ReactNode = null;
  if (!entry || isFailing(outcome)) {
    // A test with no row and no result here is one the page is still looking for among those the
    // run lacks.
    if (readFailed && !entry && isApiError(result.error, 404))
      evidence = <Loading>Loading the test…</Loading>;
    else if (readFailed)
      evidence = isApiError(result.error, 401) ? null : (
        <FailureNotice
          error={result.error}
          retry={() => result.refetch()}
          busy={result.isFetching}
        />
      );
    else if (!result.data) evidence = <Loading>Loading the result…</Loading>;
    else if (isFailing(result.data.outcome))
      evidence = <EvidenceColumn result={result.data} level={3} />;
  }
  return (
    <>
      <div className="dl-triage__head">
        <h2 className="dl-triage__title">
          <NodeId value={nodeId} size="lg" />
        </h2>
        <div className="dl-pagehead__sub">
          {outcome ? <OutcomeBadge outcome={outcome} /> : null}
          {change?.change ? (
            <ChangeBadge
              change={change.change}
              was={change.was}
              streak={change.streak}
              baseline={context.baseline ?? undefined}
              outcome={outcome}
              detail
            />
          ) : null}
        </div>
        <TestHistory project={detail.project} nodeId={nodeId} note={!noted} leave={leave} />
      </div>
      {context.command ? (
        <div className="dl-triage__cmd">
          <Command text={context.command} copy={false} />
          <RerunButton nodeids={[nodeId]} command={context.command} variant="primary" keyHint="c">
            Copy rerun command
          </RerunButton>
          <a className="dl-link" href={resultHref(detail.id, nodeId)}>
            Open result page
          </a>
        </div>
      ) : null}
      {evidence}
    </>
  );
}

// The test across its project's latest runs, so since when is read without a click. Each run
// opens its result; the strip is a slider, so the history page, which lists each as a link, is
// linked beside it.
function TestHistory({
  project,
  nodeId,
  note,
  leave,
}: {
  project: string;
  nodeId: string;
  note: boolean;
  leave: (path: string) => void;
}) {
  const recent = useRecentHistory(project, nodeId);
  if (recent.isError && !(isApiError(recent.error, 401) && recent.data)) {
    return isApiError(recent.error, 401) ? null : (
      <FailureNotice error={recent.error} retry={() => recent.refetch()} busy={recent.isFetching} />
    );
  }
  if (!recent.data) return <Loading>Loading its history…</Loading>;
  if (!recent.data.items.length)
    return <p className="dl-caption">{`No run of ${project} has reported this test.`}</p>;
  const strip = historyStrip(recent.data.items, nodeId);
  return (
    <>
      <HistoryGrid
        stacked
        runs={strip.runs}
        rows={[{ nodeid: nodeId, outcomes: strip.outcomes, changes: strip.changes, note }]}
        onOpen={leave}
      />
      <p className="dl-caption">
        <a className="dl-link" href={historyHref(project, nodeId)}>
          Full history
        </a>
      </p>
    </>
  );
}

// The selected test: the page's own state, which moves at once, and the address's node_id, which
// follows it, so a shared link or Back opens on it. Each rewrite replaces the address, so Back
// leaves the run rather than walking every move, at most every ADDRESS_GAP ms; a navigation that
// changes the address all the same, as Back between two of the run's addresses, moves the
// selection.
function useSelection() {
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const addressed = search.get('node_id');
  const [chosen, setChosen] = useState(addressed);
  const current = useRef(search);
  // The node id last written to the address, or found there.
  const wrote = useRef(addressed);
  // A choice still to be written, its timer, and when the address was last written.
  const want = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const last = useRef(Number.NEGATIVE_INFINITY);
  const live = useRef(true);

  useEffect(() => {
    current.current = search;
  });
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      clearTimeout(timer.current);
    };
  }, []);
  useEffect(() => {
    if (addressed === wrote.current) return;
    wrote.current = addressed;
    want.current = null;
    clearTimeout(timer.current);
    timer.current = undefined;
    setChosen(addressed);
  }, [addressed]);

  const write = useCallback(() => {
    const node = want.current;
    clearTimeout(timer.current);
    timer.current = undefined;
    if (node === null || !live.current) return;
    want.current = null;
    wrote.current = node;
    last.current = Date.now();
    const next = new URLSearchParams(current.current);
    next.set('node_id', node);
    // A browser past its limit refuses the rewrite, and the navigation's promise says so: the
    // selection is the page's own, so it stays where it moved, and the address is written later.
    Promise.resolve(navigate(`?${next}`, { replace: true, state: SAME_PAGE })).catch(() => {
      if (!live.current || want.current !== null) return;
      want.current = node;
      timer.current = setTimeout(write, ADDRESS_RETRY);
    });
  }, [navigate]);

  // Applied at once rather than as a transition, so the next key -- c straight after j -- acts on
  // the test it moved to.
  const choose = useCallback(
    (node: string) => {
      flushSync(() => setChosen(node));
      want.current = node;
      if (timer.current !== undefined) return;
      const wait = last.current + ADDRESS_GAP - Date.now();
      if (wait <= 0) write();
      else timer.current = setTimeout(write, wait);
    },
    [write],
  );

  // Writes a choice still waiting, before the page is left, so Back comes to it.
  const flush = useCallback(() => {
    if (want.current !== null) write();
  }, [write]);

  return { chosen, choose, flush };
}

// The queue's words when the run has no baseline yet; the line above it says why.
function pendingNote(detail: RunDetail): string | undefined {
  if (detail.comparison.state !== 'pending') return undefined;
  return detail.presentation === 'running'
    ? 'The failures so far. Changes show once the session ends.'
    : 'The failures it recorded. Changes show only if its end arrives.';
}

function RunBody({ detail }: { detail: RunDetail }) {
  const head = runHead(detail);
  const finished = isFinal(detail);
  const go = useGo();
  const selection = useSelection();
  const outcomes = useRunOutcomes(detail.id, finished);
  const metadata = useRunMetadata(detail.id, finished);
  const queue = useQueue(detail);
  const wanted = selection.chosen;
  const addressed = useAddressed(detail, queue, wanted);
  const c = detail.counts;
  const total = c.passed + c.failed + c.error + c.skipped + c.xfailed + c.xpassed;
  const comparison = detail.comparison;
  const baseline = isCompared(comparison) ? comparison.baseline : null;

  // With nothing in the address, the queue opens on its first test.
  const selected = wanted !== null && !addressed.missing ? wanted : queue.first;
  const selectedEntry =
    selected === undefined
      ? undefined
      : (addressed.byNode.get(selected) ?? (selected === wanted ? addressed.entry : undefined));
  // The selected test's place in the run, from its row, or from /result where the row's list did
  // not say; a test the run lacks has none.
  const placeUnknown =
    selectedEntry !== undefined &&
    selectedEntry.position === null &&
    selectedEntry.outcome !== null;
  const place = useResult(detail.id, selected ?? '', finished, placeUnknown);
  const cursor = selectedEntry?.position ?? (placeUnknown ? place.data?.position : undefined);

  // Leaving the page writes the selection still waiting first, so Back comes to it.
  const { flush, choose } = selection;
  const leave = useCallback(
    (path: string) => {
      flush();
      go(path);
    },
    [flush, go],
  );
  useEffect(() => {
    // Before the client follows a link, as Open result page.
    document.addEventListener('click', flush, true);
    return () => document.removeEventListener('click', flush, true);
  }, [flush]);
  function onSelect(r: { nodeid: string }) {
    choose(r.nodeid);
  }
  // A test the run lacks has no result here: its result in the baseline is the one to open. Any
  // other is opened here, a test whose row has not arrived yet included.
  function onOpen(r: { nodeid: string; change?: Change | null }) {
    const lacked = r.change === 'removed' || r.change === 'not-reached';
    leave(resultHref(lacked && baseline ? baseline.id : detail.id, r.nodeid));
  }

  const list = outcomes.data ? lineResults(outcomes.data.outcomes, outcomes.data.changes) : null;
  const summary = <SummaryLine counts={c} seconds={head.seconds} running={head.running} />;
  let line: ReactNode;
  if (head.running && total === 0) {
    line = (
      <>
        {summary}
        <p className="dl-caption">Results arrive when the session finishes.</p>
      </>
    );
  } else if (list) {
    // pytest's own closing line sits under the run's line, at the line's measure.
    line = (
      <Dotline
        results={list}
        width="auto"
        running={head.running}
        cursor={cursor}
        under={summary}
        label={dotlineLabel(c, head.running, comparison.counts)}
      />
    );
  } else if (outcomes.isError && !isApiError(outcomes.error, 401)) {
    line = (
      <>
        {summary}
        <FailureNotice error={outcomes.error} retry={() => outcomes.refetch()} />
      </>
    );
  } else {
    line = (
      <>
        {summary}
        <Loading>Loading results…</Loading>
      </>
    );
  }

  const nf = queue.newFailures;
  let rerun: ReactNode = null;
  if (nf.count > 0) {
    const label = `Copy rerun of ${plural(nf.count, 'new failure', 'new failures')}`;
    if (nf.complete) {
      rerun = (
        <RerunButton nodeids={nf.ids} fileName="new-failures.txt">
          {label}
        </RerunButton>
      );
    } else if (!nf.failed) {
      // Until every new failure is read, the button it becomes, busy: past 20 it is a download.
      rerun =
        nf.count > 20 ? (
          <Button size="sm" icon="download" busy>
            {`Download ${plural(nf.count, 'node id', 'node ids')}`}
          </Button>
        ) : (
          <Button size="sm" icon="terminal" busy>
            {label}
          </Button>
        );
    }
  }

  const failedRead = queue.failed[0];
  const results: QueueResult[] = addressed.extra ? [addressed.extra, ...queue.rows] : queue.rows;
  // A run still recording may list more of its results than its detail counted when it was read.
  const listed = queue.wholeRows.length;
  const allTotal = queue.outcome === 'all' ? Math.max(total, listed) : total;
  const allCounts =
    queue.outcome === 'all' ? c : { ...c, [queue.outcome]: Math.max(c[queue.outcome], listed) };
  const metaCount = metadata.data?.items.length;
  return (
    <>
      <div className="dl-runhead__line">{line}</div>
      <BaselineNote {...baselineNote(detail)}>{rerun}</BaselineNote>
      {addressed.missing && wanted !== null ? (
        <Notice
          tone="warning"
          title={
            <>
              This run has no result for <bdi className="dl-recorded">{visible(wanted)}</bdi>
            </>
          }
        >
          It was not collected in this run, or the address is mistyped.
        </Notice>
      ) : null}
      {failedRead ? (
        <FailureNotice
          error={failedRead.error}
          retry={() => {
            for (const read of queue.failed) readMore(read);
          }}
          busy={queue.failed.some((read) => read.isFetching)}
        />
      ) : null}
      {head.running && total === 0 ? null : (
        <ChangeQueue
          results={results}
          baseline={baseline ? { label: runLabel(baseline.id), href: runHref(baseline.id) } : null}
          baselineNote={pendingNote(detail)}
          interrupted={!!comparison.counts?.not_reached}
          total={allTotal}
          running={head.running}
          shortcuts
          totals={queue.totals}
          onMore={queue.onMore}
          loading={queue.loading}
          allResults={queue.wholeRows}
          outcomeCounts={allCounts}
          outcome={queue.outcome}
          onOutcome={queue.setOutcome}
          selected={selected}
          onSelect={onSelect}
          onOpen={onOpen}
          renderDetail={(r, context) => (
            <TriageDetail
              detail={detail}
              nodeId={r.nodeid}
              entry={
                addressed.byNode.get(r.nodeid) ??
                (r.nodeid === wanted ? addressed.entry : undefined)
              }
              context={context}
              leave={leave}
            />
          )}
        />
      )}
      <details className="dl-disclosure">
        <summary>
          <Icon name="chevron-right" size={14} />
          {metaCount === undefined ? 'Metadata' : `Metadata (${plural(metaCount, 'key', 'keys')})`}
        </summary>
        <div className="dl-disclosure__body">
          {metadata.isError && !(isApiError(metadata.error, 401) && metadata.data) ? (
            isApiError(metadata.error, 401) ? null : (
              <FailureNotice error={metadata.error} retry={() => metadata.refetch()} />
            )
          ) : metadata.data ? (
            <MetaList items={metaItems(metadata.data)} emptyText="This run reported no metadata" />
          ) : (
            <Loading />
          )}
        </div>
      </details>
    </>
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
    body = <Loading />;
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
