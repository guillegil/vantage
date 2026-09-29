import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SummaryLine } from './SummaryLine';

describe('SummaryLine', () => {
  it("prints pytest's closing line in its order, plurals and durations", () => {
    const { container } = render(
      <SummaryLine counts={{ passed: 208, error: 1, failed: 2, skipped: 3 }} seconds={132.4} />,
    );
    expect(container.firstChild).toHaveTextContent(
      '2 failed, 208 passed, 3 skipped, 1 error in 132.40s (0:02:12)',
    );
    expect(container.firstChild).toHaveClass('dl-summary--failed');
  });

  it("takes pytest's colours: warning for xpassed, passed for passes alone", () => {
    const { container, rerender } = render(<SummaryLine counts={{ passed: 3, xpassed: 1 }} />);
    expect(container.firstChild).toHaveClass('dl-summary--warning');
    rerender(<SummaryLine counts={{ passed: 3 }} seconds={0.5} />);
    expect(container.firstChild).toHaveClass('dl-summary--passed');
    expect(container.firstChild).toHaveTextContent('3 passed in 0.50s');
    rerender(<SummaryLine counts={{}} />);
    expect(container.firstChild).toHaveTextContent('no tests ran');
    expect(container.firstChild).toHaveClass('dl-summary--warning');
  });

  it('says so while running', () => {
    const { container } = render(<SummaryLine counts={{}} running seconds={4} />);
    expect(container.firstChild).toHaveTextContent('collecting so far · 4.00s');
    expect(container.firstChild).toHaveClass('dl-summary--running');
  });

  it('counts errors in pytest plurals', () => {
    const { container } = render(<SummaryLine counts={{ error: 2 }} />);
    expect(container.firstChild).toHaveTextContent('2 errors');
  });
});
