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
import { useOverflowFocus, useUid, useWidth } from '../lib/hooks';
import { isFailing } from '../lib/outcomes';
import { visibleText } from '../lib/visible';
import { NodeId } from '../NodeId/NodeId';

type HistoryRun = HistoryGridProps['runs'][number];
type HistoryRowData = HistoryGridProps['rows'][number];

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
  pitch: number;
  mark: number;
  height: number;
  stacked: boolean;
  onOpen?: HistoryGridProps['onOpen'];
}) {
  const { row, runs, W } = p;
  const outs = row.outcomes || [];
  const chg = row.changes || [];
  const pitch = p.pitch || 10;
  const w = p.mark || 8;
  const H = p.height || 16;
  const n = outs.length;
  const [cur, setCur] = useState(-1);
  const noteId = useUid('dl-hnote');
  const marks: ReactElement[] = [
    <rect key="base" className="dl-dotline__base" x={0} y={H - 1} width={W} height={1} />,
  ];
  outs.forEach((o, i) => {
    const shape = o
      ? markRects(o, i * pitch, w, H, 'c', chg[i])
      : [
          <rect
            key="n"
            className="dl-m--none"
            x={i * pitch + Math.floor(w / 2) - 1}
            y={H - 4}
            width={2}
            height={2}
          />,
        ];
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
    return `${run?.label || `run ${i + 1}`}${run?.detail ? ` (${visibleText(run.detail)})` : ''}: ${
      outs[i] || 'not in this run'
    }${c}`;
  }
  function move(i: number) {
    if (n) setCur(Math.max(0, Math.min(n - 1, i)));
  }
  // A run that carries the address of this test's result in it opens there: the pointer's cell, or the cursor's on Enter.
  const opens = runs.some((r) => r?.href);
  function open(i: number) {
    const run = runs[i];
    const href = run?.href;
    if (!run || !href || i < 0 || i >= n) return;
    if (p.onOpen) p.onOpen(href, run);
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
        className={cx('dl-hgrid__marks', opens && 'dl-hgrid__marks--opens')}
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

export function HistoryGrid(p: HistoryGridProps) {
  const runs = p.runs || [];
  const rows = p.rows || [];
  const stacked = !!p.stacked;
  // fill: a test's own page gives its runs the panel's width, larger marks for fewer runs, so the history
  // is the loudest thing on the page. Elsewhere a run takes 10px.
  const [width, measure] = useWidth();
  const fill = !!p.fill && stacked;
  const pitch =
    fill && width && runs.length
      ? Math.max(10, Math.min(28, Math.floor((width + 4) / runs.length)))
      : 10;
  const mark = pitch >= 16 ? pitch - 4 : pitch - 2;
  const tall = pitch >= 16 ? 24 : 16;
  const W = Math.max(runs.length * pitch - (pitch - mark), 8);
  const wrap = useRef<HTMLDivElement | null>(null);
  const over = useOverflowFocus(wrap);
  // The first and last run's labels sit under the ends of the marks. With too few runs to hold both
  // apart, the axis names none: the readout names each run, and the span is said in words.
  const first = runs.length ? String(runs[0]?.label || '') : '';
  const last = runs.length > 1 ? String(runs[runs.length - 1]?.label || '') : '';
  const fits = W >= (first.length + last.length) * 7.2 + 12;
  const span =
    runs.length === 1 ? 'the only run' : `${plural(runs.length, 'run', 'runs')}, oldest to newest`;
  return (
    <div
      ref={wrap}
      className={cx('dl-hgrid-wrap', p.className)}
      tabIndex={over ? 0 : undefined}
      role={over ? 'region' : undefined}
      aria-label={over ? 'Test history' : undefined}
    >
      {fill ? <div ref={measure} className="dl-hgrid__measure" aria-hidden="true" /> : null}
      <div className={cx('dl-hgrid', stacked && 'dl-hgrid--stacked')}>
        {stacked ? null : (
          <div className="dl-hgrid__axis">
            <span>{rows.length === 1 ? 'test' : plural(rows.length, 'test', 'tests')}</span>
          </div>
        )}
        {fits ? (
          <div className="dl-hgrid__axis" style={{ width: W }}>
            <span>{first}</span>
            <span>{last}</span>
          </div>
        ) : (
          <div
            className="dl-hgrid__axis dl-hgrid__axis--words"
            style={stacked ? undefined : { width: W }}
          >
            {stacked ? <span>{span}</span> : null}
          </div>
        )}
        {stacked ? null : (
          <div className="dl-hgrid__axis">
            <span>{span}</span>
          </div>
        )}
        {rows.map((row, r) => (
          <HistoryRow
            // biome-ignore lint/suspicious/noArrayIndexKey: rows keep the order the consumer gave.
            key={(row.nodeid || '') + r}
            row={row}
            runs={runs}
            W={W}
            pitch={pitch}
            mark={mark}
            height={tall}
            stacked={stacked}
            onOpen={p.onOpen}
          />
        ))}
      </div>
    </div>
  );
}
