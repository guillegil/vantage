import { expect, it } from 'vitest';
import { isFinal } from './queries';

it('takes a run as final once it has an exit status, whatever its presentation says', () => {
  expect(isFinal({ exit_status: 0 })).toBe(true);
  expect(isFinal({ exit_status: 2 })).toBe(true);
  // Running, or abandoned: vantage push may still deliver its end.
  expect(isFinal({ exit_status: null })).toBe(false);
  expect(isFinal(undefined)).toBe(false);
});
