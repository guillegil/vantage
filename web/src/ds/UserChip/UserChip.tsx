import type { UserChipProps } from '../contract';
import { cx } from '../lib/cx';

// Two letters: a name's first and last initials, or a username's first two letters or parts (ci-bench: CB).
export function initialsOf(name: string): string {
  const t = String(name || '?').trim();
  let ps = t.split(/\s+/);
  if (ps.length < 2) ps = t.split(/[._-]+/).filter(Boolean);
  if (ps.length < 2) return (t.charAt(0) + t.charAt(1)).toUpperCase() || '?';
  return ((ps[0] ?? '').charAt(0) + (ps[ps.length - 1] ?? '').charAt(0)).toUpperCase();
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
        <span className={cx('dl-user__name', p.mono && 'dl-user__name--mono')}>
          {p.name}
          {p.you ? <span className="dl-user__you"> (you)</span> : null}
        </span>
      )}
    </span>
  );
}
