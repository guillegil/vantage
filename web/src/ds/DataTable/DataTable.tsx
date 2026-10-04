import {
  type Key,
  type MouseEvent,
  type ReactNode,
  type UIEvent,
  useEffect,
  useRef,
  useState,
} from 'react';
import type { DataTableColumn, DataTableProps, DataTableSort } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { fmtCount, fmtValue, plural } from '../lib/format';
import { useOverflowFocus } from '../lib/hooks';

function isNum<Row>(c: DataTableColumn<Row>): boolean {
  return c.align === 'num' || (!c.align && !!c.format && c.format !== 'text');
}
function alignClass<Row>(c: DataTableColumn<Row>): string | undefined {
  return isNum(c) ? 'is-num' : c.align === 'end' ? 'is-end' : undefined;
}
function nameOf<Row>(c: DataTableColumn<Row>): string {
  return typeof c.label === 'string' ? c.label : c.key;
}
function empty(v: unknown): boolean {
  return v == null || v === '';
}

export function DataTable<Row>(p: DataTableProps<Row>) {
  const cols = p.columns || [];
  const rows = p.rows || [];
  const wrap = useRef<HTMLDivElement | null>(null);
  const over = useOverflowFocus(wrap);
  const rowProbe = useRef<HTMLTableRowElement | null>(null);
  const [ownSort, setOwnSort] = useState<DataTableSort | null>(p.defaultSort || null);
  const sort = p.sort !== undefined ? p.sort : ownSort;
  const [ownKeys, setOwnKeys] = useState<unknown[]>(p.defaultSelectedKeys || []);
  const selKeys = p.selectedKeys || ownKeys;
  const [scrollTop, setScrollTop] = useState(0);
  const [rowH, setRowH] = useState(p.dense ? 28 : 36);
  const [said, setSaid] = useState('');
  const allBox = useRef<HTMLInputElement | null>(null);
  const raf = useRef(0);
  const multi = p.selectable === 'multiple';
  const onRowSelect = p.onRowSelect;
  const single = typeof onRowSelect === 'function';
  function keyOf(r: Row, i: number): unknown {
    return p.rowKey ? r[p.rowKey] : i;
  }
  // Sorting is stable and puts empty cells last whichever way it runs.
  const view = rows.map((r, i) => ({ r, i }));
  const sortCol = sort?.key ? cols.find((c) => c.key === sort.key) : undefined;
  if (sort && sortCol) {
    const val =
      sortCol.sortValue || ((r: Row) => (r as Record<string, unknown>)[sortCol.key] as unknown);
    const dir = sort.dir === 'desc' ? -1 : 1;
    view.sort((a, b) => {
      const x = val(a.r);
      const y = val(b.r);
      const xn = empty(x);
      const yn = empty(y);
      if (xn || yn) return xn && yn ? a.i - b.i : xn ? 1 : -1;
      const c =
        typeof x === 'number' && typeof y === 'number'
          ? x - y
          : String(x).localeCompare(String(y), 'en', { numeric: true });
      return c ? c * dir : a.i - b.i;
    });
  }
  function setSort(c: DataTableColumn<Row>) {
    const next: DataTableSort =
      !sort || sort.key !== c.key
        ? { key: c.key, dir: c.defaultDir || (isNum(c) ? 'desc' : 'asc') }
        : { key: c.key, dir: sort.dir === 'asc' ? 'desc' : 'asc' };
    if (p.sort === undefined) setOwnSort(next);
    if (p.onSortChange) p.onSortChange(next);
    setSaid(`Sorted by ${nameOf(c)}, ${next.dir === 'asc' ? 'ascending' : 'descending'}`);
  }
  function setSelected(keys: unknown[]) {
    if (!p.selectedKeys) setOwnKeys(keys);
    if (p.onSelectionChange) p.onSelectionChange(keys);
  }
  // Keys are compared as the design system's object of keys compares them: by their text.
  const selSet = new Set(selKeys.map(String));
  // Past windowAfter rows in a table with a height, only the rows in view (and a margin) are in the page.
  const windowed = !!p.maxHeight && view.length > (p.windowAfter || 100);
  let start = 0;
  let end = view.length;
  if (windowed) {
    const visibleRows = Math.ceil((typeof p.maxHeight === 'number' ? p.maxHeight : 480) / rowH);
    start = Math.max(0, Math.floor(scrollTop / rowH) - 8);
    end = Math.min(view.length, start + visibleRows + 16);
  }
  useEffect(() => {
    // The windowing arithmetic uses the height a row actually renders at.
    const tr = rowProbe.current;
    if (tr && windowed) {
      const hh = tr.getBoundingClientRect().height;
      if (hh && Math.abs(hh - rowH) > 0.5) setRowH(hh);
    }
    if (allBox.current)
      allBox.current.indeterminate = selKeys.length > 0 && selKeys.length < rows.length;
  });
  function onScroll(e: UIEvent<HTMLDivElement>) {
    if (!windowed) return;
    const el = e.currentTarget;
    if (!raf.current)
      raf.current = requestAnimationFrame(() => {
        raf.current = 0;
        setScrollTop(el.scrollTop);
      });
  }
  const colCount = cols.length + (multi ? 1 : 0);
  const head = (
    <tr>
      {multi ? (
        <th key="_sel" scope="col" className="dl-table__check">
          <input
            ref={allBox}
            type="checkbox"
            aria-label={`Select all ${plural(rows.length, 'row', 'rows')}`}
            checked={rows.length > 0 && selKeys.length === rows.length}
            onChange={(e) => setSelected(e.target.checked ? rows.map(keyOf) : [])}
          />
        </th>
      ) : null}
      {cols.map((c) => {
        const on = !!sortCol && sortCol.key === c.key;
        const ariaSort = c.sortable
          ? on
            ? sort?.dir === 'asc'
              ? 'ascending'
              : 'descending'
            : 'none'
          : undefined;
        return (
          <th
            key={c.key}
            scope="col"
            className={alignClass(c)}
            style={c.width ? { width: c.width } : undefined}
            aria-sort={ariaSort}
          >
            {c.sortable ? (
              <button
                type="button"
                className={cx('dl-table__sort', on && 'is-on')}
                onClick={() => setSort(c)}
              >
                <span>{c.label}</span>
                <Icon
                  name={on ? (sort?.dir === 'asc' ? 'chevron-up' : 'chevron-down') : 'chevrons'}
                  size={14}
                />
              </button>
            ) : (
              c.label
            )}
          </th>
        );
      })}
    </tr>
  );
  function cell(c: DataTableColumn<Row>, r: Row): ReactNode {
    const v = c.render ? c.render(r) : (r as Record<string, unknown>)[c.key];
    if (!c.render && empty(v))
      return (
        <span className="dl-meta__none" title="Not recorded">
          —
        </span>
      );
    if (!c.render && c.format) return fmtValue(v, c.format, c.unit);
    return v as ReactNode;
  }
  function row(item: { r: Row; i: number }, n: number) {
    const r = item.r;
    const key = keyOf(r, item.i);
    const picked = p.selected != null && p.selected === key;
    const checked = selSet.has(String(key));
    return (
      <tr
        key={key as Key}
        ref={n === 0 ? rowProbe : undefined}
        aria-rowindex={windowed ? start + n + 2 : undefined}
        className={
          cx(picked && 'is-selected', checked && 'is-checked', single && 'is-action') || undefined
        }
        onClick={
          onRowSelect
            ? (e: MouseEvent<HTMLTableRowElement>) => {
                const t = e.target as Element;
                if (!t.closest('a,button,input,select,textarea,label')) onRowSelect(r);
              }
            : undefined
        }
      >
        {multi ? (
          <td className="dl-table__check">
            <input
              type="checkbox"
              checked={checked}
              aria-label={`Select ${p.rowLabel ? p.rowLabel(r) : String(key)}`}
              onChange={(e) =>
                setSelected(
                  e.target.checked ? selKeys.concat([key]) : selKeys.filter((k) => k !== key),
                )
              }
            />
          </td>
        ) : null}
        {cols.map((c, ci) => {
          let content = cell(c, r);
          // A row that opens something is a button in its first cell, so the keyboard reaches it too.
          if (onRowSelect && ci === 0)
            content = (
              <button
                type="button"
                className="dl-table__rowbtn"
                aria-current={picked ? 'true' : undefined}
                onClick={() => onRowSelect(r)}
              >
                {content}
              </button>
            );
          return (
            <td key={c.key} className={alignClass(c)}>
              {content}
            </td>
          );
        })}
      </tr>
    );
  }
  const body = view.length
    ? [
        ...(windowed && start > 0
          ? [
              // biome-ignore lint/a11y/noAriaHiddenOnFocusable: a spacer row holds nothing to focus; it only keeps the scroll height.
              <tr
                key="_top"
                aria-hidden="true"
                className="dl-table__spacer"
                style={{ height: start * rowH }}
              >
                <td colSpan={colCount} />
              </tr>,
            ]
          : []),
        ...view.slice(start, end).map(row),
        ...(windowed && end < view.length
          ? [
              // biome-ignore lint/a11y/noAriaHiddenOnFocusable: a spacer row holds nothing to focus; it only keeps the scroll height.
              <tr
                key="_bot"
                aria-hidden="true"
                className="dl-table__spacer"
                style={{ height: (view.length - end) * rowH }}
              >
                <td colSpan={colCount} />
              </tr>,
            ]
          : []),
      ]
    : [
        <tr key="_none">
          <td className="dl-table__empty" colSpan={colCount}>
            {p.empty || 'Nothing to show.'}
          </td>
        </tr>,
      ];
  const scrolls = !!p.maxHeight;
  const foot =
    p.footer !== undefined ? (
      p.footer
    ) : windowed || multi ? (
      <>
        {plural(rows.length, p.nounOne || 'row', p.noun || 'rows')}
        {multi && selKeys.length ? `, ${fmtCount(selKeys.length)} selected` : ''}
        {sort && sortCol
          ? `, sorted by ${nameOf(sortCol)}${sort.dir === 'asc' ? ', ascending' : ', descending'}`
          : ''}
      </>
    ) : null;
  return (
    <div className={cx('dl-table-box', p.className)}>
      <div
        ref={wrap}
        className={cx('dl-table-wrap', scrolls && 'dl-table-wrap--scroll')}
        style={scrolls ? { maxHeight: p.maxHeight } : undefined}
        onScroll={onScroll}
        // A region while it overflows or has a height, so a keyboard can scroll it.
        tabIndex={over || scrolls ? 0 : undefined}
        role={over || scrolls ? 'region' : undefined}
        aria-label={over || scrolls ? p.caption || 'Table' : undefined}
      >
        <table
          className={cx('dl-table', p.dense && 'dl-table--dense', windowed && 'dl-table--windowed')}
          aria-rowcount={windowed ? view.length + 1 : undefined}
        >
          {p.caption ? <caption className="dl-sr">{p.caption}</caption> : null}
          <thead>{head}</thead>
          <tbody>{body}</tbody>
        </table>
      </div>
      {foot ? <div className="dl-table__foot">{foot}</div> : null}
      <span className="dl-sr" aria-live="polite">
        {said}
      </span>
    </div>
  );
}
