import { useState } from 'react';
import type { SegmentedControlProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { arrowNav } from '../lib/keys';

export function SegmentedControl(p: SegmentedControlProps) {
  const opts = p.options || [];
  const [own, setOwn] = useState(p.defaultValue != null ? p.defaultValue : opts[0]?.value);
  const value = p.value != null ? p.value : own;
  return (
    <div
      className={cx('dl-seg', p.className)}
      role="radiogroup"
      aria-label={p.label}
      onKeyDown={arrowNav}
    >
      {opts.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on ? 'true' : 'false'}
            tabIndex={on ? 0 : -1}
            className="dl-seg__opt"
            onClick={() => {
              if (p.value == null) setOwn(o.value);
              if (p.onChange) p.onChange(o.value);
            }}
          >
            {o.icon ? <Icon name={o.icon} size={14} /> : null}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
