import {
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactElement,
  useRef,
  useState,
} from 'react';
import type { Change, HistoryGridProps, Outcome } from '../contract';
import { markRects } from '../Dotline/Dotline';
import { cx } from '../lib/cx';
import { fmtCount, plural } from '../lib/format';
import { useOverflowFocus, useUid } from '../lib/hooks';
import { isFailing } from '../lib/outcomes';
import { visibleText } from '../lib/visible';
import { NodeId } from '../NodeId/NodeId';

type HistoryRun = HistoryGridProps['runs'][number] & {
  /** The run's result of this test; a cell whose run has one opens it. */
  href?: string;
};
type HistoryRowData = HistoryGridProps['rows'][number];

export interface PortedHistoryGridProps extends HistoryGridProps {
  runs: HistoryRun[];
  /** Follows a run's href, in place of loading it as a new page. */
  onOpen?: (href: string) => void;
}

const CHANGE_WORDS: Record<Change, string> = {
  'new-failure': 'new failure',
  'still-failing': 'still failing',
  fixed: 'fixed',
  'new-test': 'new test',
  removed: 'removed',
  'not-reached': 'not reached',
};

interface Note {
  kind: 'none' | 'failing' | 'flaky' | 'passing';
  strong?: string;
  rest: string;
}

export function historyNote(outcomes: (Outcome | null)[], runs: { label: string }[]): Note {
  const idx: number[] = [];
  outcomes.forEach((o, i) => {
    if (o) idx.push(i);
  });
  if (!idx.length) return { kind: 'none', rest: 'not in these runs' };
  // A test missing from some runs says so, so a rate never looks better than its sample.
  const within =
    idx.length < runs.length
      ? ` · in ${fmtCount(idx.length)} of ${plural(runs.length, 'run', 'runs')}`
      : '';
  const last = outcomes[idx[idx.length - 1] as number];
  if (isFailing(last)) {
    let n = 0;
    let first = idx[idx.length - 1] as number;
    for (let k = idx.length - 1; k >= 0 && isFailing(outcomes[idx[k] as number]); k--) {
      n++;
      first = idx[k] as number;
    }
    const since = runs[first]?.label;
    return {
      kind: 'failing',
      strong: `failing ${plural(n, 'run', 'runs')}`,
      rest: since ? ` since ${since}` : '',
    };
  }
  let flips = 0;
  let prev: boolean | null = null;
  let measured = 0;
  let ok = 0;
  for (const i of idx) {
    const o = outcomes[i];
    if (o === 'skipped') continue;
    measured++;
    if (o === 'passed' || o === 'xfailed') ok++;
    const f = !(o === 'passed' || o === 'xfailed');
    if (prev !== null && f !== prev) flips++;
    prev = f;
  }
  if (flips >= 3)
    return {
      kind: 'flaky',
      strong: 'flaky',
      rest: ` · ${fmtCount(flips)} flips in ${plural(idx.length, 'run', 'runs')}`,
    };
  if (!measured) return { kind: 'none', strong: 'skipped', rest: ` in every run${within}` };
  const pct = (ok * 100) / measured;
  // Never round up to 100% while anything is not passing.
  const s = ok === measured ? '100' : Math.min(99.9, pct).toFixed(1).replace(/\.0$/, '');
  return { kind: 'passing', strong: `${s}%`, rest: ` passing${within}` };
}

// One test across runs. Focused, arrow keys move a cursor from run to run and the note reads
// that run and its outcome; the pointer does the same, so no fact lives only in a tooltip.
function HistoryRow(p: {
  row: HistoryRowData;
  runs: HistoryRun[];
  W: number;
  stacked: boolean;
  onOpen?: (href: string) => void;
}) {
  const { row, runs, W } = p;
  const outs = row.outcomes || [];
  const chg = row.changes || [];
  const pitch = 10;
  const w = 8;
  const H = 16;
  const n = outs.length;
  const [cur, setCur] = useState(-1);
  const noteId = useUid('dl-hnote');
  const marks: ReactElement[] = [
    <rect key="base" className="dl-dotline__base" x={0} y={H - 1} width={W} height={1} />,
  ];
  outs.forEach((o, i) => {
    const shape = o
      ? markRects(o, i * pitch, w, H, 'c', chg[i])
      : [<rect key="n" className="dl-m--none" x={i * pitch + 3} y={H - 4} width={2} height={2} />];
    // biome-ignore lint/suspicious/noArrayIndexKey: a cell's place is its run, oldest first.
    marks.push(<g key={`g${i}`}>{shape}</g>);
  });
  if (cur >= 0)
    marks.push(
      <rect
        key="cursor"
        className="dl-hgrid__cursor"
        x={cur * pitch - 2}
        y={-2}
        width={w + 4}
        height={H + 4}
        rx={2}
      />,
    );
  function at(i: number): string {
    const run = runs[i];
    const change = chg[i];
    const c = outs[i] && change ? `, ${CHANGE_WORDS[change]}` : '';
    return `${run?.label || `run ${i + 1}`}${run?.detail ? ` (${run.detail})` : ''}: ${
      outs[i] || 'not in this run'
    }${c}`;
  }
  function move(i: number) {
    if (n) setCur(Math.max(0, Math.min(n - 1, i)));
  }
  // A run that carries its result's address opens it: the pointer's cell, or the cursor's on Enter.
  const opens = runs.some((r) => r.href);
  function open(i: number) {
    const href = runs[i]?.href;
    if (!href || i < 0 || i >= n) return;
    if (p.onOpen) p.onOpen(href);
    else window.location.assign(href);
  }
  function onKey(e: KeyboardEvent<SVGSVGElement>) {
    const from = cur < 0 ? n - 1 : cur;
    if (e.key === 'Enter' && opens) {
      e.preventDefault();
      open(from);
      return;
    }
    let to: number | null = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') to = from + 1;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') to = from - 1;
    else if (e.key === 'Home') to = 0;
    else if (e.key === 'End') to = n - 1;
    if (to === null) return;
    e.preventDefault();
    move(to);
  }
  function cellOf(e: PointerEvent<SVGSVGElement> | MouseEvent<SVGSVGElement>): number | null {
    const r = e.currentTarget.getBoundingClientRect();
    if (!r.width) return null;
    return Math.floor(((e.clientX - r.left) * (W / r.width)) / pitch);
  }
  function onPointer(e: PointerEvent<SVGSVGElement>) {
    const i = cellOf(e);
    if (i !== null) move(i);
  }
  function onClick(e: MouseEvent<SVGSVGElement>) {
    const i = cellOf(e);
    if (i !== null) open(i);
  }
  const note = historyNote(outs, runs);
  const now = cur < 0 ? n - 1 : cur;
  return (
    <>
      {p.stacked ? null : (
        <div className="dl-hgrid__label">
          <NodeId value={row.nodeid} truncate href={row.href} />
        </div>
      )}
      <svg
        width={W}
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        className="dl-hgrid__marks"
        tabIndex={n ? 0 : undefined}
        role={n ? 'slider' : 'img'}
        aria-label={`History of ${row.nodeid ? visibleText(row.nodeid) : 'this test'}`}
        aria-describedby={noteId}
        aria-orientation={n ? 'horizontal' : undefined}
        aria-valuemin={n ? 1 : undefined}
        aria-valuemax={n || undefined}
        aria-valuenow={n ? now + 1 : undefined}
        aria-valuetext={n ? at(now) : undefined}
        aria-keyshortcuts={n && opens ? 'Enter' : undefined}
        onKeyDown={onKey}
        onFocus={() => {
          if (cur < 0) move(n - 1);
        }}
        onBlur={() => setCur(-1)}
        onPointerMove={onPointer}
        onPointerDown={onPointer}
        onPointerLeave={(e) => {
          if (e.pointerType === 'mouse') setCur(-1);
        }}
        onClick={opens ? onClick : undefined}
      >
        {marks}
      </svg>
      {/* A row can leave its note out where the line above it already says the same; the readout still shows. */}
      {row.note === false && cur < 0 ? (
        <div id={noteId} className="dl-hgrid__note dl-sr">
          {(note.strong || '') + note.rest}
        </div>
      ) : (
        <div
          id={noteId}
          className={cx('dl-hgrid__note', cur < 0 && `dl-hgrid__note--${note.kind}`)}
        >
          {cur >= 0 ? (
            <span className="dl-hgrid__readout">{at(cur)}</span>
          ) : (
            <>
              {note.strong ? <b>{note.strong}</b> : null}
              {note.rest}
            </>
          )}
        </div>
      )}
    </>
  );
}

export function HistoryGrid(p: PortedHistoryGridProps) {
  const runs = p.runs || [];
  const rows = p.rows || [];
  const stacked = !!p.stacked;
  const W = Math.max(runs.length * 10 - 2, 8);
  const wrap = useRef<HTMLDivElement | null>(null);
  const over = useOverflowFocus(wrap);
  return (
    <div
      ref={wrap}
      className={cx('dl-hgrid-wrap', p.className)}
      tabIndex={over ? 0 : undefined}
      role={over ? 'region' : undefined}
      aria-label={over ? 'Test history' : undefined}
    >
      <div className={cx('dl-hgrid', stacked && 'dl-hgrid--stacked')}>
        {stacked ? null : (
          <div className="dl-hgrid__axis">
            <span>{rows.length === 1 ? 'test' : plural(rows.length, 'test', 'tests')}</span>
          </div>
        )}
        <div className="dl-hgrid__axis" style={{ width: W }}>
          <span>{runs.length ? runs[0]?.label : ''}</span>
          <span>{runs.length > 1 ? runs[runs.length - 1]?.label : ''}</span>
        </div>
        {stacked ? null : (
          <div className="dl-hgrid__axis">
            <span>{`${plural(runs.length, 'run', 'runs')}, oldest to newest`}</span>
          </div>
        )}
        {rows.map((row, r) => (
          <HistoryRow
            // biome-ignore lint/suspicious/noArrayIndexKey: rows keep the order the consumer gave.
            key={(row.nodeid || '') + r}
            row={row}
            runs={runs}
            W={W}
            stacked={stacked}
            onOpen={p.onOpen}
          />
        ))}
      </div>
    </div>
  );
}
