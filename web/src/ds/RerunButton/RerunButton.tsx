import { Button } from '../Button/Button';
import type { RerunButtonProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { useCopy } from '../lib/copy';
import { cx } from '../lib/cx';
import { plural } from '../lib/format';
import { isHidden } from '../lib/visible';
import { Tooltip } from '../Tooltip/Tooltip';

// A character a terminal could act on as it is pasted, or that prints nothing or reorders what
// follows it: every control character, line breaks and tabs included, and every hidden one.
function isUnsafe(cp: number): boolean {
  return cp < 0x20 || isHidden(cp);
}

// Each byte of a character's UTF-8 as a three-digit octal escape, which every shell that reads
// $'...' takes the same way, whatever follows it.
function escapeBytes(ch: string): string {
  return Array.from(
    new TextEncoder().encode(ch),
    (b) => `\\${b.toString(8).padStart(3, '0')}`,
  ).join('');
}

// A node id quoted for a POSIX shell only where it needs it: brackets, spaces and quotes do. One
// holding a control or hidden character is written $'...', each such character as escaped bytes,
// so the command carries none: nothing in it acts as it is pasted, and what shows is what copies.
export function shellQuote(v: string): string {
  const s = String(v);
  if (/^[A-Za-z0-9_./:=@%+,-]+$/.test(s)) return s;
  let unsafe = false;
  for (const ch of s) if (isUnsafe(ch.codePointAt(0) ?? 0)) unsafe = true;
  if (!unsafe) return `'${s.replace(/'/g, "'\\''")}'`;
  let out = '';
  for (const ch of s) {
    const cp = ch.codePointAt(0) ?? 0;
    if (isUnsafe(cp)) out += escapeBytes(ch);
    else if (ch === '\\' || ch === "'") out += `\\${ch}`;
    else out += ch;
  }
  return `$'${out}'`;
}

// A node id as an argument pytest reads as a test: pytest takes a word starting with - as an
// option and one starting with @ as a file of arguments, so either is given as a path from here.
export function pytestArg(id: string): string {
  return /^[-@]/.test(id) ? `./${id}` : id;
}

export function rerunCommand(ids: string[]): string {
  return `pytest ${ids.map((id) => shellQuote(pytestArg(id))).join(' ')}`;
}

// What pytest's own @file splits a line at, as Python's str.splitlines does: a node id holding one
// of these can never be one line.
const LINE_BREAKS = new Set([0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029]);

function hasLineBreak(id: string): boolean {
  for (const ch of id) if (LINE_BREAKS.has(ch.codePointAt(0) ?? 0)) return true;
  return false;
}

// The file for pytest @file, one argument a line. It opens with --, so no line is ever an option,
// and gives each node id as pytestArg does, since pytest reads a line starting with @ as another
// file before -- applies; a node id holding a line break is left out, and counted.
export function rerunFile(ids: string[]): { text: string; kept: number; left: number } {
  const kept = ids.filter((id) => !hasLineBreak(id));
  return {
    text: `${['--', ...kept.map(pytestArg)].join('\n')}\n`,
    kept: kept.length,
    left: ids.length - kept.length,
  };
}

// Copies a pytest command that reruns exactly these tests. Past `limit` ids a command line gets
// unwieldy, so it offers a file of node ids for pytest's own @file argument instead.
export function RerunButton(p: RerunButtonProps) {
  const ids = p.nodeids || [];
  const limit = p.limit || 20;
  const file = p.fileName || 'rerun.txt';
  const cmd = p.command || rerunCommand(ids);
  const [copied, copy] = useCopy(cmd);
  if (!ids.length) return null;
  if (ids.length > limit) {
    const listed = rerunFile(ids);
    // A Blob's object URL, followed by an <a download>: no inline script, no string parsed as HTML
    // and no fetch, so the page's own policy allows it.
    const download = () => {
      const url = URL.createObjectURL(new Blob([listed.text], { type: 'text/plain' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = file;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    };
    return (
      <span className={cx('dl-rerun', p.className)}>
        {listed.kept ? (
          <>
            <Button size={p.size || 'sm'} icon="download" onClick={download}>
              {`Download ${plural(listed.kept, 'node id', 'node ids')}`}
            </Button>
            <span className="dl-rerun__hint">
              then run <code>{`pytest @${file}`}</code>
            </span>
          </>
        ) : null}
        {listed.left ? (
          <span className="dl-rerun__hint">
            {`${plural(listed.left, 'node id holds', 'node ids hold')} a line break, so the file leaves ${listed.left === 1 ? 'it' : 'them'} out`}
          </span>
        ) : null}
      </span>
    );
  }
  return (
    <span className={cx('dl-rerun', p.className)} data-copy-scope="">
      <span className="dl-sr" data-copy-text="">
        {cmd}
      </span>
      <Tooltip content={<code className="dl-rerun__tip">{cmd}</code>}>
        <button
          type="button"
          className={cx(
            'dl-btn',
            `dl-btn--${p.variant || 'secondary'}`,
            (p.size || 'sm') === 'sm' && 'dl-btn--sm',
          )}
          onClick={copy}
          aria-keyshortcuts={p.keyHint || undefined}
        >
          <Icon name={copied ? 'check' : 'terminal'} size={16} />
          {copied ? 'Copied' : p.children || 'Copy rerun command'}
          {p.keyHint ? (
            // biome-ignore lint/a11y/noAriaHiddenOnFocusable: a kbd takes no focus; aria-keyshortcuts already names the key.
            <kbd className="dl-kbd" aria-hidden="true">
              {p.keyHint}
            </kbd>
          ) : null}
        </button>
      </Tooltip>
      <span className="dl-sr" aria-live="polite">
        {copied ? `Copied ${cmd}` : ''}
      </span>
    </span>
  );
}
