import { Button } from '../Button/Button';
import type { RerunButtonProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { useCopy } from '../lib/copy';
import { cx } from '../lib/cx';
import { plural } from '../lib/format';
import { Tooltip } from '../Tooltip/Tooltip';

// A node id quoted for a POSIX shell only where it needs it: brackets, spaces and quotes do.
export function shellQuote(v: string): string {
  const s = String(v);
  return /^[A-Za-z0-9_./:=@%+,-]+$/.test(s) ? s : `'${s.replace(/'/g, "'\\''")}'`;
}

export function rerunCommand(ids: string[]): string {
  return `pytest ${ids.map(shellQuote).join(' ')}`;
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
    // A Blob's object URL, followed by an <a download>: no inline script, no string parsed as HTML
    // and no fetch, so the page's own policy allows it.
    const download = () => {
      const url = URL.createObjectURL(new Blob([`${ids.join('\n')}\n`], { type: 'text/plain' }));
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
        <Button size={p.size || 'sm'} icon="download" onClick={download}>
          {`Download ${plural(ids.length, 'node id', 'node ids')}`}
        </Button>
        <span className="dl-rerun__hint">
          then run <code>{`pytest @${file}`}</code>
        </span>
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
