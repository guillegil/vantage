import { useLayoutEffect, useRef } from 'react';
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
  const menuRef = useRef<HTMLDivElement | null>(null);
  // The menu is fixed to the viewport beside its button, so a table or panel that scrolls or clips never
  // cuts it off. It opens below the button, or above it where there is no room, and closes on scroll.
  // biome-ignore lint/correctness/useExhaustiveDependencies: placed each time it opens, as the design system does.
  useLayoutEffect(() => {
    const el = menuRef.current;
    const b = m.btn.current;
    if (!m.open || !el || !b) return undefined;
    el.style.position = 'fixed';
    el.style.insetInlineStart = 'auto';
    el.style.insetInlineEnd = 'auto';
    el.style.left = '0px';
    el.style.top = '0px';
    // Where 0,0 lands is the containing block's origin, which a transformed ancestor can move.
    const o = el.getBoundingClientRect();
    const r = b.getBoundingClientRect();
    const w = el.offsetWidth;
    const ht = el.offsetHeight;
    const rtl = getComputedStyle(el).direction === 'rtl';
    const toStart = p.align === 'start';
    let left = toStart !== rtl ? r.left : r.right - w;
    left = Math.max(8, Math.min(window.innerWidth - w - 8, left));
    let top = r.bottom + 4;
    if (top + ht > window.innerHeight - 8 && r.top - ht - 4 >= 8) top = r.top - ht - 4;
    el.style.left = `${left - o.left}px`;
    el.style.top = `${top - o.top}px`;
    function onScroll(e: Event) {
      const t = e.target;
      if (!(t instanceof Node && t.nodeType === 1 && el?.contains(t))) m.set(false);
    }
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
    };
  }, [m.open]);
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
          ref={menuRef}
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
