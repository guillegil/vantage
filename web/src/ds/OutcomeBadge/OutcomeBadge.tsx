import type { OutcomeBadgeProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { fmtCount } from '../lib/format';
import { isOutcome, OUTCOMES, partWord } from '../lib/outcomes';

export function OutcomeBadge(p: OutcomeBadgeProps) {
  const o = isOutcome(p.outcome) ? p.outcome : 'skipped';
  return (
    <span className={cx('dl-badge', `dl-badge--${o}`, p.className)}>
      <Icon name={OUTCOMES[o].glyph} size={14} />
      {p.count != null ? <span className="dl-badge__count">{fmtCount(p.count)}</span> : null}
      {p.count != null ? partWord(o, p.count) : o}
    </span>
  );
}
