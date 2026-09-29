import type { CommandProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { useCopy } from '../lib/copy';
import { cx } from '../lib/cx';

export function Command(p: CommandProps) {
  const text = p.text != null ? p.text : (p.children ?? '');
  const [copied, copy] = useCopy(text);
  return (
    <span className={cx('dl-cmd', p.className)} data-copy-scope="">
      <span className="dl-cmd__text" dir="ltr">
        {p.prompt === false ? null : <span className="dl-cmd__prompt">$ </span>}
        <span data-copy-text="">{text}</span>
      </span>
      {p.copy === false ? null : (
        <button type="button" className="dl-cmd__copy" onClick={copy} aria-label="Copy command">
          <Icon name={copied ? 'check' : 'copy'} size={14} />
          {copied ? 'Copied' : 'Copy'}
        </button>
      )}
      {p.copy === false ? null : (
        <span className="dl-sr" aria-live="polite">
          {copied ? 'Copied' : ''}
        </span>
      )}
    </span>
  );
}
