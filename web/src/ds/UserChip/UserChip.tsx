import type { UserChipProps } from '../contract';
import { cx } from '../lib/cx';

export function initialsOf(name: string): string {
  const ps = String(name || '?')
    .trim()
    .split(/\s+/);
  return (
    (ps[0] || '?').charAt(0) + (ps.length > 1 ? (ps[ps.length - 1] ?? '').charAt(0) : '')
  ).toUpperCase();
}

export function UserChip(p: UserChipProps) {
  return (
    <span
      className={cx('dl-user', p.size === 'sm' && 'dl-user--sm', p.className)}
      title={p.username ? `${p.name} (${p.username})` : p.name}
    >
      <span className="dl-user__mono" aria-hidden="true">
        {p.initials || initialsOf(p.name)}
      </span>
      {p.showName === false ? (
        <span className="dl-sr">{p.name}</span>
      ) : (
        <span className="dl-user__name">
          {p.name}
          {p.you ? <span className="dl-user__you"> (you)</span> : null}
        </span>
      )}
    </span>
  );
}
