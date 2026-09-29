import { useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { Navigate, Outlet, useLocation, useMatch, useNavigate } from 'react-router';
import { projectRef, runsHref } from '../adapt';
import { isApiError } from '../api/client';
import { type Session, signOut, useProjects, useRun, useSession } from '../api/queries';
import { AppBar, Button, Notice } from '../ds';
import { FailureNotice } from '../pages/Failure';
import { useInAppLinks } from './links';
import { signInHref } from './next';
import { clearSessionEnded, sessionEndedAt, useSessionEnded } from './sessionEnd';

const RUN_ID = /^[0-9a-f]{32}$/;

export function hhmm(value: Date | string): string {
  const d = typeof value === 'string' ? new Date(value) : value;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

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

function here(location: { pathname: string; search: string; hash: string }): string {
  return `${location.pathname}${location.search}${location.hash}`;
}

// The project the page is about: named in its address, or the run's.
function useCurrentProject(): string | null {
  const runs = useMatch('/p/:project/*');
  const run = useMatch('/runs/:runId');
  const runId = run?.params.runId ?? '';
  const detail = useRun(runId, RUN_ID.test(runId));
  if (runs?.params.project) return runs.params.project;
  return detail.data?.project ?? null;
}

function Shell({ session }: { session: Session }) {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const goSignIn = useGoSignIn();
  const ended = useSessionEnded();
  const current = useCurrentProject();
  const projects = useProjects();
  const go = useCallback(
    (path: string) => {
      // Once the session has ended, a link leads to signing in first.
      if (sessionEndedAt()) goSignIn(path);
      else navigate(path);
    },
    [goSignIn, navigate],
  );
  useInAppLinks(go);
  const items = projects.data?.items ?? [];
  const found = items.find((p) => p.name === current);
  const project = current ? (found ? projectRef(found) : { name: current, role: null }) : undefined;
  const user = session.user;
  async function onSignOut() {
    try {
      await signOut();
    } finally {
      queryClient.clear();
      clearSessionEnded();
      navigate('/sign-in');
    }
  }
  return (
    <>
      <a className="app-skip" href="#main">
        Skip to the page
      </a>
      <AppBar
        homeHref="/"
        project={project}
        projects={items.map(projectRef)}
        switcherLabel={user?.admin ? 'All projects' : undefined}
        canCreate={false}
        search={false}
        nav={current ? [{ id: 'runs', label: 'Runs', href: runsHref(current) }] : []}
        active="runs"
        onSelectProject={(p) => go(runsHref(p.name))}
        user={user ? { name: user.name } : undefined}
        account={
          user
            ? {
                note: (
                  <>
                    Signed in as <b>{user.name}</b>
                    {session.expires_at ? ` until ${hhmm(session.expires_at)}` : ''}
                  </>
                ),
                items: [{ label: 'Sign out', icon: 'log-out', onSelect: onSignOut }],
              }
            : undefined
        }
      />
      <main id="main" className="dl-page app-main" tabIndex={-1}>
        {ended ? (
          <Notice
            tone="warning"
            title={`Your session ended at ${hhmm(ended)}`}
            action={
              <Button size="sm" onClick={() => goSignIn(here(location))}>
                Sign in again
              </Button>
            }
          >
            Sign in again to carry on; this page stays as it is.
          </Notice>
        ) : null}
        <Outlet context={session} />
      </main>
    </>
  );
}

// Every page but sign-in: known to be signed in, or on a server with no
// users, before anything else is asked.
export function Root() {
  const session = useSession();
  const location = useLocation();
  if (session.isError) {
    if (isApiError(session.error, 401)) return <Navigate to={signInHref(here(location))} replace />;
    return (
      <main id="main" className="dl-page">
        <FailureNotice
          error={session.error}
          retry={() => session.refetch()}
          busy={session.isFetching}
        />
      </main>
    );
  }
  if (!session.data) {
    return (
      <main id="main" className="dl-page">
        <p className="dl-caption" role="status">
          Loading…
        </p>
      </main>
    );
  }
  return <Shell session={session.data} />;
}
