import type { IconName, NoticeProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';

const NOTICE_ICON: Record<string, IconName> = {
  info: 'info',
  warning: 'warning',
  danger: 'alert',
};

export function Notice(p: NoticeProps) {
  const tone = p.tone || 'info';
  return (
    <div
      className={cx('dl-notice', `dl-notice--${tone}`, p.className)}
      role={tone === 'danger' ? 'alert' : undefined}
    >
      <Icon name={NOTICE_ICON[tone] || 'info'} size={16} />
      <div className="dl-notice__text">
        {p.title ? <span className="dl-notice__title">{p.title}</span> : null}
        {p.children ? <span>{p.children}</span> : null}
      </div>
      {p.action ? <div className="dl-notice__action">{p.action}</div> : null}
    </div>
  );
}
