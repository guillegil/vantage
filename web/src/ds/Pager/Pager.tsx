import { Button } from '../Button/Button';
import type { PagerProps } from '../contract';
import { cx } from '../lib/cx';
import { fmtCount, plural } from '../lib/format';

export interface PortedPagerProps extends PagerProps {
  /** The order the shown items are in, said before "More exist". Default "newest first". */
  order?: string;
}

export function Pager(p: PortedPagerProps) {
  const noun = p.noun || 'runs';
  const one = p.nounOne || noun.replace(/s$/, '');
  const size = p.pageSize || 50;
  const shown = p.shown || 0;
  let text: string;
  if (!shown && !p.hasMore) text = `No ${noun} match.`;
  else if (p.hasMore)
    text = `${plural(shown, one, noun)} shown, ${p.order || 'newest first'}. More exist.`;
  else text = shown === 1 ? `The only ${one} is shown.` : `All ${plural(shown, one, noun)} shown.`;
  // role=status: when a page loads, the new count is read out without moving focus.
  return (
    <div className={cx('dl-pager', p.className)}>
      <span role="status">{text}</span>
      {p.hasMore ? (
        <Button size="sm" onClick={p.onMore} busy={p.loading} busyLabel="Loading">
          {`Load ${fmtCount(size)} more`}
        </Button>
      ) : null}
    </div>
  );
}
