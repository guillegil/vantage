import type { ButtonHTMLAttributes, MouseEvent } from 'react';
import type { ButtonProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { Tooltip } from '../Tooltip/Tooltip';

export function Button(p: ButtonProps) {
  const {
    variant,
    size,
    icon,
    label,
    className,
    children,
    href,
    busy,
    busyLabel,
    disabledReason,
    ...rest
  } = p;
  const iconOnly = !!icon && (children == null || children === false);
  const unavailable = !!disabledReason || !!busy;
  const cls = cx(
    'dl-btn',
    `dl-btn--${variant || 'secondary'}`,
    size === 'sm' && 'dl-btn--sm',
    iconOnly && 'dl-btn--icon',
    busy && 'dl-btn--busy',
    className,
  );
  const kids = (
    <>
      {icon ? <Icon name={icon} size={16} /> : null}
      {iconOnly ? null : busy && busyLabel ? busyLabel : children}
      {busy ? <span className="dl-btn__cursor" aria-hidden="true" /> : null}
    </>
  );
  const a11y: ButtonHTMLAttributes<HTMLElement> & { 'aria-label'?: string } = {};
  if (iconOnly) a11y['aria-label'] = label;
  if (unavailable) {
    // aria-disabled, not disabled: the button stays focusable, so its reason can be read, and a click does nothing.
    a11y['aria-disabled'] = 'true';
    a11y.onClick = (e: MouseEvent) => e.preventDefault();
  }
  if (busy) a11y['aria-busy'] = 'true';
  const el = href ? (
    <a {...(rest as object)} {...a11y} className={cls} href={href}>
      {kids}
    </a>
  ) : (
    <button type="button" {...rest} {...a11y} className={cls}>
      {kids}
    </button>
  );
  if (disabledReason) return <Tooltip content={disabledReason}>{el}</Tooltip>;
  if (iconOnly && label)
    return (
      <Tooltip content={label} describe={false}>
        {el}
      </Tooltip>
    );
  return el;
}
