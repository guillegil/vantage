// Every test watches the page for what must never happen: a policy
// violation, a script error, or a request to anywhere but the server under
// test. Each is asserted after the test.
import { test as base, expect, type Page } from '@playwright/test';

export const CLOSED = () => process.env.VANTAGE_E2E_CLOSED as string;
export const OPEN = () => process.env.VANTAGE_E2E_OPEN as string;
export const ALICE = () => process.env.VANTAGE_E2E_ALICE as string;
export const BOB = () => process.env.VANTAGE_E2E_BOB as string;

// A refused request is logged by the browser as a console error; that is the
// API answering, not the page failing.
const REFUSED = /^Failed to load resource: the server responded with a status of 4\d\d/;

interface Watch {
  // Console messages a test expects, such as a browser's own warning.
  expected: RegExp[];
  violations: string[];
  errors: string[];
  foreign: string[];
  allowed: Set<string>;
}

export const test = base.extend<{ watch: Watch }>({
  watch: [
    async ({ page }, use) => {
      const watch: Watch = {
        expected: [],
        violations: [],
        errors: [],
        foreign: [],
        allowed: new Set(),
      };
      await page.addInitScript(() => {
        document.addEventListener('securitypolicyviolation', (e) => {
          const w = window as unknown as { __violations?: string[] };
          w.__violations = w.__violations ?? [];
          w.__violations.push(`${e.violatedDirective} ${e.blockedURI}`);
        });
      });
      page.on('console', (message) => {
        const text = message.text();
        if (message.type() !== 'error' || REFUSED.test(text)) return;
        if (!watch.expected.some((pattern) => pattern.test(text))) watch.errors.push(text);
      });
      page.on('pageerror', (error) => watch.errors.push(String(error)));
      page.on('request', (request) => {
        const url = new URL(request.url());
        if (url.protocol === 'data:' || url.protocol === 'about:') return;
        watch.foreign.push(url.origin);
      });
      await use(watch);
      const violations = await collectViolations(page);
      expect(violations, 'policy violations').toEqual([]);
      expect(watch.errors, 'console errors').toEqual([]);
      const origins = new Set(watch.foreign);
      for (const origin of watch.allowed) origins.delete(origin);
      expect([...origins], 'requests to another origin').toEqual([]);
    },
    { auto: true },
  ],
});

async function collectViolations(page: Page): Promise<string[]> {
  try {
    return await page.evaluate(
      () => (window as unknown as { __violations?: string[] }).__violations ?? [],
    );
  } catch {
    return [];
  }
}

// Allows the server under test, and only it.
export function allow(watch: Watch, base: string): void {
  watch.allowed.add(new URL(base).origin);
}

export async function signIn(page: Page, name: string, password: string): Promise<void> {
  await page.getByLabel('Username').fill(name);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

// Signs in and waits until the page has left sign-in.
export async function signedIn(page: Page, name: string, password: string): Promise<void> {
  await signIn(page, name, password);
  await expect(page).not.toHaveURL(/\/sign-in/);
}

export { expect };
