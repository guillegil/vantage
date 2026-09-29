import type { ProjectSwitcherProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { useMenu } from '../lib/hooks';
import { menuItem } from '../lib/menu';
import { RoleBadge } from '../RoleBadge/RoleBadge';

export function ProjectSwitcher(p: ProjectSwitcherProps) {
  const m = useMenu(p);
  const cur = p.current;
  const projects = p.projects || [];
  // Only a person who may create projects gets the item, and only with something to call.
  const onCreate = p.canCreate !== false && typeof p.onCreate === 'function' ? p.onCreate : null;
  return (
    <div className={cx('dl-projsw', p.className)} ref={m.root} onKeyDown={m.onKeyDown}>
      <button
        ref={m.btn}
        type="button"
        className="dl-projsw__btn"
        aria-haspopup="menu"
        aria-expanded={m.open ? 'true' : 'false'}
        title={cur?.name}
        onClick={m.toggle}
      >
        <span className="dl-projsw__name">{cur?.name || 'Choose a project'}</span>
        <Icon name="chevron-down" size={14} />
      </button>
      {m.open ? (
        <div className="dl-menu" role="menu" aria-label="Projects">
          <div className="dl-menu__label">{p.label || 'Your projects'}</div>
          {projects.length ? null : (
            <div className="dl-menu__empty">
              {p.emptyText ||
                'No projects you can open yet. An admin of this server can add you to one.'}
            </div>
          )}
          {projects.map((pr) => {
            const isCur = pr.name === cur?.name;
            return menuItem(
              pr.name,
              isCur ? 'check' : null,
              pr.name,
              pr.role ? <RoleBadge role={pr.role} /> : null,
              () => {
                m.set(false);
                if (p.onSelect) p.onSelect(pr);
              },
              isCur,
              pr.tracks,
            );
          })}
          {onCreate ? (
            <>
              <div className="dl-menu__sep" role="separator" />
              {menuItem('new', 'plus', 'New project', null, () => {
                m.set(false);
                onCreate();
              })}
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
