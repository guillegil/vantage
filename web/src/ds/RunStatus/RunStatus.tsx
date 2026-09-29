import type { ReactNode } from 'react';
import type { RunStatusProps } from '../contract';
import { cx } from '../lib/cx';

// pytest's exit statuses, as a finished run shows them.
const EXIT: Record<number, [string, string | null, string]> = {
  0: ['dot', 'passed', 'all passed'],
  1: ['dot', 'failed', 'tests failed'],
  2: ['square', null, 'interrupted'],
  3: ['dot', 'error', 'internal error'],
  4: ['dot', 'error', 'usage error'],
  5: ['dot', 'skipped', 'no tests collected'],
};

export function RunStatus(p: RunStatusProps) {
  const state = p.state || 'finished';
  let ind: ReactNode;
  let words: string;
  let exit: string | null = null;
  let note: string | null = null;
  let reason: string | null = null;
  if (state === 'running') {
    ind = <span className="dl-status__cursor" />;
    words = 'running';
    if (p.lastContact) note = `last contact ${p.lastContact}`;
  } else if (state === 'interrupted') {
    ind = <span className="dl-status__square" />;
    words = 'interrupted';
    reason = 'a report arrived saying the session was stopped';
  } else if (state === 'abandoned') {
    ind = <span className="dl-status__ring" />;
    words = 'abandoned';
    reason = 'no end recorded and no contact within the grace period';
  } else {
    const e = (p.exitStatus != null && EXIT[p.exitStatus]) || ['dot', 'skipped', 'finished'];
    ind =
      e[0] === 'square' ? (
        <span className="dl-status__square" />
      ) : (
        <span className={`dl-status__dot dl-status__dot--${e[1]}`} />
      );
    words = e[2];
    if (p.exitStatus != null) exit = `exit ${p.exitStatus}`;
  }
  const full = words + (exit ? ` · ${exit}` : '') + (note ? ` · ${note}` : '');
  const explained = reason && p.explain;
  return (
    <span
      className={cx('dl-status', explained && 'dl-status--explain', p.className)}
      title={reason ? `${full}: ${reason}` : full}
    >
      <span className="dl-status__ind" aria-hidden="true">
        {ind}
      </span>
      {p.compact ? (
        <span className="dl-sr">{full + (reason ? `: ${reason}` : '')}</span>
      ) : (
        <>
          <span>{words}</span>
          {exit ? <span className="dl-status__exit">{exit}</span> : null}
          {note ? <span className="dl-status__note">{note}</span> : null}
          {reason ? (
            explained ? (
              <span className="dl-status__note">{reason}</span>
            ) : (
              <span className="dl-sr">{`: ${reason}`}</span>
            )
          ) : null}
        </>
      )}
    </span>
  );
}
