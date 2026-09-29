// The only code that knows both the API's field names and the design
// system's props. Pure functions, so each mapping is tested on its own.

import type { Project, ResultItem, RunDetail, RunListItem, RunMetadata } from '../api/queries';
import type {
  CommitRefProps,
  MetaListProps,
  Outcome,
  OutcomeCounts,
  ProjectRef,
  RunItem,
  RunState,
  RunStatusProps,
} from '../ds';
import { describeCounts } from '../ds';

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

function seconds(startedAt: string, finishedAt: string | null): number | undefined {
  if (!finishedAt) return undefined;
  const s = (Date.parse(finishedAt) - Date.parse(startedAt)) / 1000;
  return Number.isFinite(s) && s >= 0 ? s : undefined;
}

export function runItem(
  item: RunListItem,
  { sessionUser, outcomes }: { sessionUser: string | null; outcomes?: string },
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
  if (outcomes !== undefined) run.results = outcomesToResults(outcomes);
  return run;
}

// What the run page's line says it draws: stored order is the order pytest reported results.
export function dotlineLabel(counts: OutcomeCounts, running: boolean): string {
  return `Results in the order pytest reported them: ${describeCounts(counts)}${running ? ', still running' : ''}`;
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
  outcome: Outcome;
  message: string | null;
  seconds: number | null;
}

function firstLine(text: string | null | undefined): string | null {
  if (!text) return null;
  const line = text.split('\n').find((l) => l.trim() !== '');
  return line ?? null;
}

export function resultRow(item: ResultItem, index: number): ResultRow {
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
    outcome: item.outcome,
    message,
    seconds: item.duration,
  };
}
