import type { MenuButtonProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { useMenu } from '../lib/hooks';
import { Tooltip } from '../Tooltip/Tooltip';

// An icon button that opens a short menu of actions. An item a person may not use stays in the
// menu with its reason under it, so the reason is found where the action is.
export function MenuButton(p: MenuButtonProps) {
  const m = useMenu({ defaultOpen: p.defaultOpen });
  const items = p.items || [];
  const iconOnly = p.children == null;
  const btn = (
    <button
      ref={m.btn}
      type="button"
      className={cx(
        'dl-btn',
        `dl-btn--${p.variant || 'secondary'}`,
        p.size === 'sm' && 'dl-btn--sm',
        iconOnly && 'dl-btn--icon',
      )}
      aria-haspopup="menu"
      aria-expanded={m.open ? 'true' : 'false'}
      aria-label={iconOnly ? p.label : undefined}
      onClick={m.toggle}
    >
      <Icon name={p.icon || 'more'} size={16} />
      {iconOnly ? null : p.children}
    </button>
  );
  return (
    <div className={cx('dl-menubtn', p.className)} ref={m.root} onKeyDown={m.onKeyDown}>
      {/* The tooltip stays mounted while the menu is open, so the button keeps its focus. */}
      {iconOnly ? (
        <Tooltip content={p.label} describe={false} open={m.open ? false : undefined}>
          {btn}
        </Tooltip>
      ) : (
        btn
      )}
      {m.open ? (
        <div
          className={cx('dl-menu', p.align === 'start' ? null : 'dl-menu--end', 'dl-menu--actions')}
          role="menu"
          aria-label={p.label}
        >
          {items.map((it, i) => {
            if ('separator' in it)
              // biome-ignore lint/suspicious/noArrayIndexKey: the items are a fixed list.
              return <div key={i} className="dl-menu__sep" role="separator" />;
            const off = !!it.disabledReason;
            return (
              <button
                // biome-ignore lint/suspicious/noArrayIndexKey: the items are a fixed list.
                key={i}
                type="button"
                role="menuitem"
                tabIndex={-1}
                className={cx('dl-menu__item', it.danger && 'dl-menu__item--danger')}
                aria-disabled={off ? 'true' : undefined}
                onClick={() => {
                  if (off) return;
                  m.set(false, true);
                  if (it.onSelect) it.onSelect();
                }}
              >
                {it.icon ? <Icon name={it.icon} size={16} /> : <span />}
                <span className="dl-menu__name">{it.label}</span>
                <span />
                {off ? <span className="dl-menu__why">{it.disabledReason}</span> : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
