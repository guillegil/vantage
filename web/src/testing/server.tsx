// A stand-in for the server, for tests of whole pages in jsdom: alice is
// signed in on a closed server, and each test answers what its page asks.
import { QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { vi } from 'vitest';
import { createQueryClient } from '../app/queryClient';
import { routes } from '../app/router';

export type Handler = (
  method: string,
  path: string,
  query: URLSearchParams,
  request: Request,
) => Response | undefined;

export const ALICE = {
  open: false,
  user: { name: 'alice', admin: true },
  expires_at: '2026-09-27T21:14:00Z',
};

export function json(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function refuse(status: number, error: string, detail = error): Response {
  return json(status, { error, detail, fields: [] });
}

// Every request the page made, as "GET /path?query", in order.
export function stubServer(answer: Handler): string[] {
  const calls: string[] = [];
  const fetch = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    const path = url.pathname.replace(/^\/api\/v1/, '');
    calls.push(`${request.method} ${path}${url.search}`);
    const given = answer(request.method, path, url.searchParams, request);
    if (given) return given;
    if (path === '/session' && request.method === 'GET') return json(200, ALICE);
    if (path === '/projects') {
      return json(200, {
        items: [
          { name: 'default', created_at: '2026-09-01T00:00:00Z', role: 'editor' },
          { name: 'firmware', created_at: '2026-09-01T00:00:00Z', role: 'viewer' },
        ],
      });
    }
    return refuse(404, 'not_found');
  });
  vi.stubGlobal('fetch', fetch);
  return calls;
}

export function renderAt(path: string) {
  const queryClient = createQueryClient();
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, queryClient };
}
