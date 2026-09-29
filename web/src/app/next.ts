// Where sign-in returns to: a path on this origin, never another site.
export function safeNext(raw: string | null | undefined, origin = window.location.origin): string {
  if (!raw?.startsWith('/') || raw.startsWith('//') || raw.startsWith('/\\')) return '/';
  let url: URL;
  try {
    url = new URL(raw, origin);
  } catch {
    return '/';
  }
  if (url.origin !== origin) return '/';
  return `${url.pathname}${url.search}${url.hash}`;
}

export function signInHref(next: string): string {
  const safe = safeNext(next);
  return safe === '/' ? '/sign-in' : `/sign-in?next=${encodeURIComponent(safe)}`;
}
