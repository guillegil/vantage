import { Command } from '../Command/Command';
import type { EmptyStateProps } from '../contract';
import { cx } from '../lib/cx';

export function EmptyState(p: EmptyStateProps) {
  const text = typeof p.children === 'string';
  return (
    <div className={cx('dl-empty', p.className)}>
      <svg
        className="dl-empty__line"
        viewBox="0 0 240 16"
        width={240}
        height={16}
        aria-hidden="true"
      >
        <rect className="dl-dotline__base" x={0} y={15} width={240} height={1} />
        <rect className="dl-cursor" x={0} y={0} width={6} height={16} />
      </svg>
      <h3 className="dl-empty__title">{p.title}</h3>
      {p.children ? (
        text ? (
          <p className="dl-empty__text">{p.children}</p>
        ) : (
          <div className="dl-empty__body">{p.children}</div>
        )
      ) : null}
      {p.command ? <Command text={p.command} /> : null}
      {p.action || null}
    </div>
  );
}
