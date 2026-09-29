import { allow, expect, OPEN, test } from './fixtures';

test('an open server lands on default runs with no sign-in', async ({ page, watch }) => {
  allow(watch, OPEN());
  await page.goto(`${OPEN()}/`);
  await expect(page).toHaveURL(`${OPEN()}/p/default/runs`);
  await expect(page.getByRole('heading', { level: 1, name: 'default' })).toBeVisible();
  await expect(page.getByText('This server has no users: anyone who can reach it')).toBeVisible();
  await expect(page.getByRole('button', { name: /^Account/ })).toHaveCount(0);
});

test('a run shows its counts and one mark per result', async ({ page, watch }) => {
  allow(watch, OPEN());
  await page.goto(`${OPEN()}/p/default/runs`);
  const row = page.getByRole('listitem').first();
  await expect(row).toContainText('2 failed');
  await expect(row).toContainText('4 passed');
  await expect(row).toContainText('1 skipped');
  await expect(row).toContainText('1 xfailed');
  await expect(row).toContainText('1 xpassed');
  await expect(row).toContainText('1 error');
  // One mark per result once the row's outcomes are read: ten results, the
  // error's bar broken in two.
  await expect(row.locator('svg[role="img"] rect:not(.dl-dotline__base)')).toHaveCount(11);
});

test('opening a run moves within the page, and the run lists what did not pass', async ({
  page,
  watch,
}) => {
  allow(watch, OPEN());
  await page.goto(`${OPEN()}/p/default/runs`);
  await page.evaluate(() => {
    (window as unknown as { __marker: number }).__marker = 1;
  });
  const link = page.locator('.dl-run__id').first();
  const id = await link.getAttribute('title');
  await link.click();
  await expect(page).toHaveURL(`${OPEN()}/runs/${id}`);
  expect(await page.evaluate(() => (window as unknown as { __marker?: number }).__marker)).toBe(1);
  const notPassing = page.locator('section', {
    has: page.getByRole('heading', { name: 'Not passing' }),
  });
  await expect(notPassing).toContainText('test_assert_fails');
  await expect(notPassing).toContainText('expected 3.3V, got 3.38V');
  await expect(notPassing).toContainText('test_fixture_errors');
  await expect(notPassing).toContainText('test_unexpected_pass');
  await expect(notPassing).not.toContainText('test_passes');
});

test('a deep link reloads, and sign-in is not a page on an open server', async ({
  page,
  watch,
}) => {
  allow(watch, OPEN());
  await page.goto(`${OPEN()}/p/default/runs`);
  const id = await page.locator('.dl-run__id').first().getAttribute('title');
  await page.goto(`${OPEN()}/runs/${id}`);
  await page.reload();
  await expect(page.getByRole('heading', { level: 1 })).toContainText((id ?? '').slice(0, 8));
  await page.goto(`${OPEN()}/sign-in`);
  await expect(page).toHaveURL(`${OPEN()}/p/default/runs`);
});
