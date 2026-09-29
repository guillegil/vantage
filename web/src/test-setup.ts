import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach } from 'vitest';

beforeEach(() => {
  // A page served over HTTPS or from loopback, as vantage's own are; a test
  // that needs otherwise stubs it.
  Object.defineProperty(window, 'isSecureContext', {
    value: true,
    configurable: true,
    writable: true,
  });
});

afterEach(() => {
  cleanup();
});
