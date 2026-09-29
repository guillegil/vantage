import { useQueryClient } from '@tanstack/react-query';
import { type FormEvent, useEffect, useState } from 'react';
import { Navigate, useSearchParams } from 'react-router';
import { ApiError, isApiError } from '../api/client';
import { type Session, signIn, useSession } from '../api/queries';
import { safeNext } from '../app/next';
import { clearSessionEnded } from '../app/sessionEnd';
import { Button, Command, Field, Icon, Notice, Panel, Wordmark } from '../ds';

// What each refusal of POST /session means to the person signing in.
function refusal(error: unknown): { tone: 'danger' | 'warning'; text: string } {
  if (isApiError(error, 401)) {
    return {
      tone: 'danger',
      text: "That username and password don't match an account on this server.",
    };
  }
  if (isApiError(error, 503)) {
    return {
      tone: 'warning',
      text: 'The server is busy checking other sign-ins. Try again in a few seconds.',
    };
  }
  if (!isApiError(error) || error.status === 0) {
    return { tone: 'danger', text: 'The server did not answer.' };
  }
  return { tone: 'danger', text: error.detail };
}

// Browsers keep a session cookie only in a secure context: HTTPS, or this
// machine's loopback address. Anywhere else no password is ever sent.
function InsecureNotice() {
  const { host, hostname, port, protocol } = window.location;
  const local = port || (protocol === 'https:' ? '443' : '80');
  return (
    <Notice tone="warning" title="Signing in needs HTTPS or this machine’s own address">
      <span className="dl-stack dl-stack--tight">
        <span>
          {`This page was opened over plain HTTP at ${window.location.origin}, where browsers do not keep a sign-in. Reach this server through an SSH tunnel, then open http://127.0.0.1:${local}, or put it behind a TLS reverse proxy.`}
        </span>
        <Command text={`ssh -L ${local}:127.0.0.1:${local} ${hostname || host}`} />
      </span>
    </Notice>
  );
}

export function SignInPage() {
  const [params] = useSearchParams();
  const next = safeNext(params.get('next'));
  const session = useSession();
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    document.title = 'Sign in · vantage';
  }, []);

  // Already signed in, or a server with no users, which needs no sign-in.
  if (signedIn || session.data) return <Navigate to={next} replace />;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setFailure(null);
    try {
      const signed: Session = await signIn(name, password);
      // Nothing read as anyone else stays in memory.
      queryClient.clear();
      clearSessionEnded();
      queryClient.setQueryData(['session'], signed);
      setSignedIn(true);
    } catch (error) {
      if (isApiError(error, 409, 'open_server')) {
        queryClient.removeQueries({ queryKey: ['session'] });
        await session.refetch();
      }
      setFailure(error instanceof ApiError ? error : new ApiError(0, 'unreachable', ''));
      if (!isApiError(error, 503)) setPassword('');
    } finally {
      setBusy(false);
    }
  }

  const said = failure ? refusal(failure) : null;
  const unreachable = session.isError && !isApiError(session.error, 401);

  return (
    <main className="dl-signin" id="main">
      <div className="dl-stack dl-stack--tight">
        <Wordmark />
        <span className="dl-caption dl-mono">{window.location.origin}</span>
      </div>
      {next !== '/' ? <Notice>{`Sign in again to go back to ${next}.`}</Notice> : null}
      <Panel title="Sign in" level={1}>
        {window.isSecureContext ? (
          <form className="dl-stack" method="post" onSubmit={onSubmit} noValidate>
            {said ? (
              said.tone === 'danger' ? (
                <Notice tone="danger">{said.text}</Notice>
              ) : (
                // A warning notice has no role of its own; this one answers the button just pressed.
                <div role="alert">
                  <Notice tone={said.tone}>{said.text}</Notice>
                </div>
              )
            ) : unreachable ? (
              <Notice tone="danger">The server did not answer.</Notice>
            ) : null}
            <Field
              label="Username"
              id="signin-user"
              name="username"
              mono
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
              value={name}
              onChange={(e) => setName(e.currentTarget.value)}
            />
            <Field
              label="Password"
              id="signin-pass"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
            />
            <Button variant="primary" type="submit" busy={busy} busyLabel="Signing in">
              Sign in
            </Button>
          </form>
        ) : (
          <InsecureNotice />
        )}
      </Panel>
      <div className="dl-caption">No account? An admin of this server can add you.</div>
      <details className="dl-disclosure">
        <summary>
          <Icon name="chevron-right" size={14} />
          First sign-in on a new server?
        </summary>
        <div className="dl-disclosure__body">
          <p>
            A new server has one account, <code>admin</code>. Its password was printed once, on the
            server’s standard error, the first time it started. Sign in with it, then add everyone
            else’s accounts.
          </p>
        </div>
      </details>
    </main>
  );
}
