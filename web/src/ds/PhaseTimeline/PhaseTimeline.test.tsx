import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PhaseTimeline } from './PhaseTimeline';

describe('PhaseTimeline', () => {
  it('draws each phase in proportion and names it with its duration', () => {
    const { container } = render(
      <PhaseTimeline
        phases={[
          { name: 'setup', seconds: 0.9 },
          { name: 'call', seconds: 6.2, outcome: 'failed' },
          { name: 'teardown', seconds: 0.3 },
        ]}
      />,
    );
    expect(
      screen.getByRole('img', { name: 'setup 900 ms, call 6.20 s failed, teardown 300 ms' }),
    ).toBeInTheDocument();
    const segs = container.querySelectorAll<HTMLElement>('.dl-phases__seg');
    expect(segs).toHaveLength(3);
    expect(segs[1]).toHaveClass('dl-phases__seg--call', 'dl-phases__seg--failed');
    expect(Number(segs[1]?.style.flexGrow)).toBeCloseTo(6.2 / 7.4);
    expect(screen.getByText('failed', { exact: false })).toHaveClass('dl-o--failed');
  });

  it('colours only a phase that failed or errored, and says any other outcome in words', () => {
    const { container } = render(
      <PhaseTimeline
        phases={[
          { name: 'setup', seconds: 0.01, outcome: 'error' },
          { name: 'teardown', seconds: 0.002, outcome: 'skipped' },
        ]}
      />,
    );
    expect(container.querySelector('.dl-phases__seg--error')).not.toBeNull();
    expect(container.querySelector('.dl-phases__seg--skipped')).toBeNull();
    expect(container.querySelector('.dl-phases__legend')).toHaveTextContent('setup 10 ms error');
    expect(container.querySelector('.dl-phases__legend')).toHaveTextContent(
      'teardown 2 ms skipped',
    );
  });
});
