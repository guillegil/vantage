// The only code that knows both the API's field names and the design
// system's props. Pure functions, so each mapping is tested on its own.

import type {
  ChangeWord,
  HistoryEntry,
  Project,
  ResultDetail,
  ResultItem,
  RunDetail,
  RunListItem,
  RunMetadata,
  RunOutcomes,
} from '../api/queries';
import type {
  Change,
  CommitRefProps,
  HistoryGridProps,
  MetaListProps,
  Outcome,
  OutcomeCounts,
  PhaseTimelineProps,
  ProjectRef,
  ResultLike,
  RunItem,
  RunState,
  RunStatusProps,
} from '../ds';
import { describeCounts, fmtCount, isOutcome, plural, visibleText } from '../ds';

// Lists and headings print a run by the first 8 characters of its id.
export function runLabel(id: string): string {
  return id.slice(0, 8);
}

export function runHref(id: string): string {
  return `/runs/${id}`;
}

export function runsHref(project: string): string {
  return `/p/${encodeURIComponent(project)}/runs`;
}

// A node id travels in the query string, as it does in the API, since it holds `/`.
export function resultHref(runId: string, nodeId: string): string {
  return `/runs/${runId}/result?node_id=${encodeURIComponent(nodeId)}`;
}

export function historyHref(project: string, nodeId: string): string {
  return `/p/${encodeURIComponent(project)}/tests/history?node_id=${encodeURIComponent(nodeId)}`;
}

const CHARS: Record<string, Outcome> = {
  '.': 'passed',
  F: 'failed',
  E: 'error',
  s: 'skipped',
  x: 'xfailed',
  X: 'xpassed',
};

// pytest's characters, as GET /runs/{id}/outcomes answers them, back to outcome words.
export function outcomesToResults(outcomes: string): Outcome[] {
  const out: Outcome[] = [];
  for (const ch of outcomes) {
    const o = CHARS[ch];
    if (o) out.push(o);
  }
  return out;
}

// The API's change words, snake_case like every API code, as the design system spells them.
export const CHANGE: Record<ChangeWord, Change> = {
  new_failure: 'new-failure',
  still_failing: 'still-failing',
  fixed: 'fixed',
  new_test: 'new-test',
  removed: 'removed',
  not_reached: 'not-reached',
};

// GET /runs/{id}/outcomes' change characters, aligned with its outcomes; `-` is no change.
const CHANGE_CHARS: Record<string, Change> = {
  n: 'new-failure',
  s: 'still-failing',
  f: 'fixed',
  t: 'new-test',
};

// A run's line: each outcome, with its change where the run was compared and the result
// changed. Without changes -- a run pending or with nothing to compare with -- the plain
// outcomes.
export function lineResults(outcomes: string, changes: string | null): ResultLike[] {
  const out: ResultLike[] = [];
  let i = 0;
  for (const ch of outcomes) {
    const outcome = CHARS[ch];
    const change = changes === null ? undefined : CHANGE_CHARS[changes[i] ?? '-'];
    i += 1;
    if (!outcome) continue;
    out.push(change ? { outcome, change } : outcome);
  }
  return out;
}

// Whether a run was compared: then, and only then, the API gives its baseline and counts.
function compared(
  comparison: RunListItem['comparison'],
): comparison is RunListItem['comparison'] & {
  baseline: NonNullable<RunListItem['comparison']['baseline']>;
  counts: NonNullable<RunListItem['comparison']['counts']>;
} {
  return (
    (comparison.state === 'branch' || comparison.state === 'project') &&
    comparison.baseline !== null &&
    comparison.counts !== null
  );
}

function seconds(startedAt: string, finishedAt: string | null): number | undefined {
  if (!finishedAt) return undefined;
  const s = (Date.parse(finishedAt) - Date.parse(startedAt)) / 1000;
  return Number.isFinite(s) && s >= 0 ? s : undefined;
}

export function runItem(
  item: RunListItem,
  { sessionUser, outcomes }: { sessionUser: string | null; outcomes?: RunOutcomes | undefined },
): RunItem {
  const run: RunItem = {
    id: item.id,
    label: runLabel(item.id),
    href: runHref(item.id),
    state: item.presentation as RunState,
    counts: { ...item.counts },
    visibility: 'project',
    startedAt: item.started_at,
  };
  if (item.exit_status != null) run.exitStatus = item.exit_status;
  const s = seconds(item.started_at, item.finished_at);
  if (s !== undefined) run.seconds = s;
  if (item.vcs) {
    if (item.vcs.branch) run.branch = item.vcs.branch;
    if (item.vcs.commit) run.sha = item.vcs.commit;
    if (item.vcs.dirty) run.dirty = true;
  }
  if (item.recorded_by != null) {
    run.by = { name: item.recorded_by, you: item.recorded_by === sessionUser };
  }
  if (outcomes !== undefined) run.results = lineResults(outcomes.outcomes, outcomes.changes);
  const comparison = item.comparison;
  if (compared(comparison)) {
    run.changes = {
      newFailures: comparison.counts.new_failure,
      fixed: comparison.counts.fixed,
      baseline: runLabel(comparison.baseline.id),
    };
  }
  return run;
}

// What the run page's line says it draws: stored order is the order pytest reported results,
// and the changes its marks show, as the line itself counts them.
export function dotlineLabel(
  counts: OutcomeCounts,
  running: boolean,
  changes: { new_failure: number; fixed: number } | null = null,
): string {
  const changed: string[] = [];
  if (changes?.new_failure)
    changed.push(plural(changes.new_failure, 'new failure', 'new failures'));
  if (changes?.fixed) changed.push(`${fmtCount(changes.fixed)} fixed`);
  return `Results in the order pytest reported them: ${describeCounts(counts)}${
    changed.length ? `; ${changed.join(', ')}` : ''
  }${running ? ', still running' : ''}`;
}

// How much earlier `from` started than `to`, in the words a run list's times use: "42 min",
// "3 h", "2 d". Two start times, since a baseline is chosen by when it started.
export function earlier(from: string, to: string): string {
  const s = Math.floor((Date.parse(to) - Date.parse(from)) / 1000);
  if (!Number.isFinite(s) || s < 1) return 'under 1 s';
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h`;
  return `${fmtCount(Math.floor(h / 24))} d`;
}

// The run page's line saying what the run was compared with: the words before the
// baseline's label, the baseline (null when there is none), and the words after it.
export interface BaselineLine {
  lead: string;
  baseline: { id: string; label: string; href: string } | null;
  tail: string;
}

export function baselineLine(detail: RunDetail): BaselineLine {
  const comparison = detail.comparison;
  if (!compared(comparison)) {
    let lead: string;
    if (comparison.state === 'none') lead = 'Nothing to compare with yet.';
    else if (detail.presentation === 'running')
      lead = 'Compared with its baseline once the session ends.';
    else lead = 'Not compared: no end was recorded.';
    return { lead, baseline: null, tail: '' };
  }
  const base = comparison.baseline;
  let lead = 'Compared with ';
  if (comparison.state === 'project') {
    // Why it fell back to the project's latest complete run, from what this run recorded.
    // A recorded name is plain text here, so a character that reorders text is written out.
    const vcs = detail.vcs;
    let why: string;
    if (vcs?.branch) why = `No earlier complete run on ${visibleText(vcs.branch)}`;
    else if (vcs?.commit) why = `No branch recorded (detached HEAD at ${vcs.commit.slice(0, 7)})`;
    else if (vcs) why = 'No branch recorded';
    else why = 'Recorded outside a git repository';
    lead = `${why}; compared with `;
  }
  const on = base.branch ? ` on ${visibleText(base.branch)}` : '';
  return {
    lead,
    baseline: { id: base.id, label: runLabel(base.id), href: runHref(base.id) },
    tail: `${on}, ${earlier(base.started_at, detail.started_at)} earlier`,
  };
}

export function projectRef(project: Project): ProjectRef {
  return { name: project.name, role: project.role };
}

export interface RunHead {
  label: string;
  status: RunStatusProps;
  reason: string | null;
  commit: CommitRefProps;
  recordedBy: string | null;
  startedAt: string;
  finishedAt: string | null;
  seconds: number | undefined;
  running: boolean;
}

export function runHead(detail: RunDetail): RunHead {
  const status: RunStatusProps = {
    state: detail.presentation as RunState,
    explain: true,
  };
  if (detail.exit_status != null) status.exitStatus = detail.exit_status;
  const commit: CommitRefProps = {};
  if (detail.vcs) {
    if (detail.vcs.branch) commit.branch = detail.vcs.branch;
    if (detail.vcs.commit) commit.sha = detail.vcs.commit;
    if (detail.vcs.dirty) commit.dirty = true;
    if (detail.vcs.commit_subject) commit.subject = detail.vcs.commit_subject;
  }
  return {
    label: runLabel(detail.id),
    status,
    reason: detail.presentation === 'interrupted' ? detail.interrupt_reason : null,
    commit,
    recordedBy: detail.recorded_by,
    startedAt: detail.started_at,
    finishedAt: detail.finished_at,
    seconds: seconds(detail.started_at, detail.finished_at),
    running: detail.presentation === 'running',
  };
}

// Why a declared key has no value, in a few words.
const MISSING: Record<string, string> = {
  absent: 'not in its file',
  not_scalar: 'not a single value',
  value_too_large: 'value too large',
  source_unavailable: 'file not read',
};

export function metaItems(metadata: RunMetadata): MetaListProps['items'] {
  return metadata.items.map((item) =>
    item.status === 'captured'
      ? { key: item.key, value: item.value }
      : {
          key: item.key,
          value: null,
          source: MISSING[item.status] ?? item.status,
        },
  );
}

export const NOT_RECORDED = 'Failure text was not recorded; run with --vantage-failure-text';

export interface ResultRow {
  key: string;
  nodeId: string;
  href: string;
  outcome: Outcome;
  message: string | null;
  seconds: number | null;
}

function firstLine(text: string | null | undefined): string | null {
  if (!text) return null;
  const line = text.split('\n').find((l) => l.trim() !== '');
  return line ?? null;
}

export function resultRow(item: ResultItem, index: number, runId: string): ResultRow {
  const failure = item.failure;
  let message: string | null = null;
  if (item.outcome === 'failed' || item.outcome === 'error') {
    message = firstLine(failure?.failure_message) ?? NOT_RECORDED;
  } else if (item.outcome === 'skipped') {
    message = firstLine(failure?.skip_reason);
  } else if (item.outcome === 'xfailed' || item.outcome === 'xpassed') {
    message = firstLine(failure?.xfail_reason);
  }
  return {
    key: `${index}:${item.node_id}`,
    nodeId: item.node_id,
    href: resultHref(runId, item.node_id),
    outcome: item.outcome,
    message,
    seconds: item.duration,
  };
}

// What failure text may hold, said wherever it is shown.
export const FAILURE_TEXT_NOTICE =
  'May contain any value a test printed or asserted, credentials included.';

// The plugin and the server cut each text field to 64 KiB, and flag the cut.
export const KEPT = 'Only the first 64 KiB was kept.';

export interface EvidenceBlock {
  key: string;
  title: string;
  text: string;
  kind: 'traceback' | 'output';
  meta?: string;
  truncated?: string;
}

export interface ResultEvidence {
  // What was recorded, in the order it is read.
  blocks: EvidenceBlock[];
  // The titles of fields dropped whole: the session's failure text passed its budget.
  dropped: string[];
  // Why nothing is shown, when nothing is: 'silent', output was captured and was empty;
  // 'unrecorded', a failure carries no failure text, which a run that recorded it always
  // holds, so the run did not record it; 'unknown', a result that did not fail holds
  // nothing, as it does when the run recorded no failure text and also when it did with
  // output capture off (-s), which the API does not tell apart.
  absence: 'silent' | 'unrecorded' | 'unknown' | null;
}

const PHASES = ['setup', 'call', 'teardown'] as const;
type Phase = (typeof PHASES)[number];

function phaseOutcomeOf(detail: ResultDetail, phase: Phase): string | null {
  return detail[`${phase}_outcome`];
}

// The first phase pytest reported as failed: where the failure happened.
export function failingPhase(detail: ResultDetail): Phase | null {
  for (const phase of PHASES) {
    const o = phaseOutcomeOf(detail, phase);
    if (o === 'failed' || o === 'error') return phase;
  }
  return null;
}

function location(detail: ResultDetail): string | null {
  if (!detail.failure_path) return null;
  return detail.failure_lineno != null
    ? `${detail.failure_path}:${detail.failure_lineno}`
    : detail.failure_path;
}

function joined(...parts: (string | null | undefined)[]): string | undefined {
  const kept = parts.filter((p): p is string => Boolean(p));
  return kept.length ? kept.join(' · ') : undefined;
}

export function resultEvidence(detail: ResultDetail): ResultEvidence {
  const phase = failingPhase(detail);
  const output = 'setup, call and teardown';
  const fields: {
    key: string;
    title: string;
    text: string | null;
    truncated: boolean;
    kind: EvidenceBlock['kind'];
    meta?: string;
  }[] = [
    {
      key: 'traceback',
      title: 'Traceback',
      text: detail.traceback,
      truncated: detail.traceback_truncated,
      kind: 'traceback',
      meta: joined(phase ? `${phase} phase` : null, location(detail)),
    },
    {
      key: 'message',
      title: 'Message',
      text: detail.failure_message,
      truncated: detail.failure_message_truncated,
      kind: 'output',
      meta: joined(detail.failure_type),
    },
    {
      key: 'repr',
      title: 'Exception repr',
      text: detail.failure_repr,
      truncated: detail.failure_repr_truncated,
      kind: 'output',
    },
    {
      key: 'skip',
      title: 'Skip reason',
      text: detail.skip_reason,
      truncated: detail.skip_reason_truncated,
      kind: 'output',
    },
    {
      key: 'xfail',
      title: 'Xfail reason',
      text: detail.xfail_reason,
      truncated: detail.xfail_reason_truncated,
      kind: 'output',
    },
    {
      key: 'stdout',
      title: 'Captured stdout',
      text: detail.captured_stdout,
      truncated: detail.captured_stdout_truncated,
      kind: 'output',
      meta: output,
    },
    {
      key: 'stderr',
      title: 'Captured stderr',
      text: detail.captured_stderr,
      truncated: detail.captured_stderr_truncated,
      kind: 'output',
      meta: output,
    },
  ];
  const blocks: EvidenceBlock[] = [];
  const dropped: string[] = [];
  for (const f of fields) {
    if (f.text) {
      const block: EvidenceBlock = { key: f.key, title: f.title, text: f.text, kind: f.kind };
      if (f.meta) block.meta = f.meta;
      if (f.truncated) block.truncated = KEPT;
      blocks.push(block);
    } else if (f.text === null && f.truncated) {
      dropped.push(f.title);
    }
  }
  let absence: ResultEvidence['absence'] = null;
  if (!blocks.length && !dropped.length) {
    if (detail.captured_stdout === '' || detail.captured_stderr === '') absence = 'silent';
    else if (detail.outcome === 'failed' || detail.outcome === 'error') {
      const recorded =
        detail.failure_type !== null || fields.some((f) => f.text !== null || f.truncated);
      absence = recorded ? 'unknown' : 'unrecorded';
    } else absence = 'unknown';
  }
  return { blocks, dropped, absence };
}

// pytest reports each phase as passed, failed or skipped; the timeline names
// what that meant for the result: a failed setup or teardown is an error, a
// skipped phase of an xfailed test is its expected failure.
function phaseOutcome(phase: Phase, raw: string | null, outcome: Outcome): Outcome | undefined {
  if (raw == null || raw === 'passed') return undefined;
  if (raw === 'failed') return phase === 'call' && outcome !== 'error' ? 'failed' : 'error';
  if (raw === 'skipped') return outcome === 'xfailed' ? 'xfailed' : 'skipped';
  return isOutcome(raw) ? raw : undefined;
}

export function resultPhases(detail: ResultDetail): PhaseTimelineProps['phases'] {
  const out: PhaseTimelineProps['phases'] = [];
  for (const name of PHASES) {
    const seconds = detail[`${name}_duration`];
    if (seconds == null) continue;
    const phase: PhaseTimelineProps['phases'][number] = { name, seconds };
    const o = phaseOutcome(name, phaseOutcomeOf(detail, name), detail.outcome);
    if (o) phase.outcome = o;
    out.push(phase);
  }
  return out;
}

// The branch and commit a run was made at, as the history readout names it: main at 7aa1c5d.
// The readout is a sentence of plain text, so a bidi control in a recorded branch name is
// written out rather than left to reorder the outcome it names.
function commitDetail(vcs: HistoryEntry['vcs']): string | undefined {
  const branch = vcs?.branch ? visibleText(vcs.branch) : null;
  const sha = vcs?.commit ? vcs.commit.slice(0, 7) : null;
  if (branch && sha) return `${branch} at ${sha}`;
  return branch ?? sha ?? undefined;
}

export interface HistoryStrip {
  runs: (HistoryGridProps['runs'][number] & { href: string })[];
  outcomes: Outcome[];
  // Aligned to outcomes: how each result changed against its own run's baseline.
  changes: (Change | null)[];
  durations: (number | null)[];
}

// A page of history, newest first, as the grid and the spark draw it: oldest first.
export function historyStrip(entries: HistoryEntry[], nodeId: string): HistoryStrip {
  const oldest = [...entries].reverse();
  return {
    runs: oldest.map((e) => {
      const run: HistoryStrip['runs'][number] = {
        id: e.run_id,
        label: runLabel(e.run_id),
        href: resultHref(e.run_id, nodeId),
      };
      const detail = commitDetail(e.vcs);
      if (detail) run.detail = detail;
      return run;
    }),
    outcomes: oldest.map((e) => e.outcome),
    changes: oldest.map((e) => (e.change ? CHANGE[e.change] : null)),
    durations: oldest.map((e) => e.duration),
  };
}

export interface HistoryRow {
  key: string;
  runId: string;
  label: string;
  href: string;
  outcome: Outcome;
  commit: CommitRefProps;
  startedAt: string;
  seconds: number | null;
}

export function historyRow(entry: HistoryEntry, nodeId: string): HistoryRow {
  const commit: CommitRefProps = {};
  if (entry.vcs?.branch) commit.branch = entry.vcs.branch;
  if (entry.vcs?.commit) commit.sha = entry.vcs.commit;
  if (entry.vcs?.dirty) commit.dirty = true;
  return {
    key: entry.run_id,
    runId: entry.run_id,
    label: runLabel(entry.run_id),
    href: resultHref(entry.run_id, nodeId),
    outcome: entry.outcome,
    commit,
    startedAt: entry.started_at,
    seconds: entry.duration,
  };
}
