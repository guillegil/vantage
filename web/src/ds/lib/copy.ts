import { type MouseEvent, useState } from 'react';

// Copies text to the clipboard and says so for a moment. Where the clipboard
// is refused, it selects the text so the person can copy it themselves.
export function useCopy(text: string): [boolean, (e: MouseEvent<HTMLElement>) => void] {
  const [copied, setCopied] = useState(false);
  function copy(e: MouseEvent<HTMLElement>) {
    const btn = e.currentTarget;
    function done() {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    }
    function select() {
      try {
        const scope = btn.closest('[data-copy-scope]') || btn.parentNode;
        if (!scope) return;
        const el = (scope as Element).querySelector('[data-copy-text]') || scope;
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        if (!sel) return;
        sel.removeAllRanges();
        sel.addRange(range);
      } catch {
        // nothing more to try
      }
    }
    try {
      navigator.clipboard.writeText(String(text)).then(done, select);
    } catch {
      select();
    }
  }
  return [copied, copy];
}
