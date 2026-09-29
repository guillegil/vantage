import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
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
