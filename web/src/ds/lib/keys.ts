import type { KeyboardEvent } from 'react';

// Keyboard for tablists and radio groups: arrows, Home, End move and select.
export function arrowNav(e: KeyboardEvent<HTMLElement>): void {
  if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(e.key)) return;
  const btns = Array.from(
    e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"],[role="radio"]'),
  );
  const i = btns.indexOf(document.activeElement as HTMLElement);
  if (i < 0) return;
  e.preventDefault();
  const j =
    e.key === 'Home'
      ? 0
      : e.key === 'End'
        ? btns.length - 1
        : (i + (e.key === 'ArrowRight' ? 1 : -1) + btns.length) % btns.length;
  btns[j]?.focus();
  btns[j]?.click();
}
