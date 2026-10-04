import type { ReactNode } from 'react';

// Characters that print nothing or reorder the text around them: bidirectional
// controls, zero-width and other invisible format characters, the Hangul
// fillers, the combining grapheme joiner, the variation selectors, C0 and C1
// controls other than tab and line breaks, lone surrogates, and U+FFFD, which
// the server stores where a report held U+0000. Recorded text shows each as
// its code point, so what a test printed can never read as something else.
const NAMES = new Map<number, string>([
  [0x00ad, 'soft hyphen'],
  [0x034f, 'combining grapheme joiner'],
  [0x061c, 'arabic letter mark'],
  [0x115f, 'hangul choseong filler'],
  [0x1160, 'hangul jungseong filler'],
  [0x180e, 'mongolian vowel separator'],
  [0x200b, 'zero width space'],
  [0x200c, 'zero width non-joiner'],
  [0x200d, 'zero width joiner'],
  [0x200e, 'left-to-right mark'],
  [0x200f, 'right-to-left mark'],
  [0x2028, 'line separator'],
  [0x2029, 'paragraph separator'],
  [0x202a, 'left-to-right embedding'],
  [0x202b, 'right-to-left embedding'],
  [0x202c, 'pop directional formatting'],
  [0x202d, 'left-to-right override'],
  [0x202e, 'right-to-left override'],
  [0x2060, 'word joiner'],
  [0x2066, 'left-to-right isolate'],
  [0x2067, 'right-to-left isolate'],
  [0x2068, 'first strong isolate'],
  [0x2069, 'pop directional isolate'],
  [0x3164, 'hangul filler'],
  [0xfeff, 'zero width no-break space'],
  [0xffa0, 'halfwidth hangul filler'],
  [0xfffd, 'replacement character, where the report held U+0000 or a lone surrogate'],
]);

export function isHidden(cp: number): boolean {
  if (cp === 0x09 || cp === 0x0a || cp === 0x0d) return false;
  return (
    cp <= 0x1f ||
    (cp >= 0x7f && cp <= 0x9f) ||
    (cp >= 0x200b && cp <= 0x200f) ||
    (cp >= 0x2028 && cp <= 0x202e) ||
    (cp >= 0x2060 && cp <= 0x206f) ||
    (cp >= 0xfff9 && cp <= 0xfffb) ||
    (cp >= 0xe0000 && cp <= 0xe007f) ||
    (cp >= 0xfe00 && cp <= 0xfe0f) ||
    (cp >= 0xe0100 && cp <= 0xe01ef) ||
    (cp >= 0xd800 && cp <= 0xdfff) ||
    cp === 0x00ad ||
    cp === 0x034f ||
    cp === 0x061c ||
    cp === 0x115f ||
    cp === 0x1160 ||
    cp === 0x180e ||
    cp === 0x3164 ||
    cp === 0xfeff ||
    cp === 0xffa0 ||
    cp === 0xfffd
  );
}

function nameOf(cp: number): string | undefined {
  if (cp >= 0xfe00 && cp <= 0xfe0f) return `variation selector-${cp - 0xfe00 + 1}`;
  if (cp >= 0xe0100 && cp <= 0xe01ef) return `variation selector-${cp - 0xe0100 + 17}`;
  if (cp >= 0xd800 && cp <= 0xdfff) return 'lone surrogate';
  return NAMES.get(cp);
}

export function codePoint(cp: number): string {
  return `U+${cp.toString(16).toUpperCase().padStart(4, '0')}`;
}

// Recorded text for the page: each hidden character as its code point in a
// marked box; the string itself when there is none, so ordinary text renders
// as it always has. Iterating a string goes by code point, a pair of
// surrogates counting as one and a lone one as itself.
export function visible(text: string): ReactNode {
  let plain = '';
  let out: ReactNode[] | null = null;
  let n = 0;
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0;
    if (!isHidden(cp)) {
      plain += ch;
      continue;
    }
    out = out ?? [];
    if (plain) out.push(plain);
    plain = '';
    const name = nameOf(cp);
    out.push(
      <span
        key={`u${n++}`}
        className="dl-hidden-char"
        title={name ? `${codePoint(cp)} ${name}` : codePoint(cp)}
      >
        {codePoint(cp)}
      </span>,
    );
  }
  if (!out) return plain;
  if (plain) out.push(plain);
  return out;
}

// The same for an attribute, a tooltip or an accessible name: each hidden
// character as ⟨U+202E⟩.
export function visibleText(text: string): string {
  let out = '';
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0;
    out += isHidden(cp) ? `⟨${codePoint(cp)}⟩` : ch;
  }
  return out;
}

// The first n characters by code point, so a character is never cut in two.
export function firstChars(s: string, n: number): string {
  let out = '';
  let k = 0;
  for (const ch of s) {
    if (k++ >= n) break;
    out += ch;
  }
  return out;
}
