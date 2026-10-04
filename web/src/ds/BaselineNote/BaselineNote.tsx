import { Children, type ReactNode } from 'react';
import type { BaselineNoteProps } from '../contract';
import { cx } from '../lib/cx';
import { fmtEarlier } from '../lib/format';
import { firstChars, visible } from '../lib/visible';

// A branch or commit as recorded, set apart from the words around it so neither its direction nor a
// character that reorders text reaches them.
function recorded(v: string, key: string) {
  return (
    <bdi key={key} className="dl-recorded">
      {visible(v)}
    </bdi>
  );
}

// The line under a run's dotline that says what it was compared with, or why it was not.
export function BaselineNote(p: BaselineNoteProps) {
  const state = p.state || (p.baseline ? 'compared' : 'none');
  const b = p.baseline;
  let words: ReactNode[];
  if (state === 'pending') words = ['Compared with its baseline once the session ends.'];
  else if (state === 'abandoned') words = ['Not compared: no end was recorded.'];
  else if (state === 'none' || !b) words = ['Nothing to compare with yet.'];
  else {
    const f = p.fallback;
    let lead: ReactNode[];
    // Why it fell back to the project's latest complete run, from what this run recorded.
    if (f && f.reason === 'branch')
      lead = ['No earlier complete run on ', recorded(f.branch, 'fb'), '; compared with '];
    else if (f && f.reason === 'detached')
      lead = [
        'No branch recorded (detached HEAD at ',
        recorded(firstChars(f.commit || '', 7), 'fc'),
        '); compared with ',
      ];
    else if (f && f.reason === 'no-git')
      lead = ['Recorded outside a git repository; compared with '];
    else if (f) lead = ['No branch recorded; compared with '];
    else lead = ['Compared with '];
    const label = b.href ? (
      <a key="b" className="dl-link dl-mono" href={b.href} title={b.id || undefined}>
        {b.label}
      </a>
    ) : (
      <span key="b" className="dl-mono" title={b.id || undefined}>
        {b.label}
      </span>
    );
    words = [
      ...lead,
      label,
      ...(b.branch ? [' on ', recorded(b.branch, 'bb')] : []),
      ...(p.earlier != null ? [`, ${fmtEarlier(p.earlier)} earlier`] : []),
    ];
  }
  return (
    <div className={cx('dl-runhead__base', p.className)}>
      <span className="dl-baseline">{Children.toArray(words)}</span>
      {p.children || null}
    </div>
  );
}
