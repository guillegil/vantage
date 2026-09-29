import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Outcome } from '../contract';
import { Dotline } from './Dotline';

function marks(container: HTMLElement) {
  return container.querySelectorAll('svg rect:not(.dl-dotline__base)');
}

describe('Dotline', () => {
  it('labels what it draws in pytest words and order', () => {
    render(<Dotline results={['passed', 'failed', 'passed', 'error']} />);
    expect(
      screen.getByRole('img', { name: 'Results in collection order: 1 failed, 2 passed, 1 error' }),
    ).toBeInTheDocument();
  });

  it('says so when no tests ran, and while running', () => {
    const { rerender } = render(<Dotline results={[]} />);
    expect(
      screen.getByRole('img', { name: 'Results in collection order: no tests ran' }),
    ).toBeInTheDocument();
    rerender(<Dotline results={['passed']} running />);
    expect(
      screen.getByRole('img', { name: 'Results in collection order: 1 passed, still running' }),
    ).toBeInTheDocument();
  });

  it('takes a label of its own', () => {
    render(<Dotline results={['passed']} label="In the order pytest reported them" />);
    expect(
      screen.getByRole('img', { name: 'In the order pytest reported them' }),
    ).toBeInTheDocument();
  });

  it('draws one mark per result, an error as a broken bar', () => {
    const { container } = render(<Dotline results={['passed', 'failed', 'error']} />);
    expect(marks(container)).toHaveLength(4);
    expect(container.querySelectorAll('.dl-m--error')).toHaveLength(2);
  });

  it('shares marks past its width and says how many, the most severe shown', () => {
    const results: Outcome[] = Array.from({ length: 400 }, () => 'passed');
    results[7] = 'failed';
    const { container } = render(<Dotline results={results} width={100} />);
    const scale = '1 mark = 16 results, the most severe shown';
    expect(screen.getByText(scale)).toHaveClass('dl-dotline__cap');
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain(`. ${scale}`);
    expect(container.querySelectorAll('.dl-m--failed')).toHaveLength(1);
    expect(marks(container)).toHaveLength(25);
  });

  it('hides the caption on request but keeps the scale in its name', () => {
    const results: Outcome[] = Array.from({ length: 400 }, () => 'passed');
    render(<Dotline results={results} width={100} showScale={false} />);
    expect(screen.queryByText(/^1 mark =/)).toBeNull();
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('1 mark = 16 results');
  });

  it('ends a running line in the cursor', () => {
    const { container } = render(<Dotline results={['passed']} running />);
    expect(container.querySelector('.dl-cursor')).not.toBeNull();
  });

  it('prints pytest characters in the chars variant', () => {
    render(
      <Dotline
        variant="chars"
        files={[{ path: 'tests/test_a.py', results: ['passed', 'failed', 'skipped'] }]}
      />,
    );
    expect(screen.getByText('tests/test_a.py')).toBeInTheDocument();
    expect(screen.getByRole('img').textContent).toContain('.Fs');
    expect(screen.getByRole('img').textContent).toContain('[100%]');
  });
});
