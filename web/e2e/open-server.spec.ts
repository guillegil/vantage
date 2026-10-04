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

test('opening a run moves within the page, and its queue reaches every result', async ({
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
  const older = (await page.locator('.dl-run__id').nth(1).getAttribute('title')) ?? '';
  await link.click();
  await expect(page).toHaveURL(`${OPEN()}/runs/${id}`);
  expect(await page.evaluate(() => (window as unknown as { __marker?: number }).__marker)).toBe(1);
  // The suite failed the same tests both times: nothing changed, and these still fail.
  const queue = page.locator('.dl-queue');
  await expect(queue.locator('.dl-queue__title')).toHaveText(`Changed since ${older.slice(0, 8)}`);
  await expect(queue.locator('.dl-queue__note')).toHaveText(
    `Nothing changed since ${older.slice(0, 8)}.`,
  );
  await expect(
    queue.getByRole('listbox', { name: /^Still failing/ }).getByRole('option'),
  ).toHaveText([
    /suite\.py::test_assert_fails/,
    /suite\.py::test_fixture_errors/,
    /suite\.py::test_hostile_output/,
  ]);
  // The first is selected, with its failure beside it.
  const detail = page.getByRole('region', { name: 'Selected test: suite.py::test_assert_fails' });
  await expect(detail).toContainText('expected 3.3V, got 3.38V');
  await expect(detail).toContainText(`Still failing: 2 runs, since ${older.slice(0, 8)}`);
  // The whole run, in the order pytest reported it, filtered by the server.
  await queue.getByRole('button', { name: 'All 10 results' }).click();
  await expect(queue.locator('.dl-queue__title')).toHaveText('10 results');
  const all = queue.getByRole('listbox', { name: 'Results' }).getByRole('option');
  await expect(all).toHaveCount(10);
  await expect(all.first()).toContainText('suite.py::test_passes');
  await queue.getByRole('radio', { name: 'xpassed 1' }).click();
  await expect(all).toHaveText([/suite\.py::test_unexpected_pass/]);
  await all.first().click();
  await expect(page).toHaveURL(
    `${OPEN()}/runs/${id}?node_id=${encodeURIComponent('suite.py::test_unexpected_pass')}`,
  );
  await expect(
    page.getByRole('region', { name: 'Selected test: suite.py::test_unexpected_pass' }),
  ).toContainText('xpassed');
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
