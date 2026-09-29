import type { CommitRefProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';

export function CommitRef(p: CommitRefProps) {
  if (!p.branch && !p.sha)
    return (
      <span className={cx('dl-commit', p.className)} title="The plugin found no git repository">
        no commit recorded
      </span>
    );
  return (
    <span className={cx('dl-commit', p.className)}>
      {p.branch ? (
        <span className="dl-commit__ref" title={`Branch ${p.branch}`} dir="ltr">
          <Icon name="branch" size={14} />
          <span className="dl-commit__text">{p.branch}</span>
        </span>
      ) : null}
      {p.sha ? (
        <span className="dl-commit__ref" title={`Commit ${p.sha}`} dir="ltr">
          <Icon name="commit" size={14} />
          <span className="dl-commit__text">{p.sha.slice(0, 7)}</span>
        </span>
      ) : null}
      {p.dirty ? (
        <span className="dl-tag dl-tag--warning" title="The working tree had uncommitted changes">
          dirty
        </span>
      ) : null}
      {p.subject ? (
        <span className="dl-commit__subject" title={p.subject}>
          {p.subject}
        </span>
      ) : null}
    </span>
  );
}
