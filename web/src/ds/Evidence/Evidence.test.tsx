import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Evidence } from './Evidence';

const ZWSP = String.fromCodePoint(0x200b);
const RLO = String.fromCodePoint(0x202e);
const REPL = String.fromCodePoint(0xfffd);

const TB = [
  'tests/power/test_rails.py:88: in test_rail_under_load',
  '>   verify.approx(vout, nominal)',
  'E   AssertionError: expected 3.3V, got 3.38V',
  '',
  'tests/power/test_rails.py:88: AssertionError',
].join('\n');

describe('Evidence', () => {
  it('prints a traceback verbatim, marking pytest’s own lines', () => {
    const { container } = render(<Evidence text={TB} meta="call phase" level={2} />);
    expect(screen.getByRole('heading', { level: 2, name: 'Traceback' })).toBeInTheDocument();
    expect(screen.getByText('call phase')).toHaveClass('dl-evidence__meta');
    const body = container.querySelector('pre') as HTMLElement;
    expect(body.textContent).toBe(TB);
    expect(body).toHaveAttribute('dir', 'ltr');
    expect(body).toHaveAttribute('tabindex', '0');
    expect(body).toHaveAccessibleName('Traceback');
    expect(screen.getByText(/^E\s+AssertionError/)).toHaveClass('dl-ev--e');
    expect(screen.getByText(/^>\s+verify/)).toHaveClass('dl-ev--src');
    expect(screen.getAllByText(/^tests\/power\/test_rails\.py:88:/)[0]).toHaveClass('dl-ev--loc');
  });

  it('marks nothing in captured output', () => {
    const { container } = render(
      <Evidence title="Captured stdout" kind="output" text={'E   not pytest\n> nor this'} />,
    );
    expect(container.querySelector('.dl-ev--e, .dl-ev--src')).toBeNull();
    expect(container.querySelector('pre')?.textContent).toBe('E   not pytest\n> nor this');
  });

  it('says it was truncated at capture and what was kept, and shows its notice', () => {
    render(
      <Evidence
        text="x"
        truncated="Only the first 64 KiB was kept."
        notice="May contain any value a test printed or asserted, credentials included."
      />,
    );
    expect(screen.getByText('truncated at capture')).toHaveClass('dl-tag--warning');
    expect(screen.getByText('Only the first 64 KiB was kept.')).toBeInTheDocument();
    expect(screen.getByText(/credentials included/)).toBeInTheDocument();
  });

  it('wraps, expands and copies the text as recorded', async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const text = `out${RLO}put`;
    const { container } = render(<Evidence title="Captured stderr" kind="output" text={text} />);
    const body = container.querySelector('pre') as HTMLElement;
    fireEvent.click(screen.getByRole('button', { name: 'Wrap lines' }));
    expect(body).toHaveClass('dl-evidence__body--wrap');
    expect(screen.getByRole('button', { name: 'Wrap lines' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    expect(body).toHaveClass('dl-evidence__body--full');
    expect(screen.getByRole('button', { name: 'Collapse' })).toHaveAttribute(
      'aria-controls',
      body.id,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Copy captured stderr' }));
    expect(writeText).toHaveBeenCalledWith(text);
    expect(await screen.findAllByText('Copied')).not.toHaveLength(0);
  });

  it('leaves out its controls on request', () => {
    render(<Evidence text="x" controls={false} />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('shows a bidi control or invisible character as its code point', () => {
    const { container } = render(
      <Evidence text={`E   assert "${RLO}abc" == ""\nok${ZWSP}${REPL}`} />,
    );
    const body = container.querySelector('pre') as HTMLElement;
    expect(body.textContent).toBe('E   assert "U+202Eabc" == ""\nokU+200BU+FFFD');
    expect(body.querySelectorAll('.dl-hidden-char')).toHaveLength(3);
  });

  it('shows one in a meta line too, which may name a recorded path', () => {
    const { container } = render(
      <Evidence text="x" meta={`call phase · tests/${RLO}yp.nimda_tset:12`} />,
    );
    const head = container.querySelector('.dl-evidence__head') as HTMLElement;
    expect(head.textContent).toContain('call phase · tests/U+202Eyp.nimda_tset:12');
    expect(head.textContent).not.toContain(RLO);
    expect(head.querySelectorAll('.dl-hidden-char')).toHaveLength(1);
  });
});
