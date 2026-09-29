import type { OutcomeMarkProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { isOutcome, OUTCOMES } from '../lib/outcomes';

export function OutcomeMark(p: OutcomeMarkProps) {
  const o = isOutcome(p.outcome) ? p.outcome : 'skipped';
  return (
    <Icon
      name={OUTCOMES[o].glyph}
      size={p.size || 16}
      title={p.label || o}
      className={cx(`dl-o--${o}`, p.className)}
    />
  );
}
