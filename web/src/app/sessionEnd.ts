import { useSyncExternalStore } from 'react';

// When a session that was working stopped authenticating: the page keeps
// what it shows and says so, instead of being thrown away.
let endedAt: Date | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function markSessionEnded(expiresAt?: string | null, now = new Date()): void {
  if (endedAt) return;
  const expiry = expiresAt ? new Date(expiresAt) : null;
  endedAt = expiry && expiry.getTime() <= now.getTime() ? expiry : now;
  emit();
}

export function clearSessionEnded(): void {
  if (!endedAt) return;
  endedAt = null;
  emit();
}

export function sessionEndedAt(): Date | null {
  return endedAt;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useSessionEnded(): Date | null {
  return useSyncExternalStore(subscribe, sessionEndedAt, sessionEndedAt);
}
