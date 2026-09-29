import type { RetentionTagProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { fmtAbsolute, plural } from '../lib/format';

export function RetentionTag(p: RetentionTagProps) {
  const state = p.state || 'kept';
  if (state === 'pinned') {
    return (
      <span
        className={cx('dl-tag dl-tag--plain', p.className)}
        title={p.title || 'Kept while it is pinned'}
      >
        <Icon name="pin" size={12} />
        kept · pinned
        <span className="dl-sr">{`: ${p.title || 'kept while it is pinned'}`}</span>
      </span>
    );
  }
  if (state === 'expires') {
    const soon = p.days != null && p.days <= 7;
    const days = p.days ?? 0;
    return (
      <span
        className={cx('dl-tag', soon ? 'dl-tag--warning' : 'dl-tag--plain', p.className)}
        title={p.rule ? `Rule: ${p.rule}` : undefined}
      >
        <Icon name="clock" size={12} />
        {(p.days === 0
          ? 'deleted today'
          : `deleted in ${plural(days, 'day', 'days').replace(/ days?$/, ' d')}`) +
          (p.date && p.days !== 0 ? `, on ${fmtAbsolute(p.date).slice(0, 10)}` : '')}
        {p.rule ? <span className="dl-sr">{`. Rule: ${p.rule}`}</span> : null}
      </span>
    );
  }
  return (
    <span
      className={cx('dl-tag dl-tag--plain', p.className)}
      title={p.title || 'No retention rule matches this run'}
    >
      kept
      <span className="dl-sr">{`: ${p.title || 'no retention rule matches this run'}`}</span>
    </span>
  );
}
