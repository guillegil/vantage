import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DurationSpark } from './DurationSpark';

describe('DurationSpark', () => {
  it('draws durations oldest first, skipping runs without one, with the last value and range', () => {
    const { container } = render(<DurationSpark values={[4.1, null, 4.3, 0.184]} width={150} />);
    expect(
      screen.getByRole('img', {
        name: 'Duration over 3 runs: last 184 ms, from 184 ms to 4.30 s',
      }),
    ).toBeInTheDocument();
    expect(container.querySelector('.dl-spark__value')).toHaveTextContent('184 ms');
    expect(container.querySelector('.dl-spark__range')).toHaveTextContent('184 ms – 4.30 s');
    const end = container.querySelector('.dl-spark__end') as SVGCircleElement;
    expect(end.getAttribute('cx')).toBe('150');
  });

  it('says so when nothing was recorded', () => {
    render(<DurationSpark values={[null, null]} />);
    expect(screen.getByText('No durations recorded')).toHaveClass('dl-spark__range');
  });

  it('draws one run as a single point, and leaves the range out on request', () => {
    const { container } = render(<DurationSpark values={[2]} showRange={false} />);
    expect(container.querySelector('.dl-spark__line')?.getAttribute('d')).toBe('M0.0 25.0');
    expect(container.querySelector('.dl-spark__range')).toBeNull();
  });
});
