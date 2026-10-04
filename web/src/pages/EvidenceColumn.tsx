import { FAILURE_TEXT_NOTICE, resultEvidence } from '../adapt';
import type { ResultDetail } from '../api/queries';
import { Evidence, Notice } from '../ds';

// Everything a result holds that a person reads: its failure, its reasons and its output, each
// where pytest would print it. The result page shows it under its heading, the run page under the
// selected test's, so each says at which level its blocks' titles sit.
export function EvidenceColumn({ result, level }: { result: ResultDetail; level: 2 | 3 }) {
  const ev = resultEvidence(result);
  if (ev.absence === 'unrecorded') {
    return (
      <p className="dl-caption">
        Failure text was not recorded; run with{' '}
        <code className="dl-mono">--vantage-failure-text</code>
      </p>
    );
  }
  if (ev.absence === 'unknown') {
    return (
      <p className="dl-caption">
        Nothing was captured for this result: output capture was off, or the run did not record
        failure text (<code className="dl-mono">--vantage-failure-text</code>).
      </p>
    );
  }
  return (
    <>
      {ev.blocks.map((b) => (
        <Evidence
          key={b.key}
          title={b.title}
          text={b.text}
          kind={b.kind}
          meta={b.meta}
          truncated={b.truncated}
          notice={FAILURE_TEXT_NOTICE}
          level={level}
        />
      ))}
      {ev.dropped.map((title) => (
        <Notice key={title} tone="warning" title={`${title} was not kept`}>
          This session’s failure text passed its 512 KiB limit, which is spent on failed and errored
          results first.
        </Notice>
      ))}
      {ev.absence === 'silent' ? (
        <p className="dl-caption">Nothing was printed to stdout or stderr.</p>
      ) : null}
    </>
  );
}
