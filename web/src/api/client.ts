// The only code that speaks HTTP. Every request goes to this origin's
// /api/v1 with the browser's own same-origin cookie; nothing here sets
// Authorization, logs a body, or keeps API data in web storage.
import createClient from 'openapi-fetch';
import type { components, paths } from './v1';

export type Schemas = components['schemas'];
export type Rejection = Schemas['Rejection'];

// A refusal from the server, as its Rejection body names it, or, with
// status 0 and error "unreachable", no answer at all.
export class ApiError extends Error {
  readonly status: number;
  readonly error: string;
  readonly detail: string;
  readonly fields: string[];

  constructor(status: number, error: string, detail: string, fields: string[] = []) {
    super(`${status} ${error}`);
    this.name = 'ApiError';
    this.status = status;
    this.error = error;
    this.detail = detail;
    this.fields = fields;
  }
}

function isRejection(body: unknown): body is Rejection {
  return (
    typeof body === 'object' &&
    body !== null &&
    typeof (body as Rejection).error === 'string' &&
    typeof (body as Rejection).detail === 'string'
  );
}

export const api = createClient<paths>({
  // Absolute, since a Request needs one outside a page (in tests).
  baseUrl: `${window.location.origin}/api/v1`,
  credentials: 'same-origin',
  headers: { Accept: 'application/json' },
  // Looked up on every call, so a test can stand in for the network.
  fetch: (request) => globalThis.fetch(request),
});

interface Answer<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

// The body of a 2xx answer; anything else throws an ApiError.
export async function unwrap<T>(pending: Promise<Answer<T>>): Promise<T> {
  let answer: Answer<T>;
  try {
    answer = await pending;
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new ApiError(0, 'unreachable', 'The server did not answer.');
  }
  const { data, error, response } = answer;
  if (response.ok) return data as T;
  if (isRejection(error)) {
    throw new ApiError(response.status, error.error, error.detail, error.fields ?? []);
  }
  throw new ApiError(
    response.status,
    'unexpected_answer',
    `The server answered ${response.status}.`,
  );
}

export function isApiError(e: unknown, status?: number, error?: string): e is ApiError {
  return (
    e instanceof ApiError &&
    (status === undefined || e.status === status) &&
    (error === undefined || e.error === error)
  );
}
