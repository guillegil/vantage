import { useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { useNavigate } from 'react-router';
import { signInHref } from './next';
import { sessionEndedAt } from './sessionEnd';

// Sends the browser to sign in, forgetting who it was signed in as, so the
// sign-in page asks the server afresh.
export function useGoSignIn() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return useCallback(
    (next: string) => {
      queryClient.removeQueries({ queryKey: ['session'] });
      navigate(signInHref(next));
    },
    [queryClient, navigate],
  );
}

// Follows a path within the client. Once the session has ended, it leads to
// signing in first, and back there after.
export function useGo() {
  const navigate = useNavigate();
  const goSignIn = useGoSignIn();
  return useCallback(
    (path: string) => {
      if (sessionEndedAt()) goSignIn(path);
      else navigate(path);
    },
    [goSignIn, navigate],
  );
}
