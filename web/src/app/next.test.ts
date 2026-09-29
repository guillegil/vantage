import { describe, expect, it } from 'vitest';
import { safeNext, signInHref } from './next';

const ORIGIN = 'http://127.0.0.1:8765';

describe('safeNext', () => {
  it.each([
    ['/p/firmware/runs?x=1#y', '/p/firmware/runs?x=1#y'],
    ['/runs/0123', '/runs/0123'],
  ])('keeps a path on this origin: %s', (raw, kept) => {
    expect(safeNext(raw, ORIGIN)).toBe(kept);
  });

  it.each([
    '//evil.example/x',
    '/\\evil.example',
    'https://evil.example/',
    'javascript:alert(1)',
    'p/firmware',
    '',
    null,
  ])('sends anything else home: %s', (raw) => {
    expect(safeNext(raw, ORIGIN)).toBe('/');
  });

  it('makes the sign-in address, leaving out a next of /', () => {
    expect(signInHref('/p/a/runs')).toBe('/sign-in?next=%2Fp%2Fa%2Fruns');
    expect(signInHref('/')).toBe('/sign-in');
    expect(signInHref('//evil')).toBe('/sign-in');
  });
});
