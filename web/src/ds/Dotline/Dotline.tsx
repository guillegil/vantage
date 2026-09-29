import type { ReactElement } from 'react';
import type { Change, DotlineProps, Outcome, ResultLike } from '../contract';
import { cx } from '../lib/cx';
import { fmtCount, plural } from '../lib/format';
import { useWidth } from '../lib/hooks';
import {
  countOutcomes,
  describeCounts,
  isFailing,
  isOutcome,
  OUTCOMES,
  outcomeOf,
  rankOf,
} from '../lib/outcomes';

// A mark's [top, height] in a 16px track: height is attention.
const MARK: Record<Outcome, [number, number]> = {
  passed: [13, 3],
  xfailed: [13, 3],
  skipped: [15, 1],
  xpassed: [7, 9],
  failed: [0, 16],
  error: [0, 16],
};

// How far a new failure stands above the track, at a 16px track. It is drawn outside the box, so a
// line with change marks takes the same room as one without.
const LIFT = 4;

export function markRects(
  o: string,
  x: number,
  w: number,
  H: number,
  key: string,
  change: Change | null | undefined,
): ReactElement[] {
  const k = H / 16;
  const m = isOutcome(o) ? MARK[o] : MARK.skipped;
  // A new failure pokes out above the track, so it stands taller than a failure already known.
  const lift = change === 'new-failure' && isFailing(o) ? Math.round(LIFT * k) : 0;
  let out: ReactElement[];
  if (o === 'error') {
    // A bar broken in the middle tells error from failed without colour.
    const top = Math.round(7 * k);
    const gap = Math.max(1, Math.round(2 * k));
    out = [
      <rect
        key={`${key}a`}
        className="dl-m--error"
        x={x}
        y={-lift}
        width={w}
        height={top + lift}
      />,
      <rect
        key={`${key}b`}
        className="dl-m--error"
        x={x}
        y={top + gap}
        width={w}
        height={H - top - gap}
      />,
    ];
  } else {
    out = [
      <rect
        key={key}
        className={`dl-m--${o}`}
        x={x}
        y={Math.round(m[0] * k) - lift}
        width={w}
        height={Math.max(1, Math.round(m[1] * k)) + lift}
      />,
    ];
  }
  // A fixed test's dot wears a ring above it.
  if (change === 'fixed' && !isFailing(o)) out.push(ringShape(o, x, w, H, key));
  return out;
}

function ringShape(o: string, x: number, w: number, H: number, key: string): ReactElement {
  const k = H / 16;
  const cls = `dl-ring dl-ring--${isOutcome(o) ? o : 'passed'}`;
  if (w >= 6) {
    return (
      <circle
        key={`${key}r`}
        className={cls}
        cx={x + w / 2}
        cy={Math.round(7 * k)}
        r={Math.min(3, w / 2 - 0.75)}
      />
    );
  }
  // At a 3px pitch the ring is a square outline, kept inside its own column.
  return (
    <rect
      key={`${key}r`}
      className={cls}
      x={x + 0.5}
      y={Math.round(7 * k) + 0.5}
      width={w - 1}
      height={w - 1}
    />
  );
}

interface CharsProps {
  files: { path: string; results: ResultLike[] }[];
  running?: boolean;
  label: string;
  total?: number;
  className?: string;
}

function DotlineChars(p: CharsProps) {
  const total = p.total || p.files.reduce((a, f) => a + (f.results || []).length, 0);
  let done = 0;
  return (
    <div className={cx('dl-chars', p.className)} role="img" aria-label={p.label}>
      {p.files.map((f, i) => {
        const list = (f.results || []).map(outcomeOf);
        done += list.length;
        const pct = total ? Math.floor((done * 100) / total) : 0;
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: files keep the order pytest printed them.
          <div key={i} className="dl-chars__row" aria-hidden="true">
            {f.path ? <span className="dl-chars__path">{f.path}</span> : null}
            <span className="dl-chars__marks">
              {list.map((o, j) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: a result's place in the run is its identity.
                <span key={j} className={`dl-o--${o}`}>
                  {isOutcome(o) ? OUTCOMES[o].ch : '?'}
                </span>
              ))}
              {p.running && i === p.files.length - 1 ? <span className="dl-chars__cursor" /> : null}
            </span>
            <span className="dl-chars__pct">
              {`[${pct < 10 ? '  ' : pct < 100 ? ' ' : ''}${pct}%]`}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function changeOf(r: ResultLike): Change | null {
  return r && typeof r === 'object' ? r.change || null : null;
}

// Severity for a shared mark: a new failure outranks a failure already known.
function severity(o: string, c: Change | null | undefined): number {
  return rankOf(o) + (c === 'new-failure' && isFailing(o) ? 10 : 0);
}

function describeChanges(changes: (Change | null)[]): string {
  const c = countOutcomes(changes.filter((x): x is Change => Boolean(x)));
  const parts: string[] = [];
  if (c['new-failure']) parts.push(plural(c['new-failure'], 'new failure', 'new failures'));
  if (c.fixed) parts.push(`${fmtCount(c.fixed)} fixed`);
  return parts.join(', ');
}

export function Dotline(p: DotlineProps) {
  const [measured, attach] = useWidth();
  let raw: ResultLike[] = p.results || [];
  if (p.variant === 'chars' && p.files) raw = p.files.flatMap((f) => f.results || []);
  const list = raw.map(outcomeOf);
  const changes = raw.map(changeOf);
  const changed = describeChanges(changes);
  const label =
    p.label ||
    `Results in collection order: ${describeCounts(countOutcomes(list))}${changed ? `; ${changed}` : ''}${
      p.running ? ', still running' : ''
    }`;
  if (p.variant === 'chars') {
    return (
      <DotlineChars
        files={p.files || [{ path: '', results: list }]}
        running={p.running}
        label={label}
        total={p.total}
        className={p.className}
      />
    );
  }
  const fill = p.width === 'auto';
  // Before its box is measured, a filling line draws at 320px; the first measurement redraws it.
  const width = fill ? measured || 320 : typeof p.width === 'number' && p.width ? p.width : 320;
  const pitch = 4;
  const w = 3;
  const H = 16;
  const capacity = Math.max(1, Math.floor((width - (p.running ? 10 : 0) + 1) / pitch));
  const per = Math.max(1, Math.ceil(list.length / capacity));
  const marks: [Outcome, Change | null][] = [];
  for (let i = 0; i < list.length; i += per) {
    let wi = i;
    let fixed = false;
    for (let j = i; j < Math.min(i + per, list.length); j++) {
      if (severity(list[j] as Outcome, changes[j]) > severity(list[wi] as Outcome, changes[wi]))
        wi = j;
      if (changes[j] === 'fixed') fixed = true;
    }
    const o = list[wi] as Outcome;
    const c =
      changes[wi] === 'new-failure' ? 'new-failure' : fixed && !isFailing(o) ? 'fixed' : null;
    marks.push([o, c]);
  }
  const used = marks.length * pitch;
  const svgW = Math.max(used + (p.running ? 10 : 0), 8);
  let kids: ReactElement[] = [
    <rect key="base" className="dl-dotline__base" x={0} y={H - 1} width={svgW} height={1} />,
  ];
  marks.forEach((m, k) => {
    kids = kids.concat(markRects(m[0], k * pitch, w, H, `m${k}`, m[1]));
  });
  if (p.running)
    kids.push(<rect key="cursor" className="dl-cursor" x={used + 2} y={0} width={6} height={H} />);
  // The selected result's place in the run, outlined as the history grid outlines its cursor.
  if (p.cursor != null && p.cursor >= 0 && p.cursor < list.length) {
    const ck = Math.floor(p.cursor / per);
    kids.push(
      <rect
        key="sel"
        className="dl-dotline__cursor"
        x={ck * pitch - 1.5}
        y={-LIFT - 1.5}
        width={w + 3}
        height={H + LIFT + 3}
        rx={1}
      />,
    );
  }
  const scale = per > 1 ? `1 mark = ${per} results, the most severe shown` : null;
  const full = label + (scale ? `. ${scale}` : '');
  return (
    <div
      ref={fill ? attach : undefined}
      className={cx('dl-dotline', fill && 'dl-dotline--fill', p.className)}
    >
      <svg width={svgW} height={H} viewBox={`0 0 ${svgW} ${H}`} role="img" aria-label={full}>
        <title>{full}</title>
        {kids}
      </svg>
      {scale && p.showScale !== false ? <span className="dl-dotline__cap">{scale}</span> : null}
      {/* pytest's summary line under a run's line, ending where the line ends. */}
      {p.under ? (
        <div className="dl-dotline__under" style={{ width: svgW }}>
          {p.under}
        </div>
      ) : null}
    </div>
  );
}
