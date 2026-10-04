import type { MetaListProps } from '../contract';
import { cx } from '../lib/cx';
import { visible } from '../lib/visible';

export function MetaList(p: MetaListProps) {
  const items = p.items || [];
  if (!items.length)
    return <p className="dl-meta__none">{p.emptyText || 'No metadata recorded.'}</p>;
  return (
    <dl className={cx('dl-meta', p.className)}>
      {items.flatMap((it, i) => {
        const missing = it.value == null || it.value === '';
        return [
          // biome-ignore lint/suspicious/noArrayIndexKey: a key may repeat across sources, so its place is its identity.
          <dt key={`k${i}`}>
            {visible(it.key)}
            {it.source ? <span className="dl-tag">{it.source}</span> : null}
          </dt>,
          // biome-ignore lint/suspicious/noArrayIndexKey: paired with the term above.
          <dd key={`v${i}`}>
            {missing ? (
              <span className="dl-meta__none" title="Not recorded">
                —
              </span>
            ) : (
              visible(String(it.value))
            )}
          </dd>,
        ];
      })}
    </dl>
  );
}
