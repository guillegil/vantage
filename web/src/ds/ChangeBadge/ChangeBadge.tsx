import { Children, Fragment, type ReactNode } from 'react';
import type { Change, ChangeBadgeProps, Outcome } from '../contract';
import { markRects } from '../Dotline/Dotline';
import { Icon } from '../Icon/Icon';
import { CHANGE_ICON, CHANGES, isChange } from '../lib/changes';
import { cx } from '../lib/cx';
import { capital, plural } from '../lib/format';
import { isFailing } from '../lib/outcomes';

// The mark a change draws in a dotline, at the dotline's own scale, so a label teaches the line.
export function ChangeGlyph(p: { change: Change; outcome?: Outcome | null }) {
  const c = p.change;
  const icon = CHANGE_ICON[c];
  if (icon) return <Icon name={icon} size={12} className="dl-change__icon" />;
  const o =
    c === 'fixed'
      ? p.outcome && !isFailing(p.outcome)
        ? p.outcome
        : 'passed'
      : p.outcome && isFailing(p.outcome)
        ? p.outcome
        : 'failed';
  // Its neighbours are plain passes, so the mark stands on a track rather than beside the word.
  const kids = [
    <rect key="base" className="dl-dotline__base" x={0} y={15} width={11} height={1} />,
    ...markRects('passed', 0, 3, 16, 'a', null),
    ...markRects(o, 4, 3, 16, 'g', c === 'still-failing' ? null : c),
    ...markRects('passed', 8, 3, 16, 'b', null),
  ];
  return (
    <svg
      className="dl-change__glyph"
      width={11}
      height={20}
      viewBox="0 -4 11 20"
      aria-hidden="true"
      focusable="false"
    >
      {kids}
    </svg>
  );
}

// The sentence after a change's name, for a detail head: "New failure: passed in 1adf29af".
function changeDetail(p: ChangeBadgeProps): ReactNode[] | null {
  const base = p.baseline ? <code>{p.baseline}</code> : null;
  switch (p.change) {
    case 'new-failure':
      return p.was ? [`${p.was} in `, base] : ['not in ', base, '; its first run failed'];
    case 'still-failing':
      if (p.streak?.runs)
        return [
          plural(p.streak.runs, 'run', 'runs'),
          p.streak.since ? (
            <Fragment key="s">
              , since <code>{p.streak.since}</code>
            </Fragment>
          ) : null,
        ];
      return p.was ? [`${p.was} in `, base] : null;
    case 'fixed':
      return p.was ? [`${p.was} in `, base] : null;
    case 'new-test':
      return ['not in ', base];
    case 'removed':
      return [p.was ? `${p.was} in ` : 'in ', base, ', not collected in this run'];
    case 'not-reached':
      return [p.was ? `${p.was} in ` : 'in ', base, '; this run stopped before it'];
    default:
      return null;
  }
}

export function ChangeBadge(p: ChangeBadgeProps) {
  if (!isChange(p.change)) return null;
  const ch = CHANGES[p.change];
  const rest = p.detail ? changeDetail(p) : null;
  return (
    <span className={cx('dl-change', `dl-change--${p.change}`, p.className)}>
      <ChangeGlyph change={p.change} outcome={p.outcome} />
      <span className="dl-change__text">
        <span className="dl-change__word">{p.detail ? capital(ch.word) : ch.word}</span>
        {rest ? (
          <span className="dl-change__detail">
            {': '}
            {Children.toArray(rest)}
          </span>
        ) : null}
      </span>
    </span>
  );
}
