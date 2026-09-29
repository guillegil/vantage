import type { ReactNode } from 'react';
import type { IconName } from '../contract';
import { Icon } from '../Icon/Icon';

// One item of a project or account menu: its icon (or an empty cell), its name, what follows it, and a line under it.
export function menuItem(
  key: string | number,
  icon: IconName | null | undefined,
  label: ReactNode,
  extra: ReactNode,
  onSelect: () => void,
  current?: boolean,
  sub?: ReactNode,
) {
  return (
    <button
      key={key}
      type="button"
      role="menuitem"
      tabIndex={-1}
      className="dl-menu__item"
      aria-current={current ? 'true' : undefined}
      onClick={onSelect}
    >
      {icon ? <Icon name={icon} size={16} /> : <span />}
      <span className="dl-menu__name">{label}</span>
      {extra || <span />}
      {sub ? <span className="dl-menu__sub">{sub}</span> : null}
    </button>
  );
}
