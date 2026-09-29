import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { allow, expect, OPEN, test } from './fixtures';

const RLO = String.fromCodePoint(0x202e);
const IMG = '<img src=x onerror=window.__pwned=1>';
const SCRIPT = '</script><script>window.__pwned=1</script>';

// The open server's runs, newest first: it recorded the suite twice.
async function runs(page: Page): Promise<[string, string]> {
  await page.goto(`${OPEN()}/p/default/runs`);
  const ids = page.locator('.dl-run__id');
  await expect(ids).toHaveCount(2);
  return [
    (await ids.nth(0).getAttribute('title')) ?? '',
    (await ids.nth(1).getAttribute('title')) ?? '',
  ];
}

function resultOf(id: string, node: string): string {
  return `${OPEN()}/runs/${id}/result?node_id=${encodeURIComponent(node)}`;
}

function section(page: Page, title: string) {
  return page.locator('section', { has: page.getByRole('heading', { level: 2, name: title }) });
}

async function nothingRan(page: Page): Promise<void> {
  await expect(page.locator('main img')).toHaveCount(0);
  expect(
    await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned),
  ).toBeUndefined();
}

test('a failing result opens from its run, with its evidence and its history', async ({
  page,
  watch,
}) => {
  allow(watch, OPEN());
  const [newest, older] = await runs(page);
  await page.locator('.dl-run__id').first().click();
  await section(page, 'Not passing')
    .getByRole('link', { name: 'suite.py::test_assert_fails' })
    .click();
  await expect(page).toHaveURL(resultOf(newest, 'suite.py::test_assert_fails'));
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('suite.py::test_assert_fails');
  await expect(page.locator('.dl-badge--failed')).toHaveText('failed');
  const traceback = section(page, 'Traceback');
  await expect(traceback).toContainText('expected 3.3V, got 3.38V');
  await expect(traceback).toContainText('call phase · suite.py:');
  await expect(traceback).toContainText('credentials included');
  await expect(section(page, 'Message')).toContainText('AssertionError: expected 3.3V');
  await expect(
    page.getByRole('img', { name: /^setup .*, call .* failed, teardown / }),
  ).toBeVisible();

  const slider = page.getByRole('slider', { name: 'History of suite.py::test_assert_fails' });
  // The newer run was compared with the older, where the test failed too.
  await expect(slider).toHaveAttribute(
    'aria-valuetext',
    `${newest.slice(0, 8)}: failed, still failing`,
  );
  await expect(page.getByText(`failing 2 runs since ${older.slice(0, 8)}`)).toBeVisible();
  // The first cell is the older run.
  await slider.click({ position: { x: 4, y: 8 } });
  await expect(page).toHaveURL(resultOf(older, 'suite.py::test_assert_fails'));
  const crumbs = page.getByRole('navigation', { name: 'Breadcrumb' });
  await expect(crumbs.getByRole('link', { name: older.slice(0, 8) })).toBeVisible();

  await page.getByRole('link', { name: 'Full history' }).click();
  await expect(page).toHaveURL(
    `${OPEN()}/p/default/tests/history?node_id=${encodeURIComponent('suite.py::test_assert_fails')}`,
  );
  await expect(page.getByText('All 2 results shown.')).toBeVisible();
  await page
    .getByRole('table')
    .getByRole('link', { name: newest.slice(0, 8) })
    .click();
  await expect(page).toHaveURL(resultOf(newest, 'suite.py::test_assert_fails'));
});

test('hostile text in every evidence field stays text', async ({ page, watch }) => {
  allow(watch, OPEN());
  const [newest] = await runs(page);

  await page.goto(resultOf(newest, 'suite.py::test_hostile_output'));
  await expect(section(page, 'Captured stdout')).toContainText(SCRIPT);
  await expect(section(page, 'Captured stderr')).toContainText(IMG);
  await expect(section(page, 'Traceback')).toContainText(SCRIPT);
  await expect(section(page, 'Message')).toContainText(SCRIPT);
  await expect(section(page, 'Exception repr')).toContainText(SCRIPT);
  await nothingRan(page);

  await page.goto(resultOf(newest, 'suite.py::test_skipped'));
  await expect(section(page, 'Skip reason')).toContainText(`no rig attached ${IMG}`);
  await nothingRan(page);

  await page.goto(resultOf(newest, 'suite.py::test_expected_failure'));
  await expect(section(page, 'Xfail reason')).toContainText(`known drift ${IMG}`);
  await nothingRan(page);

  await page.goto(resultOf(newest, `suite.py::test_hostile_id[${IMG}]`));
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(
    `suite.py::test_hostile_id[${IMG}]`,
  );
  await expect(
    page.getByRole('slider', { name: `History of suite.py::test_hostile_id[${IMG}]` }),
  ).toBeVisible();
  await nothingRan(page);
});

test('characters that reorder or hide text are shown, not obeyed', async ({ page, watch }) => {
  allow(watch, OPEN());
  const [newest] = await runs(page);
  const node = `suite.py::test_hostile_id[${RLO}gnp.exe]`;
  await page.goto(resultOf(newest, node));
  const heading = page.getByRole('heading', { level: 1 });
  await expect(heading).toHaveText('suite.py::test_hostile_id[U+202Egnp.exe]');
  expect(await heading.textContent()).not.toContain(RLO);
  // The tab's title is a string: the override is written out there too.
  await expect(page).toHaveTitle(/^suite\.py::test_hostile_id\[⟨U\+202E⟩gnp\.exe\] · /);
  await expect(
    page.getByRole('slider', { name: 'History of suite.py::test_hostile_id[⟨U+202E⟩gnp.exe]' }),
  ).toBeVisible();

  await page.goto(resultOf(newest, 'suite.py::test_hostile_output'));
  const stdout = section(page, 'Captured stdout');
  // The server stores U+FFFD where the test printed U+0000.
  await expect(stdout).toContainText('reordered: U+202Egnp.exe, nul: U+FFFD, end');
  expect(await stdout.locator('pre').textContent()).not.toContain(RLO);
});

for (const scheme of ['light', 'dark'] as const) {
  test(`nothing serious for assistive technology on a result and a history, ${scheme}`, async ({
    page,
    watch,
  }) => {
    allow(watch, OPEN());
    await page.emulateMedia({ colorScheme: scheme });
    const serious = async () => {
      const found = await new AxeBuilder({ page }).analyze();
      return found.violations
        .filter((v) => v.impact === 'serious' || v.impact === 'critical')
        .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
    };
    const [newest] = await runs(page);
    await page.goto(resultOf(newest, 'suite.py::test_hostile_output'));
    await expect(section(page, 'Captured stderr')).toBeVisible();
    await expect(page.getByRole('slider')).toBeVisible();
    expect(await serious()).toEqual([]);
    await page.goto(resultOf(newest, 'suite.py::test_skipped'));
    await expect(section(page, 'Skip reason')).toBeVisible();
    expect(await serious()).toEqual([]);
    await page.goto(
      `${OPEN()}/p/default/tests/history?node_id=${encodeURIComponent('suite.py::test_assert_fails')}`,
    );
    await expect(page.getByText('All 2 results shown.')).toBeVisible();
    expect(await serious()).toEqual([]);
  });
}
