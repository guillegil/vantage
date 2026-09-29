import { useState } from 'react';
import type { PinButtonProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { Tooltip } from '../Tooltip/Tooltip';

export function PinButton(p: PinButtonProps) {
  const [own, setOwn] = useState(!!p.defaultPinned);
  const pinned = p.pinned != null ? p.pinned : own;
  const disabled = !!p.disabledReason;
  const label = pinned ? 'Unpin run' : 'Pin run';
  const btn = (
    <button
      type="button"
      className={cx('dl-pin', p.className)}
      aria-pressed={pinned ? 'true' : 'false'}
      aria-disabled={disabled ? 'true' : undefined}
      aria-label={label}
      onClick={() => {
        if (disabled) return;
        if (p.pinned == null) setOwn(!pinned);
        if (p.onChange) p.onChange(!pinned);
      }}
    >
      <Icon name="pin" size={16} />
    </button>
  );
  // aria-disabled keeps it focusable, so the reason is shown on focus as well as on hover.
  return disabled ? (
    <Tooltip content={p.disabledReason}>{btn}</Tooltip>
  ) : (
    <Tooltip content={label} describe={false}>
      {btn}
    </Tooltip>
  );
}
