import type { PhaseTimelineProps } from '../contract';
import { cx } from '../lib/cx';
import { fmtSeconds } from '../lib/format';
import { isFailing } from '../lib/outcomes';

export function PhaseTimeline(p: PhaseTimelineProps) {
  const phases = p.phases || [];
  const total = phases.reduce((a, x) => a + (x.seconds || 0), 0) || 1;
  const label = phases
    .map(
      (x) =>
        `${x.name} ${fmtSeconds(x.seconds)}${x.outcome && x.outcome !== 'passed' ? ` ${x.outcome}` : ''}`,
    )
    .join(', ');
  return (
    <div className={cx('dl-phases', p.className)}>
      <div className="dl-phases__bar" role="img" aria-label={label}>
        {phases.map((x) => {
          const bad = isFailing(x.outcome);
          return (
            <span
              key={x.name}
              className={cx(
                'dl-phases__seg',
                x.name === 'call' && 'dl-phases__seg--call',
                bad && `dl-phases__seg--${x.outcome}`,
              )}
              style={{ flexGrow: (x.seconds || 0) / total, flexBasis: 0 }}
            />
          );
        })}
      </div>
      <div className="dl-phases__legend" aria-hidden="true">
        {phases.map((x) => (
          <span key={x.name}>
            {`${x.name} `}
            <b>{fmtSeconds(x.seconds)}</b>
            {x.outcome && x.outcome !== 'passed' ? (
              <span className={`dl-o--${x.outcome}`}>{` ${x.outcome}`}</span>
            ) : null}
          </span>
        ))}
      </div>
    </div>
  );
}
