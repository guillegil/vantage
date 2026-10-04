import type { CommitRefProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { firstChars, visible, visibleText } from '../lib/visible';

export function CommitRef(p: CommitRefProps) {
  if (!p.branch && !p.sha)
    return (
      <span className={cx('dl-commit', p.className)} title="The plugin found no git repository">
        no commit recorded
      </span>
    );
  // Branch, sha and subject are as recorded: characters that print nothing or reorder text show as their code points.
  return (
    <span className={cx('dl-commit', p.className)}>
      {p.branch ? (
        <span className="dl-commit__ref" title={`Branch ${visibleText(p.branch)}`} dir="ltr">
          <Icon name="branch" size={14} />
          <span className="dl-commit__text">{visible(p.branch)}</span>
        </span>
      ) : null}
      {p.sha ? (
        <span className="dl-commit__ref" title={`Commit ${visibleText(p.sha)}`} dir="ltr">
          <Icon name="commit" size={14} />
          <span className="dl-commit__text">{visible(firstChars(p.sha, 7))}</span>
        </span>
      ) : null}
      {p.dirty ? (
        <span className="dl-tag dl-tag--warning" title="The working tree had uncommitted changes">
          dirty
        </span>
      ) : null}
      {p.subject ? (
        <bdi className="dl-commit__subject" title={visibleText(p.subject)}>
          {visible(p.subject)}
        </bdi>
      ) : null}
    </span>
  );
}
