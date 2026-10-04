import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DataTable } from './DataTable';

interface Row {
  id: string;
  name: string;
  seconds: string | null;
}

it('renders its columns and rows, a missing value marked', () => {
  render(
    <DataTable<Row>
      caption="Results"
      rowKey="id"
      columns={[
        { key: 'name', label: 'Test' },
        { key: 'seconds', label: 'Duration', align: 'num' },
      ]}
      rows={[{ id: 'a', name: 'test_a', seconds: null }]}
    />,
  );
  expect(screen.getByRole('table', { name: 'Results' })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Duration' })).toHaveClass('is-num');
  expect(screen.getByRole('cell', { name: 'test_a' })).toBeInTheDocument();
  expect(screen.getByTitle('Not recorded')).toHaveTextContent('—');
});

it('says so when there are no rows', () => {
  render(
    <DataTable<Row>
      columns={[{ key: 'name', label: 'Test' }]}
      rows={[]}
      empty="No results match."
    />,
  );
  expect(screen.getByRole('cell', { name: 'No results match.' })).toBeInTheDocument();
});

interface Item {
  id: string;
  name: string;
  seconds: number | null;
  bytes?: number;
}

const ITEMS: Item[] = [
  { id: 'a', name: 'test_b10', seconds: 2, bytes: 512 },
  { id: 'b', name: 'test_b2', seconds: null, bytes: 1_536 },
  { id: 'c', name: 'test_a', seconds: 30.5, bytes: 1_048_576 },
];
const names = () =>
  screen
    .getAllByRole('row')
    .slice(1)
    .map((r) => r.querySelector('td')?.textContent);

describe('DataTable', () => {
  it('formats a column’s values and aligns a figure column as figures', () => {
    render(
      <DataTable<Item>
        rowKey="id"
        columns={[
          { key: 'name', label: 'Test' },
          { key: 'seconds', label: 'Duration', format: 'seconds' },
          { key: 'bytes', label: 'Size', format: 'bytes' },
          { key: 'id', label: 'Id', format: 'text' },
        ]}
        rows={ITEMS}
      />,
    );
    expect(screen.getByRole('columnheader', { name: 'Duration' })).toHaveClass('is-num');
    expect(screen.getByRole('columnheader', { name: 'Id' })).not.toHaveClass('is-num');
    const third = screen.getAllByRole('row')[3] as HTMLElement;
    expect(Array.from(third.querySelectorAll('td')).map((c) => c.textContent)).toEqual([
      'test_a',
      '30.5 s',
      '1 MiB',
      'c',
    ]);
    expect(screen.getByRole('cell', { name: '1.5 KiB' })).toBeInTheDocument();
    expect(screen.getByTitle('Not recorded')).toHaveTextContent('—');
  });

  it('sorts by a sortable column, stable, empty cells last, and says the order', () => {
    render(
      <DataTable<Item>
        rowKey="id"
        columns={[
          { key: 'name', label: 'Test', sortable: true },
          { key: 'seconds', label: 'Duration', align: 'num', sortable: true },
        ]}
        rows={ITEMS}
      />,
    );
    const test = screen.getByRole('columnheader', { name: 'Test' });
    expect(test).toHaveAttribute('aria-sort', 'none');
    fireEvent.click(screen.getByRole('button', { name: 'Test' }));
    // Words A to Z first, numbers within them by value.
    expect(names()).toEqual(['test_a', 'test_b2', 'test_b10']);
    expect(test).toHaveAttribute('aria-sort', 'ascending');
    expect(screen.getByText('Sorted by Test, ascending')).toHaveAttribute('aria-live', 'polite');
    fireEvent.click(screen.getByRole('button', { name: 'Test' }));
    expect(names()).toEqual(['test_b10', 'test_b2', 'test_a']);
    // Figures largest first, the empty one last either way.
    fireEvent.click(screen.getByRole('button', { name: 'Duration' }));
    expect(names()).toEqual(['test_a', 'test_b10', 'test_b2']);
    expect(test).toHaveAttribute('aria-sort', 'none');
    fireEvent.click(screen.getByRole('button', { name: 'Duration' }));
    expect(names()).toEqual(['test_b10', 'test_a', 'test_b2']);
  });

  it('follows a controlled sort and reports the next one', () => {
    const onSortChange = vi.fn();
    render(
      <DataTable<Item>
        rowKey="id"
        sort={{ key: 'name', dir: 'desc' }}
        onSortChange={onSortChange}
        columns={[{ key: 'name', label: 'Test', sortable: true }]}
        rows={ITEMS}
      />,
    );
    expect(names()).toEqual(['test_b10', 'test_b2', 'test_a']);
    fireEvent.click(screen.getByRole('button', { name: 'Test' }));
    expect(onSortChange).toHaveBeenCalledWith({ key: 'name', dir: 'asc' });
    expect(names()).toEqual(['test_b10', 'test_b2', 'test_a']);
  });

  it('selects rows with checkboxes, all at once, and counts them in its foot', () => {
    const onSelectionChange = vi.fn();
    render(
      <DataTable<Item>
        rowKey="id"
        selectable="multiple"
        onSelectionChange={onSelectionChange}
        rowLabel={(r) => r.name}
        columns={[{ key: 'name', label: 'Test' }]}
        rows={ITEMS}
      />,
    );
    const all = screen.getByRole('checkbox', { name: 'Select all 3 rows' }) as HTMLInputElement;
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select test_a' }));
    expect(onSelectionChange).toHaveBeenLastCalledWith(['c']);
    expect(all.indeterminate).toBe(true);
    expect(screen.getAllByRole('row')[3]).toHaveClass('is-checked');
    expect(screen.getByText('3 rows, 1 selected')).toHaveClass('dl-table__foot');
    fireEvent.click(all);
    expect(onSelectionChange).toHaveBeenLastCalledWith(['a', 'b', 'c']);
    expect(all.checked).toBe(true);
    expect(all.indeterminate).toBe(false);
    fireEvent.click(all);
    expect(onSelectionChange).toHaveBeenLastCalledWith([]);
  });

  it('opens a row from a button in its first cell or a click on the row', () => {
    const onRowSelect = vi.fn();
    render(
      <DataTable<Item>
        rowKey="id"
        selected="b"
        onRowSelect={onRowSelect}
        columns={[
          { key: 'name', label: 'Test' },
          { key: 'id', label: 'Id' },
        ]}
        rows={ITEMS}
      />,
    );
    const button = screen.getByRole('button', { name: 'test_b2' });
    expect(button).toHaveClass('dl-table__rowbtn');
    expect(button).toHaveAttribute('aria-current', 'true');
    fireEvent.click(button);
    expect(onRowSelect).toHaveBeenCalledOnce();
    expect(onRowSelect).toHaveBeenLastCalledWith(ITEMS[1]);
    fireEvent.click(screen.getByRole('cell', { name: 'c' }));
    expect(onRowSelect).toHaveBeenLastCalledWith(ITEMS[2]);
    expect(screen.getAllByRole('row')[1]).toHaveClass('is-action');
  });

  it('scrolls in a fixed height, rendering only the rows in view past windowAfter', () => {
    const many = Array.from({ length: 500 }, (_, i) => ({
      id: `r${i}`,
      name: `test_${i}`,
      seconds: i,
    }));
    const { container } = render(
      <DataTable<Item>
        rowKey="id"
        caption="Results"
        maxHeight={360}
        noun="results"
        nounOne="result"
        columns={[{ key: 'name', label: 'Test' }]}
        rows={many}
      />,
    );
    const region = screen.getByRole('region', { name: 'Results' });
    expect(region).toHaveClass('dl-table-wrap--scroll');
    expect(region).toHaveStyle({ maxHeight: '360px' });
    const table = screen.getByRole('table');
    expect(table).toHaveClass('dl-table--windowed');
    expect(table).toHaveAttribute('aria-rowcount', '501');
    // 360 / 36 = 10 rows in view, and 16 more as a margin.
    const real = container.querySelectorAll('tbody tr:not(.dl-table__spacer)');
    expect(real).toHaveLength(26);
    expect(real[0]).toHaveAttribute('aria-rowindex', '2');
    expect(container.querySelector('.dl-table__spacer')).toHaveStyle({ height: `${474 * 36}px` });
    expect(screen.getByText('500 results')).toHaveClass('dl-table__foot');
  });

  it('keeps every row, and no foot, under windowAfter', () => {
    const { container } = render(
      <DataTable<Item>
        rowKey="id"
        maxHeight={360}
        columns={[{ key: 'name', label: 'Test' }]}
        rows={ITEMS}
      />,
    );
    expect(container.querySelectorAll('tbody tr')).toHaveLength(3);
    expect(container.querySelector('.dl-table__foot')).toBeNull();
    expect(screen.getByRole('table')).not.toHaveAttribute('aria-rowcount');
  });
});
