import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { NodeId, parseNodeId } from './NodeId';

const ZWSP = String.fromCodePoint(0x200b);
const RLO = String.fromCodePoint(0x202e);

it('splits a node id into its path, classes, function and parameters', () => {
  expect(parseNodeId('tests/comms/test_uart.py::TestUart::test_dma[a::b]')).toEqual({
    dir: 'tests/comms/',
    file: 'test_uart.py',
    mids: ['TestUart'],
    fn: 'test_dma',
    param: '[a::b]',
  });
});

it('prints the whole id, left to right, the file and function marked', () => {
  const { container } = render(<NodeId value="tests/test_a.py::test_one[x]" />);
  const id = container.firstChild as HTMLElement;
  expect(id).toHaveTextContent('tests/test_a.py::test_one[x]');
  expect(id).toHaveAttribute('dir', 'ltr');
  expect(id).toHaveAttribute('title', 'tests/test_a.py::test_one[x]');
  expect(screen.getByText('test_a.py')).toHaveClass('dl-nodeid__file');
  expect(screen.getByText('test_one')).toHaveClass('dl-nodeid__fn');
});

it('gives way from the path when truncated, and links when given an address', () => {
  render(<NodeId value="tests/deep/test_a.py::test_one" truncate href="/r" />);
  const link = screen.getByRole('link');
  expect(link).toHaveTextContent('…/test_a.py::test_one');
  expect(link).toHaveAttribute('href', '/r');
});

it('says nothing was recorded for an empty id', () => {
  render(<NodeId value="" />);
  expect(screen.getByTitle('Not recorded')).toHaveTextContent('—');
});

it('shows a bidi control or invisible character as its code point, never obeying it', () => {
  const value = `tests/test_a.py::test_x[${RLO}evil${ZWSP}]`;
  const { container } = render(<NodeId value={value} />);
  const id = container.firstChild as HTMLElement;
  expect(id).toHaveTextContent('tests/test_a.py::test_x[U+202EevilU+200B]');
  for (const ch of [RLO, ZWSP]) expect(id.textContent).not.toContain(ch);
  expect(id.querySelectorAll('.dl-hidden-char')).toHaveLength(2);
  expect(id).toHaveAttribute('title', 'tests/test_a.py::test_x[⟨U+202E⟩evil⟨U+200B⟩]');
});

it('lets a long id break before its parameters, unless truncated', () => {
  const { container, rerender } = render(<NodeId value="tests/test_a.py::test_one[a-b]" />);
  const breaks = () => Array.from(container.querySelectorAll('wbr'));
  // After the directory, before "::", and before the parameters.
  expect(breaks()).toHaveLength(3);
  expect(breaks()[2]?.nextElementSibling).toHaveTextContent('[a-b]');
  rerender(<NodeId value="test_a.py::test_one[a-b]" />);
  expect(breaks()).toHaveLength(2);
  rerender(<NodeId value="tests/test_a.py::test_one[a-b]" truncate />);
  expect(breaks()).toHaveLength(0);
});
