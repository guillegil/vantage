import type { ReactNode } from 'react';
import { CommitRef } from '../CommitRef/CommitRef';
import type { OutcomeCounts, RunItem, RunListProps } from '../contract';
import { Dotline } from '../Dotline/Dotline';
import { EmptyState } from '../EmptyState/EmptyState';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { fmtCount, fmtSeconds, plural } from '../lib/format';
import { useBand, useUid } from '../lib/hooks';
import { countOutcomes, isOutcome, outcomeOf, PYTEST_ORDER, partWord } from '../lib/outcomes';
import { PinButton } from '../PinButton/PinButton';
import { RetentionTag } from '../RetentionTag/RetentionTag';
import { RunStatus } from '../RunStatus/RunStatus';
import { Time } from '../Time/Time';
import { UserChip } from '../UserChip/UserChip';
import { VisibilityTag } from '../VisibilityTag/VisibilityTag';

function RunCounts(c: OutcomeCounts): ReactNode {
  const parts: ReactNode[] = [];
  for (const k of PYTEST_ORDER) {
    const n = c[k];
    if (!n || !isOutcome(k)) continue;
    if (parts.length) parts.push(' · ');
    parts.push(
      <span key={k}>
        <b className={`dl-o--${k}`}>{fmtCount(n)}</b>
        {` ${partWord(k, n)}`}
      </span>,
    );
  }
  return parts.length ? parts : 'no results';
}

// "2 new · 1 fixed": failures this run has that its baseline did not, and failures it no longer has.
function RunChanges(ch: RunItem['changes']): ReactNode {
  if (!ch || (!ch.newFailures && !ch.fixed)) return null;
  const parts: ReactNode[] = [];
  const words: string[] = [];
  if (ch.newFailures) {
    parts.push(
      <span key="n">
        <b className="dl-o--failed">{fmtCount(ch.newFailures)}</b> new
      </span>,
    );
    words.push(plural(ch.newFailures, 'new failure', 'new failures'));
  }
  if (ch.fixed) {
    if (parts.length) parts.push(' · ');
    parts.push(
      <span key="f">
        <b className="dl-o--passed">{fmtCount(ch.fixed)}</b> fixed
      </span>,
    );
    words.push(`${fmtCount(ch.fixed)} fixed`);
  }
  const said = words.join(' and ') + (ch.baseline ? `, compared with ${ch.baseline}` : '');
  return (
    <span className="dl-run__changes" title={said}>
      <span aria-hidden="true">{parts}</span>
      <span className="dl-sr">{`${said}. `}</span>
    </span>
  );
}

interface RunRowProps {
  run: RunItem;
  now?: string | Date;
  onPin?: (run: RunItem, pinned: boolean) => void;
}

function RunRow(p: RunRowProps) {
  const r = p.run;
  const finished = !r.state || r.state === 'finished';
  const results = r.results || [];
  const counts = r.counts || countOutcomes(results.map(outcomeOf));
  const onPin = p.onPin;
  return (
    <div className="dl-run" role="listitem">
      {/* Without onPin the column stays, empty, so every row keeps the same grid. */}
      {onPin ? (
        <PinButton
          pinned={!!r.pinned}
          disabledReason={r.pinDisabledReason}
          onChange={(v) => onPin(r, v)}
        />
      ) : (
        <span className="dl-run__pin" aria-hidden="true" />
      )}
      <div className="dl-run__cell dl-run__who">
        <div className="dl-run__line">
          <RunStatus state={r.state} exitStatus={r.exitStatus} compact={finished} />
          <a className="dl-run__id" href={r.href || '#'} title={r.id}>
            {r.label || r.id}
          </a>
        </div>
        <div className="dl-run__by">
          {r.by ? (
            <UserChip name={r.by.name} initials={r.by.initials} you={r.by.you} size="sm" />
          ) : null}
          {r.startedAt ? <Time value={r.startedAt} now={p.now} /> : null}
        </div>
      </div>
      <div className="dl-run__cell dl-run__results">
        <Dotline results={results} running={r.state === 'running'} width={200} showScale={false} />
        <span
          className={cx(
            'dl-run__counts',
            r.changes && (r.changes.newFailures || r.changes.fixed) && 'dl-run__counts--changes',
          )}
        >
          {RunChanges(r.changes)}
          <span className="dl-run__outs">{RunCounts(counts)}</span>
        </span>
      </div>
      <div className="dl-run__cell dl-run__commit">
        <CommitRef branch={r.branch} sha={r.sha} dirty={r.dirty} />
      </div>
      <div className="dl-run__cell dl-run__access">
        <VisibilityTag visibility={r.visibility ?? 'project'} sharedWith={r.sharedWith} />
        {/* A run no rule will delete needs no tag in a list; one appears when a pin holds it or a rule will delete it. */}
        {r.retention && r.retention.state !== 'kept' ? <RetentionTag {...r.retention} /> : null}
      </div>
      <div className="dl-run__dur">{r.seconds != null ? fmtSeconds(r.seconds) : '—'}</div>
    </div>
  );
}

// Every fact its own column from 1000px; commit and visibility share one from 740px; narrower, rows stack.
const RUN_BANDS = [
  [1000, 'wide'],
  [740, 'mid'],
  [0, 'narrow'],
] as const;

export function RunList(p: RunListProps) {
  const runs = p.runs || [];
  const gid = useUid('dl-runs');
  const [band, attach] = useBand(RUN_BANDS);
  if (!runs.length) {
    return (
      <>
        {p.empty || (
          <EmptyState title="No runs yet" command="pytest --vantage">
            Runs appear here once a pytest session reports to this server.
          </EmptyState>
        )}
      </>
    );
  }
  const pinned = runs.filter((r) => r.pinned);
  const rest = runs.filter((r) => !r.pinned);
  const row = (r: RunItem) => <RunRow key={r.id} run={r} now={p.now} onPin={p.onPin} />;
  // Each group is its own list, named by its visible heading, so "Pinned" reaches a screen reader too.
  const group = (key: string, title: string, icon: 'pin' | null, items: RunItem[]) => [
    <div key={`${key}h`} id={gid + key} className="dl-runs__group">
      {icon ? <Icon name={icon} size={12} /> : null}
      {title}
    </div>,
    <div key={`${key}l`} role="list" aria-labelledby={gid + key}>
      {items.map(row)}
    </div>,
  ];
  const heads =
    band === 'mid'
      ? ['Run', 'Results', 'Commit and visibility']
      : ['Run', 'Results', 'Commit', 'Visibility'];
  return (
    <div ref={attach} className={cx('dl-runs', p.className)} data-band={band}>
      {p.header === false || band === 'narrow' ? null : (
        <div className="dl-runs__head" aria-hidden="true">
          <span />
          {heads.map((t) => (
            <span key={t}>{t}</span>
          ))}
          <span className="is-end">Duration</span>
        </div>
      )}
      {pinned.length ? group('p', 'Pinned', 'pin', pinned) : null}
      {pinned.length && rest.length ? group('r', 'Newest first', null, rest) : null}
      {pinned.length ? null : (
        <div role="list" aria-label={p.label || 'Runs'}>
          {rest.map(row)}
        </div>
      )}
    </div>
  );
}
