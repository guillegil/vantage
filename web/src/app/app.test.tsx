// The client end to end in jsdom, against a stand-in for the server: who is
// asking, signing in and out, and a session that ends while a page is open.
import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createQueryClient } from './queryClient';
import { routes } from './router';
import { clearSessionEnded } from './sessionEnd';

type Handler = (method: string, path: string, request: Request) => Response | undefined;

const ID = '0123abcd0123abcd0123abcd0123abcd';
const COUNTS = { passed: 3, failed: 1, error: 0, skipped: 0, xfailed: 0, xpassed: 0 };
const RUN = {
  id: ID,
  started_at: '2026-09-27T09:00:00Z',
  finished_at: '2026-09-27T09:00:05Z',
  exit_status: 1,
  interrupted: false,
  presentation: 'finished',
  vcs: null,
  recorded_by: 'alice',
  counts: COUNTS,
};
const ALICE = {
  open: false,
  user: { name: 'alice', admin: true },
  expires_at: '2026-09-27T21:14:00Z',
};

function json(status: number, body?: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

function refuse(status: number, error: string, detail = error): Response {
  return json(status, { error, detail, fields: [] });
}

// A closed server where alice is signed in, unless a handler says otherwise.
function server(overrides: Handler = () => undefined) {
  const calls: string[] = [];
  const fetch = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    const path = url.pathname.replace(/^\/api\/v1/, '');
    calls.push(`${request.method} ${path}${url.search}`);
    const answer = overrides(request.method, path, request);
    if (answer) return answer;
    if (path === '/session' && request.method === 'GET') return json(200, ALICE);
    if (path === '/projects') {
      return json(200, { items: [{ name: 'default', created_at: RUN.started_at, role: 'owner' }] });
    }
    if (path === '/projects/default/runs') {
      return json(200, {
        items: [RUN],
        has_more: false,
        next_cursor: null,
        metadata_horizon: null,
      });
    }
    if (path === `/runs/${ID}/outcomes`) return json(200, { outcomes: '..F.' });
    return refuse(404, 'not_found');
  });
  vi.stubGlobal('fetch', fetch);
  return calls;
}

function renderAt(path: string) {
  const queryClient = createQueryClient();
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, queryClient };
}

beforeEach(() => {
  clearSessionEnded();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('who is asking', () => {
  it('sends a browser that is not signed in to sign in, keeping where it was going', async () => {
    server((method, path) =>
      method === 'GET' && path === '/session' ? refuse(401, 'unauthenticated') : undefined,
    );
    const { router } = renderAt('/p/default/runs?x=1');
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(router.state.location.search).toBe('?next=%2Fp%2Fdefault%2Fruns%3Fx%3D1');
    expect(await screen.findByLabelText('Username')).toBeInTheDocument();
    expect(
      screen.getByText('Sign in again to go back to /p/default/runs?x=1.'),
    ).toBeInTheDocument();
  });

  it('shows the signed-in user and when the session ends', async () => {
    server();
    renderAt('/p/default/runs');
    await userEvent.click(await screen.findByRole('button', { name: 'Account: alice' }));
    expect(screen.getByRole('menu', { name: 'Account' })).toHaveTextContent(
      'Signed in as alice until 21:14 UTC',
    );
  });

  it('has no account and no sign-in on a server with no users', async () => {
    server((method, path) =>
      method === 'GET' && path === '/session'
        ? json(200, { open: true, user: null, expires_at: null })
        : undefined,
    );
    const { router } = renderAt('/sign-in?next=/p/default/runs');
    await waitFor(() => expect(router.state.location.pathname).toBe('/p/default/runs'));
    expect(await screen.findByText(/This server has no users/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Account/ })).toBeNull();
  });
});

describe('a session that ends while a page is open', () => {
  it('keeps the page, says so, and signs in again to the same place', async () => {
    let revoked = false;
    server((method, path) => {
      if (revoked && path !== '/session') return refuse(401, 'unauthenticated');
      if (revoked && method === 'GET' && path === '/session') return refuse(401, 'unauthenticated');
      return undefined;
    });
    const { router, queryClient } = renderAt('/p/default/runs');
    expect(await screen.findByRole('link', { name: '0123abcd' })).toBeInTheDocument();
    revoked = true;
    await act(() => queryClient.refetchQueries({ queryKey: ['projects'] }));
    expect(await screen.findByText(/^Your session ended at \d\d:\d\d UTC$/)).toBeInTheDocument();
    // What was on the page stays.
    expect(screen.getByRole('link', { name: '0123abcd' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Sign in again' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(router.state.location.search).toBe('?next=%2Fp%2Fdefault%2Fruns');
  });

  it('sends a link followed after it ended to sign in first', async () => {
    let revoked = false;
    server((_method, path) =>
      revoked && path !== '/session' ? refuse(401, 'unauthenticated') : undefined,
    );
    const { router, queryClient } = renderAt('/p/default/runs');
    const link = await screen.findByRole('link', { name: '0123abcd' });
    revoked = true;
    await act(() => queryClient.refetchQueries({ queryKey: ['projects'] }));
    await screen.findByText(/^Your session ended/);
    fireEvent.click(link);
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(router.state.location.search).toBe(`?next=%2Fruns%2F${ID}`);
  });
});

describe('signing in', () => {
  function signInServer(answer: (request: Request) => Response) {
    return server((method, path, request) => {
      if (method === 'GET' && path === '/session') return refuse(401, 'unauthenticated');
      if (method === 'POST' && path === '/session') return answer(request);
      return undefined;
    });
  }

  async function submit(name: string, password: string) {
    await userEvent.type(await screen.findByLabelText('Username'), name);
    await userEvent.type(screen.getByLabelText('Password'), password);
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  }

  it('posts the name and password as JSON, and says when they do not match', async () => {
    const bodies: unknown[] = [];
    signInServer((request) => {
      void request
        .clone()
        .json()
        .then((b) => bodies.push(b));
      return refuse(401, 'invalid_credentials');
    });
    renderAt('/sign-in');
    await submit('alice', 'wrong password');
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "That username and password don't match an account on this server.",
    );
    expect(bodies).toEqual([{ name: 'alice', password: 'wrong password' }]);
    expect(screen.getByLabelText('Username')).toHaveValue('alice');
    expect(screen.getByLabelText('Password')).toHaveValue('');
  });

  it('keeps what was typed while the server is busy', async () => {
    signInServer(() => refuse(503, 'password_checks_busy'));
    renderAt('/sign-in');
    await submit('alice', 'a long enough password');
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The server is busy checking other sign-ins. Try again in a few seconds.',
    );
    expect(screen.getByLabelText('Password')).toHaveValue('a long enough password');
  });

  it('says so when the server does not answer', async () => {
    server((method, path) => {
      if (method === 'GET' && path === '/session') return refuse(401, 'unauthenticated');
      if (method === 'POST' && path === '/session') throw new TypeError('Failed to fetch');
      return undefined;
    });
    renderAt('/sign-in');
    await submit('alice', 'a long enough password');
    expect(await screen.findByRole('alert')).toHaveTextContent('The server did not answer.');
  });

  it('goes where it was asked to, forgetting what was read before', async () => {
    const calls = signInServer(() => json(201, ALICE, { 'Cache-Control': 'no-store' }));
    const { router, queryClient } = renderAt('/sign-in?next=%2Fp%2Fdefault%2Fruns');
    queryClient.setQueryData(['projects'], {
      items: [{ name: 'secret', created_at: RUN.started_at, role: 'owner' }],
    });
    await submit('alice', 'a long enough password');
    await waitFor(() => expect(router.state.location.pathname).toBe('/p/default/runs'));
    expect(calls).toContain('POST /session');
    await waitFor(() =>
      expect(
        queryClient.getQueryData<{ items: { name: string }[] }>(['projects'])?.items[0]?.name,
      ).toBe('default'),
    );
  });

  it('sends no password where the browser would not keep the session', async () => {
    const calls = signInServer(() => json(201, ALICE));
    vi.stubGlobal('isSecureContext', false);
    renderAt('/sign-in');
    expect(
      await screen.findByText('Signing in needs HTTPS or this machine’s own address'),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('Password')).toBeNull();
    expect(screen.getByText(/^ssh -L \d+:127\.0\.0\.1:\d+ /)).toBeInTheDocument();
    expect(calls).not.toContain('POST /session');
  });
});

describe('signing out', () => {
  it('ends the session on the server and forgets everything read', async () => {
    const calls = server((method, path) =>
      method === 'DELETE' && path === '/session' ? json(204) : undefined,
    );
    const { router, queryClient } = renderAt('/p/default/runs');
    await userEvent.click(await screen.findByRole('button', { name: 'Account: alice' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(calls).toContain('DELETE /session');
    expect(queryClient.getQueryData(['projects'])).toBeUndefined();
    expect(queryClient.getQueryData(['runs', 'default'])).toBeUndefined();
  });
});
