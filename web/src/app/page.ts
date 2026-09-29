import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router';

// Names the page in the window's title and, after a navigation within the
// client, moves focus to its heading, as a page load would put a reader there.
export function usePage(title: string) {
  const heading = useRef<HTMLHeadingElement | null>(null);
  const location = useLocation();
  useEffect(() => {
    document.title = title;
  }, [title]);
  useEffect(() => {
    if (location.key === 'default') return;
    heading.current?.focus();
  }, [location.key]);
  return heading;
}
