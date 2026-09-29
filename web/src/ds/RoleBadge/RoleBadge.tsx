import type { IconName, RoleBadgeProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';

const ROLES: Record<string, [IconName, string]> = {
  owner: ['key', 'Owner'],
  editor: ['pencil', 'Editor'],
  viewer: ['eye', 'Viewer'],
};

export function RoleBadge(p: RoleBadgeProps) {
  // An open server checks no role: print nothing rather than a role nobody holds.
  if (p.role == null || (p.role as string) === '') return null;
  const r = ROLES[p.role] || (['user', String(p.role)] as [IconName, string]);
  return (
    <span className={cx('dl-role', p.className)}>
      <Icon name={r[0]} size={14} />
      {r[1]}
    </span>
  );
}
