import type { QueryClient } from '@tanstack/react-query';
import { makeQueryClient, type Session } from '../api/queries';
import { markSessionEnded } from './sessionEnd';

// A 401 on who is asking is handled by the layout, which sends the browser
// to sign in. A 401 on anything else ends a session that was working: the
// page stays as it is and says so.
export function createQueryClient(): QueryClient {
  const client: QueryClient = makeQueryClient({
    onSignedOut: () => undefined,
    onSessionEnded: () =>
      markSessionEnded(client.getQueryData<Session>(['session'])?.expires_at ?? null),
  });
  return client;
}
