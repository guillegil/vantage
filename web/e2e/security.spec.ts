import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { ALICE, allow, CLOSED, expect, OPEN, signedIn, test } from './fixtures';

const RLO = String.fromCodePoint(0x202e);
const IMG = '<img src=x onerror=window.__pwned=1>';
const SCRIPT = '</script><script>window.__pwned=1</script>';

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
  const queue = page.locator('.dl-queue');
  // Failure text, in the evidence beside a test the queue holds.
  await queue.getByRole('option').filter({ hasText: 'suite.py::test_hostile_output' }).click();
  await expect(
    page.getByRole('region', { name: 'Selected test: suite.py::test_hostile_output' }),
  ).toContainText(SCRIPT);
  // Hostile node ids, in the whole run and as the selected test, its rerun quoted for a shell.
  await queue.getByRole('button', { name: /^All \d+ results$/ }).click();
  const hostile = queue
    .getByRole('option')
    .filter({ hasText: `suite.py::test_hostile_id[${IMG}]` });
  await hostile.click();
  const detail = page.getByRole('region', {
    name: `Selected test: suite.py::test_hostile_id[${IMG}]`,
  });
  await expect(detail.getByRole('heading', { level: 2 })).toHaveText(
    `suite.py::test_hostile_id[${IMG}]`,
  );
  await expect(detail.locator('.dl-triage__cmd')).toContainText(
    `pytest 'suite.py::test_hostile_id[${IMG}]'`,
  );
  // A character that reorders text is shown as its code point, not obeyed.
  const reordered = queue.getByRole('option').filter({ hasText: 'gnp.exe' });
  await expect(reordered).toContainText('suite.py::test_hostile_id[U+202Egnp.exe]');
  expect(await reordered.textContent()).not.toContain(RLO);
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
    await expect(
      page.getByRole('region', { name: 'Selected test: suite.py::test_assert_fails' }),
    ).toContainText('expected 3.3V, got 3.38V');
    expect(await serious()).toEqual([]);
    await page.getByRole('button', { name: /^All \d+ results$/ }).click();
    await expect(
      page.getByRole('option').filter({ hasText: 'suite.py::test_passes_again' }),
    ).toBeVisible();
    expect(await serious()).toEqual([]);
  });
}
