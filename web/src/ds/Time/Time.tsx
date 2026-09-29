import type { TimeProps } from '../contract';
import { cx } from '../lib/cx';
import { fmtAbsolute, fmtRelative, toDate } from '../lib/format';

export function Time(p: TimeProps) {
  const d = p.value == null ? null : toDate(p.value);
  if (d == null || Number.isNaN(d.getTime()))
    return (
      <span className={cx('dl-meta__none', p.className)} title="Not recorded">
        —
      </span>
    );
  const abs = fmtAbsolute(p.value);
  const text =
    p.mode === 'absolute'
      ? abs
      : p.mode === 'date'
        ? abs.slice(0, 10)
        : fmtRelative(p.value, p.now);
  return (
    <time className={cx('dl-time', p.className)} dateTime={d.toISOString()} title={abs}>
      {text}
    </time>
  );
}
