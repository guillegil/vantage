import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { MetaList } from './MetaList';

it('lists each key and value, a missing value marked', () => {
  const { container } = render(
    <MetaList
      items={[
        { key: 'rig', value: 'bench-02' },
        { key: 'os', value: null, source: 'file not read' },
      ]}
    />,
  );
  const terms = container.querySelectorAll('dt');
  expect(terms[0]).toHaveTextContent('rig');
  expect(container.querySelectorAll('dd')[0]).toHaveTextContent('bench-02');
  expect(terms[1]).toHaveTextContent('osfile not read');
  expect(screen.getByTitle('Not recorded')).toHaveTextContent('—');
});

it('says when there is none', () => {
  render(<MetaList items={[]} emptyText="This run reported no metadata" />);
  expect(screen.getByText('This run reported no metadata')).toBeInTheDocument();
});

it('shows hidden characters in a recorded key and value as code points', () => {
  const zwsp = String.fromCodePoint(0x200b);
  const { container } = render(
    <MetaList items={[{ key: `rig${zwsp}`, value: `bench${zwsp}02` }]} />,
  );
  expect(container.querySelector('dt')).toHaveTextContent('rigU+200B');
  expect(container.querySelector('dd')).toHaveTextContent('benchU+200B02');
  expect(container.querySelectorAll('.dl-hidden-char')).toHaveLength(2);
  expect(container.textContent).not.toContain(zwsp);
});
