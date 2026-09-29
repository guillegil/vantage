import type { DurationSparkProps } from '../contract';
import { cx } from '../lib/cx';
import { fmtSeconds } from '../lib/format';

export function DurationSpark(p: DurationSparkProps) {
  const v = (p.values || []).filter((x): x is number => x != null);
  const W = p.width || 120;
  const H = p.height || 28;
  if (!v.length) return <span className="dl-spark__range">No durations recorded</span>;
  const min = Math.min(...v);
  const max = Math.max(...v);
  const span = max - min || 1;
  const step = v.length > 1 ? W / (v.length - 1) : 0;
  const pts = v.map((x, i) => [i * step, 2 + (H - 5) * (1 - (x - min) / span)] as const);
  const line = pts.map((q, i) => `${i ? 'L' : 'M'}${q[0].toFixed(1)} ${q[1].toFixed(1)}`).join('');
  const end = pts[pts.length - 1] as readonly [number, number];
  const area = `${line}L${end[0].toFixed(1)} ${H}L0 ${H}Z`;
  const last = v[v.length - 1] as number;
  const label = `Duration over ${v.length} runs: last ${fmtSeconds(last)}, from ${fmtSeconds(min)} to ${fmtSeconds(max)}`;
  return (
    <span className={cx('dl-spark', p.className)}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
        <path className="dl-spark__area" d={area} />
        <line className="dl-spark__grid" x1={0} x2={W} y1={H - 0.5} y2={H - 0.5} />
        <path className="dl-spark__line" d={line} />
        <circle className="dl-spark__end" cx={end[0]} cy={end[1]} r={2.5} />
      </svg>
      <span className="dl-spark__value">{fmtSeconds(last)}</span>
      {p.showRange === false ? null : (
        <span className="dl-spark__range">{`${fmtSeconds(min)} – ${fmtSeconds(max)}`}</span>
      )}
    </span>
  );
}
