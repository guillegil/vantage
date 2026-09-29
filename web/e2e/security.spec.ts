import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { ALICE, allow, CLOSED, expect, OPEN, signedIn, test } from './fixtures';

const PAGE_POLICY =
  "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'; require-trusted-types-for 'script'; trusted-types 'none'";

async function firstRun(page: Page, base: string): Promise<string> {
  await page.goto(`${base}/p/default/runs`);
  const id = await page.locator('.dl-run__id').first().getAttribute('title');
  return id ?? '';
}

test('hostile names and failure text stay text', async ({ page, watch }) => {
  allow(watch, OPEN());
  const id = await firstRun(page, OPEN());
  await page.goto(`${OPEN()}/runs/${id}`);
  const all = page.locator('section', { has: page.getByRole('heading', { name: 'All results' }) });
  await expect(all).toContainText('[<img src=x onerror=window.__pwned=1>]');
  const notPassing = page.locator('section', {
    has: page.getByRole('heading', { name: 'Not passing' }),
  });
  await expect(notPassing).toContainText('</script><script>window.__pwned=1</script>');
  await expect(page.locator('main img')).toHaveCount(0);
  expect(
    await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned),
  ).toBeUndefined();
});

test('the page is served under its policy', async ({ page, watch }) => {
  allow(watch, OPEN());
  const answer = await page.goto(`${OPEN()}/`);
  expect(answer?.headers()['content-security-policy']).toBe(PAGE_POLICY);
});

for (const scheme of ['light', 'dark'] as const) {
  test(`nothing serious for assistive technology, ${scheme}`, async ({ page, watch }) => {
    allow(watch, OPEN());
    allow(watch, CLOSED());
    await page.emulateMedia({ colorScheme: scheme });
    const serious = async () => {
      const found = await new AxeBuilder({ page }).analyze();
      return found.violations
        .filter((v) => v.impact === 'serious' || v.impact === 'critical')
        .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
    };
    await page.goto(`${CLOSED()}/sign-in`);
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
    expect(await serious()).toEqual([]);
    await signedIn(page, 'alice', ALICE());
    await page.goto(`${CLOSED()}/p/firmware/runs`);
    await expect(page.getByRole('listitem').first()).toContainText('2 failed');
    expect(await serious()).toEqual([]);
    const id = await firstRun(page, OPEN());
    await expect(page.getByRole('listitem').first()).toContainText('passed');
    expect(await serious()).toEqual([]);
    await page.goto(`${OPEN()}/runs/${id}`);
    await expect(page.getByRole('heading', { name: 'All results' })).toBeVisible();
    await expect(page.locator('table').last()).toContainText('test_passes');
    expect(await serious()).toEqual([]);
  });
}
