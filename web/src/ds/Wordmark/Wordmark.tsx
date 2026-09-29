import type { WordmarkProps } from '../contract';
import { cx } from '../lib/cx';

export function Wordmark(p: WordmarkProps) {
  const className = cx('dl-wordmark', p.className);
  return p.href ? (
    <a className={className} href={p.href}>
      vantage
    </a>
  ) : (
    <span className={className}>vantage</span>
  );
}
