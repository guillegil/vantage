import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RerunButton, rerunCommand, shellQuote } from './RerunButton';

const UART = 'tests/comms/test_uart.py::test_framing_at_921600';
const RAIL = 'tests/power/test_rails.py::test_rail_ripple[3V3]';

function ids(n: number): string[] {
  return Array.from({ length: n }, (_, i) => `tests/test_many.py::test_case[${i}]`);
}

function stubClipboard() {
  const writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  return writeText;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('the command', () => {
  it('quotes a node id for a POSIX shell only where it needs it', () => {
    expect(shellQuote(UART)).toBe(UART);
    expect(shellQuote('tests/a-b/test_x.py::test_y@2,3+4=5%')).toBe(
      'tests/a-b/test_x.py::test_y@2,3+4=5%',
    );
    expect(shellQuote(RAIL)).toBe(`'${RAIL}'`);
    expect(shellQuote('tests/test_a.py::test_x[a b]')).toBe("'tests/test_a.py::test_x[a b]'");
    expect(shellQuote('tests/test_a.py::test_x[$HOME]')).toBe("'tests/test_a.py::test_x[$HOME]'");
    expect(shellQuote("tests/test_a.py::test_x[it's]")).toBe("'tests/test_a.py::test_x[it'\\''s]'");
    expect(shellQuote('tests/test_a.py::test_x["q"]')).toBe(`'tests/test_a.py::test_x["q"]'`);
    expect(shellQuote('')).toBe("''");
  });

  it('runs pytest on exactly the given tests and adds no --vantage', () => {
    expect(rerunCommand([UART, RAIL])).toBe(`pytest ${UART} '${RAIL}'`);
    expect(rerunCommand([UART])).not.toContain('--vantage');
  });
});

describe('copying', () => {
  it('shows the command as its tooltip and description, and copies it', async () => {
    const writeText = stubClipboard();
    render(<RerunButton nodeids={[UART, RAIL]}>Copy rerun of 2 new failures</RerunButton>);
    const cmd = `pytest ${UART} '${RAIL}'`;
    const button = screen.getByRole('button', { name: 'Copy rerun of 2 new failures' });
    expect(button).toHaveAccessibleDescription(cmd);
    expect(button).toHaveClass('dl-btn', 'dl-btn--secondary', 'dl-btn--sm');
    expect(button).not.toHaveAttribute('aria-keyshortcuts');
    expect(screen.getByRole('tooltip', { hidden: true })).toHaveTextContent(cmd);
    const live = document.querySelector('[aria-live="polite"]');
    expect(live).toHaveTextContent(/^$/);
    fireEvent.click(button);
    expect(writeText).toHaveBeenCalledWith(cmd);
    expect(cmd).not.toContain('--vantage');
    expect(await screen.findByRole('button', { name: 'Copied' })).toBe(button);
    expect(live).toHaveTextContent(`Copied ${cmd}`);
  });

  it('says Copied only for a moment', async () => {
    stubClipboard();
    vi.useFakeTimers();
    render(<RerunButton nodeids={[UART]} />);
    const button = screen.getByRole('button', { name: 'Copy rerun command' });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(button).toHaveTextContent('Copied');
    act(() => {
      vi.advanceTimersByTime(1600);
    });
    expect(button).toHaveTextContent('Copy rerun command');
    expect(document.querySelector('[aria-live="polite"]')).toHaveTextContent(/^$/);
  });

  it('copies a command it is given in place of its own', () => {
    const writeText = stubClipboard();
    render(<RerunButton nodeids={[UART]} command="python -m pytest -p no:cacheprovider x" />);
    fireEvent.click(screen.getByRole('button'));
    expect(writeText).toHaveBeenCalledWith('python -m pytest -p no:cacheprovider x');
  });

  it('prints its key inside the button and sets it as the shortcut', () => {
    render(
      <RerunButton nodeids={[UART]} variant="primary" keyHint="c" size="md">
        Copy rerun command
      </RerunButton>,
    );
    const button = screen.getByRole('button', { name: 'Copy rerun command' });
    expect(button).toHaveAttribute('aria-keyshortcuts', 'c');
    expect(button).toHaveClass('dl-btn--primary');
    expect(button).not.toHaveClass('dl-btn--sm');
    const kbd = button.querySelector('kbd');
    expect(kbd).toHaveTextContent('c');
    expect(kbd).toHaveClass('dl-kbd');
    expect(kbd).toHaveAttribute('aria-hidden', 'true');
  });

  it('renders nothing without node ids', () => {
    const { container } = render(<RerunButton nodeids={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('past its limit', () => {
  function stubDownload() {
    const blobs: Blob[] = [];
    const create = vi.fn((b: Blob) => {
      blobs.push(b);
      return 'blob:http://localhost/0d7e';
    });
    const revoke = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: create, configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revoke, configurable: true });
    const clicked: { href: string; download: string; attached: boolean }[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push({
        href: this.getAttribute('href') ?? '',
        download: this.download,
        attached: document.body.contains(this),
      });
    });
    return { blobs, revoke, clicked };
  }

  it('copies a command for 20 node ids', () => {
    render(<RerunButton nodeids={ids(20)} fileName="new-failures.txt" />);
    expect(screen.getByRole('button', { name: 'Copy rerun command' })).toBeInTheDocument();
    expect(screen.queryByText(/Download/)).toBeNull();
  });

  it('offers a file of 21 for pytest @file, one node id a line', async () => {
    const { blobs, revoke, clicked } = stubDownload();
    vi.useFakeTimers();
    const list = ids(21);
    const { container } = render(<RerunButton nodeids={list} fileName="new-failures.txt" />);
    expect(container.querySelector('.dl-rerun__hint')).toHaveTextContent(
      'then run pytest @new-failures.txt',
    );
    expect(container.querySelector('.dl-rerun__hint code')).toHaveTextContent(
      'pytest @new-failures.txt',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download 21 node ids' }));
    expect(clicked).toEqual([
      { href: 'blob:http://localhost/0d7e', download: 'new-failures.txt', attached: true },
    ]);
    expect(document.querySelector('a[download]')).toBeNull();
    expect(blobs).toHaveLength(1);
    expect(blobs[0]?.type).toBe('text/plain');
    expect(await blobs[0]?.text()).toBe(`${list.join('\n')}\n`);
    expect(revoke).not.toHaveBeenCalled();
    act(() => {
      vi.runAllTimers();
    });
    expect(revoke).toHaveBeenCalledWith('blob:http://localhost/0d7e');
  });

  it('takes its own limit, and names its file rerun.txt by default', () => {
    const { clicked } = stubDownload();
    const { container } = render(<RerunButton nodeids={[UART, RAIL, 'tests/c.py::t']} limit={2} />);
    expect(container.querySelector('.dl-rerun__hint')).toHaveTextContent('pytest @rerun.txt');
    fireEvent.click(screen.getByRole('button', { name: 'Download 3 node ids' }));
    expect(clicked[0]?.download).toBe('rerun.txt');
  });
});
