import { useEffect, useRef } from 'react';
import { useLocation, useNavigationType } from 'react-router';

// The navigation state a page gives an address it rewrites for itself in place, such as the run
// page's selected test: the reader is still on the page, so focus stays where it is. Coming back to
// that address later is a navigation like any other.
export const SAME_PAGE = { samePage: true } as const;

function isSamePage(state: unknown): boolean {
  return (
    typeof state === 'object' &&
    state !== null &&
    (state as { samePage?: unknown }).samePage === true
  );
}

// Names the page in the window's title and, after a navigation within the
// client, moves focus to its heading, as a page load would put a reader there.
export function usePage(title: string) {
  const heading = useRef<HTMLHeadingElement | null>(null);
  const location = useLocation();
  const how = useNavigationType();
  useEffect(() => {
    document.title = title;
  }, [title]);
  useEffect(() => {
    if (location.key === 'default') return;
    if (how === 'REPLACE' && isSamePage(location.state)) return;
    heading.current?.focus();
  }, [location.key, location.state, how]);
  return heading;
}
