import { type ReactNode, useEffect, useRef, useState } from 'react';
import type { OutcomeCounts, SummaryLineProps } from '../contract';
import { cx } from '../lib/cx';
import { fmtPytestSeconds } from '../lib/format';
import { describeCounts, PYTEST_ORDER, partWord } from '../lib/outcomes';

const PART_CLASS: Record<(typeof PYTEST_ORDER)[number], string> = {
  failed: 'dl-o--failed',
  passed: 'dl-o--passed',
  skipped: 'dl-o--skipped',
  xfailed: 'dl-o--xfailed',
  xpassed: 'dl-o--xpassed',
  error: 'dl-o--error',
  warnings: 'dl-summary__part--warnings',
  deselected: 'dl-summary__part--deselected',
};

// pytest's rule: red with any failure or error, yellow with warnings or xpassed, green with passes, else yellow.
function summaryColor(c: OutcomeCounts, running?: boolean): string {
  if (running) return 'running';
  if (c.failed || c.error) return 'failed';
  if (c.warnings || c.xpassed) return 'warning';
  if (c.passed) return 'passed';
  return 'warning';
}

export function SummaryLine(p: SummaryLineProps) {
  const c = p.counts || {};
  const [fits, setFits] = useState(true);
  const root = useRef<HTMLDivElement | null>(null);
  const textRef = useRef<HTMLSpanElement | null>(null);
  const said = `${describeCounts(c)}|${p.seconds}|${!!p.running}`;
  // biome-ignore lint/correctness/useExhaustiveDependencies: remeasured whenever what it says changes.
  useEffect(() => {
    const el = root.current;
    const t = textRef.current;
    if (!el || !t || typeof ResizeObserver === 'undefined') return undefined;
    // Its width on one line, measured unwrapped, against the room there is beside two rules.
    function check() {
      if (!el || !t) return;
      const prev = t.style.whiteSpace;
      t.style.whiteSpace = 'nowrap';
      const natural = t.scrollWidth;
      t.style.whiteSpace = prev;
      setFits(el.clientWidth >= natural + 52);
    }
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [said]);
  const parts: ReactNode[] = [];
  for (const k of PYTEST_ORDER) {
    const n = c[k];
    if (!n) continue;
    if (parts.length) parts.push(<span key={`${k}-sep`}>, </span>);
    parts.push(
      <span key={k} className={cx('dl-summary__part', PART_CLASS[k])}>
        {`${n} ${partWord(k, n)}`}
      </span>,
    );
  }
  if (!parts.length)
    parts.push(<span key="none">{p.running ? 'collecting' : 'no tests ran'}</span>);
  let tail = '';
  if (p.running) tail = ` so far${p.seconds != null ? ` · ${fmtPytestSeconds(p.seconds)}` : ''}`;
  else if (p.seconds != null) tail = ` in ${fmtPytestSeconds(p.seconds)}`;
  return (
    <div
      ref={root}
      className={cx(
        'dl-summary',
        `dl-summary--${summaryColor(c, p.running)}`,
        !fits && 'dl-summary--wrap',
        p.className,
      )}
    >
      <span className="dl-summary__rule" aria-hidden="true" />
      <span ref={textRef} className="dl-summary__text">
        {parts}
        {tail}
      </span>
      <span className="dl-summary__rule" aria-hidden="true" />
    </div>
  );
}
