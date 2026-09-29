import { createElement, type ReactNode, useState } from 'react';
import type { EvidenceProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { useCopy } from '../lib/copy';
import { cx } from '../lib/cx';
import { useUid } from '../lib/hooks';
import { visible } from '../lib/visible';

export function evidenceLines(text: string | null | undefined, kind: string): ReactNode {
  const lines = String(text || '').split('\n');
  if (kind !== 'traceback') return visible(lines.join('\n'));
  return lines.map((ln, i) => {
    // pytest's own marks: "E" lines explain the failure, ">" is the failing source line, "path.py:N:" locates it.
    const cls = /^E(\s|$)/.test(ln)
      ? 'dl-ev--e'
      : /^>/.test(ln)
        ? 'dl-ev--src'
        : /^\S.*\.py:\d+:/.test(ln)
          ? 'dl-ev--loc'
          : undefined;
    return (
      // biome-ignore lint/suspicious/noArrayIndexKey: a line's place in the text is its identity.
      <span key={i} className={cls}>
        {visible(ln + (i < lines.length - 1 ? '\n' : ''))}
      </span>
    );
  });
}

export function Evidence(p: EvidenceProps) {
  const title = p.title || 'Traceback';
  const [wrapLines, setWrapLines] = useState(false);
  const [full, setFull] = useState(false);
  const [copied, copy] = useCopy(p.text);
  const bodyId = useUid('dl-ev');
  const level = Math.min(6, Math.max(1, p.level || 3));
  return (
    <section className={cx('dl-evidence', p.className)} data-copy-scope="">
      <div className="dl-evidence__head">
        {createElement(`h${level}`, { className: 'dl-evidence__title' }, title)}
        {p.meta ? <span>{typeof p.meta === 'string' ? visible(p.meta) : p.meta}</span> : null}
        {p.actions || null}
        {p.controls === false ? null : (
          <span className="dl-evidence__tools">
            <button
              type="button"
              className="dl-evidence__tool"
              aria-pressed={wrapLines ? 'true' : 'false'}
              onClick={() => setWrapLines(!wrapLines)}
            >
              Wrap lines
            </button>
            <button
              type="button"
              className="dl-evidence__tool"
              aria-expanded={full ? 'true' : 'false'}
              aria-controls={bodyId}
              onClick={() => setFull(!full)}
            >
              {full ? 'Collapse' : 'Expand'}
            </button>
            <button
              type="button"
              className="dl-evidence__tool"
              aria-label={`Copy ${title.toLowerCase()}`}
              onClick={copy}
            >
              <Icon name={copied ? 'check' : 'copy'} size={14} />
              {copied ? 'Copied' : 'Copy'}
            </button>
            <span className="dl-sr" aria-live="polite">
              {copied ? 'Copied' : ''}
            </span>
          </span>
        )}
      </div>
      <pre
        id={bodyId}
        className={cx(
          'dl-evidence__body',
          wrapLines && 'dl-evidence__body--wrap',
          full && 'dl-evidence__body--full',
        )}
        tabIndex={0}
        aria-label={title}
        dir="ltr"
        data-copy-text=""
      >
        {evidenceLines(p.text, p.kind || 'traceback')}
      </pre>
      {p.truncated || p.notice ? (
        <div className="dl-evidence__foot">
          {p.truncated ? (
            <span className="dl-tag dl-tag--warning">
              <Icon name="warning" size={12} />
              truncated at capture
            </span>
          ) : null}
          {typeof p.truncated === 'string' ? <span>{p.truncated}</span> : null}
          {p.notice ? <span>{p.notice}</span> : null}
        </div>
      ) : null}
    </section>
  );
}
