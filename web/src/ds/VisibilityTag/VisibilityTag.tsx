import type { VisibilityTagProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';

export function VisibilityTag(p: VisibilityTagProps) {
  const priv = p.visibility === 'private';
  const shared = p.sharedWith || 0;
  const title = priv
    ? `Visible to the person who started it${shared ? ` and the ${shared} people it is shared with` : ' only'}`
    : `Visible to every member of ${p.project || 'the project'}${shared ? `, and shared with ${shared} more` : ''}`;
  return (
    <span className={cx('dl-vis', p.className)} title={title}>
      <Icon name={priv ? 'lock' : 'users'} size={14} />
      {priv ? 'Private' : 'Project'}
      {shared ? (
        <>
          <span aria-hidden="true"> · </span>
          <Icon name="link" size={14} />
          {`+${shared}`}
        </>
      ) : null}
    </span>
  );
}
