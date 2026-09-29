// Class names from the truthy arguments, space separated.
export function cx(...parts: (string | false | null | undefined | 0)[]): string {
  return parts.filter(Boolean).join(' ');
}

// One handler that calls both, in order; either may be absent.
export function chain<E>(
  a: ((e: E) => void) | undefined,
  b: ((e: E) => void) | undefined,
): (e: E) => void {
  return (e) => {
    if (a) a(e);
    if (b) b(e);
  };
}
