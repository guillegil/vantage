import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ICONS, Icon } from './Icon';

describe('Icon', () => {
  it('is hidden from assistive technology beside its word', () => {
    const { container } = render(<Icon name="check" />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg).not.toHaveAttribute('role');
    expect(svg).toHaveAttribute('width', '16');
  });

  it('is an image named by its title when it stands alone', () => {
    const { getByRole } = render(<Icon name="pin" title="Pinned" size={12} />);
    expect(getByRole('img', { name: 'Pinned' })).toHaveAttribute('width', '12');
  });

  it('draws circles, rects and paths from its glyph', () => {
    const { container } = render(<Icon name="lock" />);
    expect(container.querySelectorAll('rect')).toHaveLength(1);
    expect(container.querySelectorAll('path')).toHaveLength(1);
    expect(Object.keys(ICONS)).toHaveLength(55);
  });

  it('has the glyphs of a sortable header and the account menu, and no longer dice or target', () => {
    expect(ICONS.chevrons).toEqual(['M5 6.5l3-3 3 3', 'M5 9.5l3 3 3-3']);
    expect(ICONS.shield).toHaveLength(1);
    expect(Object.hasOwn(ICONS, 'dice')).toBe(false);
    expect(Object.hasOwn(ICONS, 'target')).toBe(false);
  });

  it('mirrors directional glyphs only', () => {
    const { container } = render(
      <>
        <Icon name="log-out" />
        <Icon name="check" />
      </>,
    );
    const [out, check] = container.querySelectorAll('svg');
    expect(out).toHaveClass('dl-icon--dir');
    expect(check).not.toHaveClass('dl-icon--dir');
  });
});
