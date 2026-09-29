import { useEffect } from 'react';

// The design system renders plain <a href>, which keep middle-click and
// copy-link. A plain primary click on one that stays in the client is
// routed here instead of reloading the page.
export function inAppPath(event: MouseEvent, origin = window.location.origin): string | null {
  if (event.defaultPrevented || event.button !== 0) return null;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
  const target = event.target as Element | null;
  const anchor = target?.closest?.('a[href]');
  if (!(anchor instanceof HTMLAnchorElement)) return null;
  if (anchor.target || anchor.hasAttribute('download')) return null;
  const href = anchor.getAttribute('href');
  if (!href || href.startsWith('#')) return null;
  let url: URL;
  try {
    url = new URL(anchor.href, origin);
  } catch {
    return null;
  }
  if (url.origin !== origin) return null;
  if (url.pathname === '/api' || url.pathname.startsWith('/api/')) return null;
  if (url.pathname.startsWith('/assets/') || url.pathname.startsWith('/fonts/')) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}

export function useInAppLinks(go: (path: string) => void): void {
  useEffect(() => {
    function onClick(event: MouseEvent) {
      const path = inAppPath(event);
      if (path === null) return;
      event.preventDefault();
      go(path);
    }
    document.addEventListener('click', onClick);
    return () => document.removeEventListener('click', onClick);
  }, [go]);
}
