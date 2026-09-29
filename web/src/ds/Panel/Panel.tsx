import { createElement } from 'react';
import type { PanelProps } from '../contract';
import { cx } from '../lib/cx';

export function Panel(p: PanelProps) {
  const level = Math.min(6, Math.max(1, p.level || 2));
  return (
    <section className={cx('dl-panel', p.flush && 'dl-panel--flush', p.className)} id={p.id}>
      {p.title || p.actions ? (
        <div className="dl-panel__head">
          {p.title ? createElement(`h${level}`, { className: 'dl-panel__title' }, p.title) : null}
          {p.actions ? <div className="dl-panel__actions">{p.actions}</div> : null}
        </div>
      ) : null}
      <div className="dl-panel__body">{p.children}</div>
    </section>
  );
}
