import { useRef, useState } from 'react';
import { Button } from '../Button/Button';
import type { AppBarAccount, AppBarProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { useBand, useMenu } from '../lib/hooks';
import { menuItem } from '../lib/menu';
import { ProjectSwitcher } from '../ProjectSwitcher/ProjectSwitcher';
import { Tooltip } from '../Tooltip/Tooltip';
import { UserChip } from '../UserChip/UserChip';
import { Wordmark } from '../Wordmark/Wordmark';

interface AccountMenuProps {
  user: { name: string; initials?: string };
  account: AppBarAccount;
  defaultOpen?: boolean;
}

function AccountMenu(p: AccountMenuProps) {
  const m = useMenu({ defaultOpen: p.defaultOpen });
  const items = p.account.items || [];
  return (
    <div className="dl-account" ref={m.root} onKeyDown={m.onKeyDown}>
      <button
        ref={m.btn}
        type="button"
        className="dl-btn dl-btn--quiet dl-btn--sm"
        aria-haspopup="menu"
        aria-expanded={m.open ? 'true' : 'false'}
        aria-label={`Account: ${p.user.name}`}
        title={p.user.name}
        onClick={m.toggle}
      >
        <UserChip name={p.user.name} initials={p.user.initials} showName={false} />
        <Icon name="chevron-down" size={14} />
      </button>
      {m.open ? (
        <div className="dl-menu dl-menu--end" role="menu" aria-label="Account">
          <div className="dl-menu__note">
            {p.account.note != null ? p.account.note : p.user.name}
          </div>
          {items.length ? <div className="dl-menu__sep" role="separator" /> : null}
          {items.map((it, i) => {
            if ('separator' in it)
              // biome-ignore lint/suspicious/noArrayIndexKey: the items are a fixed list.
              return <div key={i} className="dl-menu__sep" role="separator" />;
            return menuItem(
              i,
              it.icon,
              it.label,
              null,
              () => {
                m.set(false);
                if (it.onSelect) it.onSelect();
              },
              it.current,
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

// Sections and search in the bar from 960px; narrower, a sections menu and a search icon; below 520px, tighter still.
const BAR_BANDS = [
  [960, 'wide'],
  [520, 'compact'],
  [0, 'tight'],
] as const;

interface SectionsMenuProps {
  nav: NonNullable<AppBarProps['nav']>;
  active?: string;
}

function SectionsMenu(p: SectionsMenuProps) {
  const m = useMenu({});
  const cur = p.nav.find((n) => n.id === p.active);
  return (
    <div className="dl-sections" ref={m.root} onKeyDown={m.onKeyDown}>
      <button
        ref={m.btn}
        type="button"
        className="dl-projsw__btn dl-sections__btn"
        aria-haspopup="menu"
        aria-expanded={m.open ? 'true' : 'false'}
        aria-label={`Sections${cur ? `, current: ${cur.label}` : ''}`}
        onClick={m.toggle}
      >
        <Icon name="menu" size={16} />
        <span className="dl-sections__label">{cur ? cur.label : 'Sections'}</span>
        <Icon name="chevron-down" size={14} />
      </button>
      {m.open ? (
        <div className="dl-menu" role="menu" aria-label="Sections">
          {p.nav.map((n) => {
            const on = n.id === p.active;
            return (
              <a
                key={n.id}
                href={n.href || '#'}
                role="menuitem"
                tabIndex={-1}
                className="dl-menu__item"
                aria-current={on ? 'page' : undefined}
                onClick={() => m.set(false)}
              >
                {on ? <Icon name="check" size={16} /> : <span />}
                <span className="dl-menu__name">{n.label}</span>
                <span />
              </a>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

export function AppBar(p: AppBarProps) {
  const nav = p.nav || [];
  const [band, attach] = useBand(BAR_BANDS);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchBtn = useRef<HTMLButtonElement | null>(null);
  const searchId = p.searchId || 'dl-appbar-search';
  const searching = searchOpen && band !== 'wide' && p.search !== false;
  function closeSearch() {
    setSearchOpen(false);
    if (searchBtn.current) searchBtn.current.focus();
  }
  function field(id: string, inRow: boolean) {
    return (
      <label className="dl-appbar__search">
        <span className="dl-sr">Search node ids</span>
        <Icon name="search" size={16} />
        <input
          className="dl-input dl-input--mono"
          type="search"
          id={id}
          placeholder="Search node ids"
          dir="ltr"
          // biome-ignore lint/a11y/noAutofocus: the row opens for typing, from a button the person pressed.
          autoFocus={inRow || undefined}
          onKeyDown={
            inRow
              ? (e) => {
                  if (e.key === 'Escape') {
                    e.preventDefault();
                    closeSearch();
                  }
                }
              : undefined
          }
        />
      </label>
    );
  }
  return (
    <header ref={attach} className={cx('dl-appbar', p.className)} data-band={band}>
      <div className="dl-appbar__bar">
        <Wordmark href={p.homeHref || '#'} />
        {p.project ? (
          <span className="dl-appbar__slash" aria-hidden="true">
            /
          </span>
        ) : null}
        {p.project ? (
          <ProjectSwitcher
            current={p.project}
            projects={p.projects}
            label={p.switcherLabel}
            defaultOpen={p.switcherOpen}
            onSelect={p.onSelectProject}
            canCreate={p.canCreate}
            onCreate={p.onCreate}
          />
        ) : null}
        {nav.length ? (
          <nav className="dl-appbar__nav" aria-label="Project">
            {nav.map((n) => (
              <a
                key={n.id}
                href={n.href || '#'}
                className="dl-appbar__link"
                aria-current={n.id === p.active ? 'page' : undefined}
              >
                {n.label}
              </a>
            ))}
          </nav>
        ) : null}
        {nav.length ? <SectionsMenu nav={nav} active={p.active} /> : null}
        <div className="dl-appbar__end">
          {p.search === false ? null : field(searchId, false)}
          {p.search === false ? null : (
            <Tooltip content="Search node ids" describe={false} className="dl-appbar__searchtip">
              <button
                ref={searchBtn}
                type="button"
                className="dl-btn dl-btn--quiet dl-btn--sm dl-btn--icon"
                aria-label="Search node ids"
                aria-expanded={searching ? 'true' : 'false'}
                onClick={() => setSearchOpen(!searchOpen)}
              >
                <Icon name="search" size={16} />
              </button>
            </Tooltip>
          )}
          {/* An open server has no accounts: no user, no account button. */}
          {!p.user ? null : p.account ? (
            <AccountMenu user={p.user} account={p.account} defaultOpen={p.accountOpen} />
          ) : (
            <span className="dl-account">
              <UserChip name={p.user.name} initials={p.user.initials} showName={false} />
            </span>
          )}
        </div>
      </div>
      {searching ? (
        <div className="dl-appbar__searchrow" role="search">
          {field(`${searchId}-row`, true)}
          <Button
            variant="quiet"
            size="sm"
            icon="cross"
            label="Close search"
            onClick={closeSearch}
          />
        </div>
      ) : null}
    </header>
  );
}
