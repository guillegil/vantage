import { useState } from 'react';
import type { TabsProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { fmtCount } from '../lib/format';
import { useUid } from '../lib/hooks';
import { arrowNav } from '../lib/keys';
import { visible } from '../lib/visible';

export function Tabs(p: TabsProps) {
  const tabs = p.tabs || [];
  const uid = useUid('dl-tabs');
  const prefix = p.idPrefix || `${uid}-`;
  const [own, setOwn] = useState(p.defaultValue != null ? p.defaultValue : tabs[0]?.id);
  const value = p.value != null ? p.value : own;
  // Tabs that carry their panel render it; the tablist and the panel then name each other.
  const owned = tabs.some((t) => t.panel !== undefined);
  const active = tabs.filter((t) => t.id === value)[0];
  const list = (
    <div
      className={cx('dl-tabs', !owned && p.className)}
      role="tablist"
      aria-label={p.label}
      onKeyDown={arrowNav}
    >
      {tabs.map((t) => {
        const on = t.id === value;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={prefix + t.id}
            aria-selected={on ? 'true' : 'false'}
            tabIndex={on ? 0 : -1}
            className="dl-tab"
            aria-controls={owned ? `${prefix}panel` : t.controls}
            onClick={() => {
              if (p.value == null) setOwn(t.id);
              if (p.onChange) p.onChange(t.id);
            }}
          >
            {visible(t.label)}
            {t.count != null ? (
              <span className={cx('dl-tab__count', t.alert && 'dl-tab__count--alert')}>
                {fmtCount(t.count)}
              </span>
            ) : null}
            {t.plugin ? (
              <Icon name="plug" size={14} title={`From ${t.plugin}`} className="dl-tab__plugin" />
            ) : null}
          </button>
        );
      })}
    </div>
  );
  if (!owned) return list;
  return (
    <div className={cx('dl-tabset', p.className)}>
      {list}
      {active ? (
        <div
          role="tabpanel"
          id={`${prefix}panel`}
          aria-labelledby={prefix + active.id}
          tabIndex={0}
          className="dl-tabpanel"
        >
          {active.panel}
        </div>
      ) : null}
    </div>
  );
}
