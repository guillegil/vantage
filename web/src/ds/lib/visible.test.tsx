import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { isHidden, visible, visibleText } from './visible';

const ZWSP = String.fromCodePoint(0x200b);
const RLO = String.fromCodePoint(0x202e);
const BEL = String.fromCodePoint(7);
const LRI = String.fromCodePoint(0x2066);
const REPL = String.fromCodePoint(0xfffd);

describe('visible', () => {
  it('leaves ordinary text a plain string, other scripts and whitespace included', () => {
    expect(visible('tests/test_a.py::test_x[שלום]\tok\r\n')).toBe(
      'tests/test_a.py::test_x[שלום]\tok\r\n',
    );
  });

  it('shows each bidi control, invisible character and replacement as its code point', () => {
    const { container } = render(<pre>{visible(`a${RLO}b${ZWSP}c${REPL}d${BEL}e`)}</pre>);
    expect(container.textContent).toBe('aU+202EbU+200BcU+FFFDdU+0007e');
    const marks = container.querySelectorAll('.dl-hidden-char');
    expect(marks).toHaveLength(4);
    expect(marks[0]).toHaveAttribute('title', 'U+202E right-to-left override');
    expect(marks[3]).toHaveAttribute('title', 'U+0007');
    for (const ch of [RLO, ZWSP, REPL, BEL]) expect(container.textContent).not.toContain(ch);
  });

  it('counts characters beyond the basic plane whole', () => {
    const { container } = render(<span>{visible('x\u{E0041}y😀')}</span>);
    expect(container.textContent).toBe('xU+E0041y😀');
  });

  it('writes them out for an attribute', () => {
    expect(visibleText(`a${LRI}b`)).toBe('a⟨U+2066⟩b');
    expect(visibleText('plain')).toBe('plain');
  });

  it('shows the characters that print nothing but are not controls', () => {
    const cases: [number, string][] = [
      [0x115f, 'U+115F hangul choseong filler'],
      [0x1160, 'U+1160 hangul jungseong filler'],
      [0x3164, 'U+3164 hangul filler'],
      [0xffa0, 'U+FFA0 halfwidth hangul filler'],
      [0x034f, 'U+034F combining grapheme joiner'],
      [0xfe00, 'U+FE00 variation selector-1'],
      [0xfe0f, 'U+FE0F variation selector-16'],
      [0xe0100, 'U+E0100 variation selector-17'],
      [0xe01ef, 'U+E01EF variation selector-256'],
    ];
    for (const [cp, title] of cases) {
      const { container, unmount } = render(
        <span>{visible(`test_login[admin${String.fromCodePoint(cp)}]`)}</span>,
      );
      expect(container.textContent).toBe(`test_login[admin${title.split(' ')[0]}]`);
      expect(container.querySelector('.dl-hidden-char')).toHaveAttribute('title', title);
      unmount();
    }
    // Their neighbours still print.
    expect(
      [0x115e, 0x1161, 0x3165, 0xffa1, 0x034e, 0xfdff, 0xfe10, 0xe00ff, 0xe01f0].some(isHidden),
    ).toBe(false);
  });

  it('never hides tab, line feed or carriage return', () => {
    expect([0x09, 0x0a, 0x0d].some(isHidden)).toBe(false);
    expect([0x00, 0x1b, 0x7f, 0x85, 0x061c, 0x2069, 0xfeff].every(isHidden)).toBe(true);
  });
});
