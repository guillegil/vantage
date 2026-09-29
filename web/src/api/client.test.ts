import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api, unwrap } from './client';

function answer(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('client', () => {
  it('asks this origin, with the same-origin cookie and never a token', async () => {
    const fetch = vi.fn(async (_request: Request) => answer(200, { items: [] }));
    vi.stubGlobal('fetch', fetch);
    await unwrap(api.GET('/projects'));
    const request = (fetch.mock.calls[0] ?? [])[0] as Request;
    expect(request.url).toBe(`${window.location.origin}/api/v1/projects`);
    expect(request.credentials).toBe('same-origin');
    expect(request.headers.get('Accept')).toBe('application/json');
    expect(request.headers.has('Authorization')).toBe(false);
  });

  it('repeats an array parameter once per value, in order', async () => {
    const fetch = vi.fn(async (_request: Request) => answer(200, { items: [], has_more: false }));
    vi.stubGlobal('fetch', fetch);
    await unwrap(
      api.GET('/runs/{run_id}/results', {
        params: {
          path: { run_id: 'a'.repeat(32) },
          query: { outcome: ['failed', 'error', 'xpassed'], limit: 100 },
        },
      }),
    );
    const [request] = fetch.mock.calls[0] ?? [];
    const url = new URL((request as Request).url);
    expect(url.searchParams.getAll('outcome')).toEqual(['failed', 'error', 'xpassed']);
    expect(url.search).toContain('outcome=failed&outcome=error&outcome=xpassed');
  });

  it('turns a rejection into an ApiError with its fields', async () => {
    vi.stubGlobal('fetch', async () =>
      answer(422, {
        error: 'invalid_parameter',
        detail: 'outcome is not one of the six',
        fields: ['query.outcome'],
      }),
    );
    const refused = unwrap(api.GET('/projects'));
    await expect(refused).rejects.toBeInstanceOf(ApiError);
    await expect(unwrap(api.GET('/projects'))).rejects.toMatchObject({
      status: 422,
      error: 'invalid_parameter',
      detail: 'outcome is not one of the six',
      fields: ['query.outcome'],
    });
  });

  it('calls no answer unreachable', async () => {
    vi.stubGlobal('fetch', async () => {
      throw new TypeError('Failed to fetch');
    });
    await expect(unwrap(api.GET('/projects'))).rejects.toMatchObject({
      status: 0,
      error: 'unreachable',
    });
  });

  it('names an answer that is not a rejection by its status', async () => {
    vi.stubGlobal('fetch', async () => new Response('Internal Server Error', { status: 500 }));
    await expect(unwrap(api.GET('/projects'))).rejects.toMatchObject({
      status: 500,
      error: 'unexpected_answer',
    });
  });
});
