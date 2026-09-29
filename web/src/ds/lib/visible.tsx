import type { ReactNode } from 'react';

// Characters that print nothing or reorder the text around them: bidirectional
// controls, zero-width and other invisible format characters, C0 and C1
// controls other than tab, line feed and carriage return, and U+FFFD, which
// the server stores where a report carried U+0000 or a lone surrogate.
// Recorded text shows each as its code point, so what a test printed can
// never read as something else.
const NAMES = new Map<number, string>([
  [0x00ad, 'soft hyphen'],
  [0x061c, 'arabic letter mark'],
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
  [0xfeff, 'zero width no-break space'],
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
    cp === 0x00ad ||
    cp === 0x061c ||
    cp === 0x180e ||
    cp === 0xfeff ||
    cp === 0xfffd
  );
}

export function codePoint(cp: number): string {
  return `U+${cp.toString(16).toUpperCase().padStart(4, '0')}`;
}

// The text with each hidden character as its code point in a marked span; a
// string alone when there is none, so ordinary text renders as it always has.
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
    const name = NAMES.get(cp);
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

// The same, for an attribute such as a tooltip: each hidden character as
// ⟨U+202E⟩.
export function visibleText(text: string): string {
  let out = '';
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0;
    out += isHidden(cp) ? `⟨${codePoint(cp)}⟩` : ch;
  }
  return out;
}
