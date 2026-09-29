import { type Key, type ReactNode, useRef } from 'react';
import type { DataTableColumn, DataTableProps } from '../contract';
import { cx } from '../lib/cx';
import { useOverflowFocus } from '../lib/hooks';

function alignClass<Row>(c: DataTableColumn<Row>): string | undefined {
  return c.align === 'num' ? 'is-num' : c.align === 'end' ? 'is-end' : undefined;
}

export function DataTable<Row>(p: DataTableProps<Row>) {
  const cols = p.columns || [];
  const rows = p.rows || [];
  const wrap = useRef<HTMLDivElement | null>(null);
  const over = useOverflowFocus(wrap);
  return (
    <div
      ref={wrap}
      className={cx('dl-table-wrap', p.className)}
      // A region only while it overflows, so a keyboard can scroll it.
      tabIndex={over ? 0 : undefined}
      role={over ? 'region' : undefined}
      aria-label={over ? p.caption || 'Table' : undefined}
    >
      <table className={cx('dl-table', p.dense && 'dl-table--dense')}>
        {p.caption ? <caption className="dl-sr">{p.caption}</caption> : null}
        <thead>
          <tr>
            {cols.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={alignClass(c)}
                style={c.width ? { width: c.width } : undefined}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((r, i) => {
              const key = (p.rowKey ? r[p.rowKey] : i) as Key;
              return (
                <tr
                  key={key}
                  className={p.selected != null && p.selected === key ? 'is-selected' : undefined}
                >
                  {cols.map((c) => {
                    let v: ReactNode = c.render
                      ? c.render(r)
                      : ((r as Record<string, unknown>)[c.key] as ReactNode);
                    if (!c.render && (v == null || v === ''))
                      v = (
                        <span className="dl-meta__none" title="Not recorded">
                          —
                        </span>
                      );
                    return (
                      <td key={c.key} className={alignClass(c)}>
                        {v}
                      </td>
                    );
                  })}
                </tr>
              );
            })
          ) : (
            <tr>
              <td className="dl-table__empty" colSpan={cols.length || 1}>
                {p.empty || 'Nothing to show.'}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
