// The project triage on the closed server, recorded round by round from
// triage.ts: what each run was compared with, and the changes its row, its
// line and a test's history mark.
import AxeBuilder from '@axe-core/playwright';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { ALICE, allow, CLOSED, expect, signIn, TOKEN, TRIAGE, test } from './fixtures';

const label = (id: string) => id.slice(0, 8);
const auth = () => ({ Authorization: `Bearer ${TOKEN()}` });
// Rounds follow each other within seconds.
const EARLIER = '(under 1 s|\\d+ s) earlier';

// Signs alice in on the way to `path`.
async function visit(page: Page, path: string): Promise<void> {
  await page.goto(`${CLOSED()}${path}`);
  await signIn(page, 'alice', ALICE());
  await expect(page).toHaveURL(`${CLOSED()}${path}`);
}

// The killed session the tests keep running: its heartbeat, as its plugin would have sent.
async function keepRunning(request: APIRequestContext): Promise<void> {
  const answer = await request.post(`${CLOSED()}/api/v1/runs/${TRIAGE().running}/heartbeat`, {
    headers: auth(),
  });
  expect(answer.status()).toBe(200);
}

async function comparison(request: APIRequestContext, id: string) {
  const answer = await request.get(`${CLOSED()}/api/v1/runs/${id}`, { headers: auth() });
  expect(answer.status()).toBe(200);
  return (await answer.json()) as {
    presentation: string;
    comparison: { state: string; counts: Record<string, number> | null };
  };
}

function row(page: Page, id: string): Locator {
  return page.locator('.dl-run', { has: page.locator(`.dl-run__id[title="${id}"]`) });
}

// Marks a line draws for its results' changes: bars standing above the track, and rings.
async function marks(line: Locator): Promise<{ lifted: number; rings: number }> {
  return line.evaluate((svg) => ({
    lifted: [...svg.querySelectorAll('.dl-m--failed, .dl-m--error')].filter(
      (r) => Number(r.getAttribute('y')) < 0,
    ).length,
    rings: svg.querySelectorAll('.dl-ring').length,
  }));
}

test('the rounds hold every state a comparison can be in, and every change', async ({
  request,
}) => {
  const runs = TRIAGE();
  await keepRunning(request);
  const states = {
    abandoned: ['pending', 'abandoned'],
    running: ['pending', 'running'],
    first: ['none', 'finished'],
    second: ['branch', 'finished'],
    stopped: ['branch', 'interrupted'],
    branch: ['project', 'finished'],
    norepo: ['project', 'finished'],
    detached: ['project', 'finished'],
  } as const;
  for (const [name, [state, presentation]] of Object.entries(states)) {
    const run = await comparison(request, runs[name as keyof typeof states]);
    expect([name, run.comparison.state, run.presentation]).toEqual([name, state, presentation]);
  }
  expect((await comparison(request, runs.second)).comparison.counts).toEqual({
    new_failure: 2,
    still_failing: 1,
    fixed: 1,
    new_test: 3,
    removed: 1,
    not_reached: 0,
  });
  const stopped = await request.get(
    `${CLOSED()}/api/v1/runs/${runs.stopped}/changes?change=not_reached&change=removed`,
    { headers: auth() },
  );
  const items = ((await stopped.json()) as { items: { node_id: string; change: string }[] }).items;
  expect(items).toContainEqual(
    expect.objectContaining({ node_id: 'triage.py::test_after_the_stop', change: 'not_reached' }),
  );
  expect(items.every((i) => i.change === 'not_reached')).toBe(true);
});

test('a compared run’s row says what changed and marks it, and no other row does', async ({
  page,
  request,
  watch,
}) => {
  allow(watch, CLOSED());
  const runs = TRIAGE();
  await keepRunning(request);
  await visit(page, '/p/triage/runs');
  const second = row(page, runs.second);
  const changes = second.locator('.dl-run__changes');
  await expect(changes.locator('[aria-hidden="true"]')).toHaveText('2 new · 1 fixed');
  await expect(changes).toHaveAttribute(
    'title',
    `2 new failures and 1 fixed, compared with ${label(runs.first)}`,
  );
  await expect(second.locator('svg[role="img"]')).toHaveAttribute(
    'aria-label',
    /; 2 new failures, 1 fixed/,
  );
  await expect
    .poll(() => marks(second.locator('svg[role="img"]')))
    .toEqual({
      lifted: 2,
      rings: 1,
    });
  for (const id of [runs.first, runs.stopped, runs.branch, runs.norepo, runs.detached]) {
    // Every one of them failed something; its marks are drawn once its line is read.
    await expect(row(page, id).locator('.dl-m--failed').first()).toBeAttached();
    await expect(row(page, id).locator('.dl-run__changes')).toHaveCount(0);
    expect(await marks(row(page, id).locator('svg[role="img"]'))).toEqual({
      lifted: 0,
      rings: 0,
    });
  }
  await expect(row(page, runs.running)).toContainText('running');
  await expect(row(page, runs.abandoned)).toContainText('abandoned');
});

test('each run’s page says what it was compared with, or why it was not', async ({
  page,
  request,
  watch,
}) => {
  allow(watch, CLOSED());
  const runs = TRIAGE();
  const sha = runs.commit.slice(0, 7);
  const said: [string, RegExp | string, string | null][] = [
    [runs.first, 'Nothing to compare with yet.', null],
    [
      runs.second,
      new RegExp(`^Compared with ${label(runs.first)} on main, ${EARLIER}$`),
      runs.first,
    ],
    [
      runs.stopped,
      new RegExp(`^Compared with ${label(runs.second)} on main, ${EARLIER}$`),
      runs.second,
    ],
    [
      runs.branch,
      new RegExp(
        `^No earlier complete run on feat/x; compared with ${label(runs.second)} on main, ${EARLIER}$`,
      ),
      runs.second,
    ],
    [
      runs.norepo,
      new RegExp(
        `^Recorded outside a git repository; compared with ${label(runs.branch)} on feat/x, ${EARLIER}$`,
      ),
      runs.branch,
    ],
    [
      runs.detached,
      new RegExp(
        `^No branch recorded \\(detached HEAD at ${sha}\\); compared with ${label(runs.norepo)}, ${EARLIER}$`,
      ),
      runs.norepo,
    ],
    [runs.abandoned, 'Not compared: no end was recorded.', null],
    [runs.running, 'Compared with its baseline once the session ends.', null],
  ];
  await visit(page, `/runs/${runs.first}`);
  for (const [id, sentence, baseline] of said) {
    if (id === runs.running) await keepRunning(request);
    await page.goto(`${CLOSED()}/runs/${id}`);
    const line = page.locator('.dl-runhead__base');
    await expect(line).toHaveText(sentence);
    if (baseline) {
      const link = line.getByRole('link', { name: label(baseline) });
      await expect(link).toHaveAttribute('href', `/runs/${baseline}`);
      await expect(link).toHaveAttribute('title', baseline);
    } else {
      await expect(line.getByRole('link')).toHaveCount(0);
    }
  }
});

test('a run’s line marks its new failures and fixes', async ({ page, watch }) => {
  allow(watch, CLOSED());
  const runs = TRIAGE();
  await visit(page, `/runs/${runs.second}`);
  const line = page.locator('.dl-runhead__line svg[role="img"]');
  await expect(line).toHaveAttribute(
    'aria-label',
    /: 3 failed, 4 passed; 2 new failures, 1 fixed$/,
  );
  await expect.poll(() => marks(line)).toEqual({ lifted: 2, rings: 1 });
  await page.locator('.dl-runhead__base').getByRole('link').click();
  await expect(page).toHaveURL(`${CLOSED()}/runs/${runs.first}`);
  await expect(page.locator('.dl-runhead__line svg[role="img"]')).toHaveAttribute(
    'aria-label',
    /: 2 failed, 2 passed$/,
  );
  await expect(page.locator('.dl-runhead__line .dl-m--failed').first()).toBeAttached();
  expect(await marks(page.locator('.dl-runhead__line svg[role="img"]'))).toEqual({
    lifted: 0,
    rings: 0,
  });
});

test('a test’s history marks how it changed in each run', async ({ page, watch }) => {
  allow(watch, CLOSED());
  const runs = TRIAGE();
  const sha = runs.commit.slice(0, 7);
  const node = 'triage.py::test_breaks';
  await visit(page, `/runs/${runs.second}/result?node_id=${encodeURIComponent(node)}`);
  const check = async () => {
    const slider = page.getByRole('slider', { name: `History of ${node}` });
    // The newest run, at a detached HEAD, failed it as the run before it did.
    await expect(slider).toHaveAttribute(
      'aria-valuetext',
      `${label(runs.detached)} (${sha}): failed, still failing`,
    );
    await slider.focus();
    await slider.press('Home');
    await expect(slider).toHaveAttribute(
      'aria-valuetext',
      `${label(runs.first)} (main at ${sha}): passed`,
    );
    await slider.press('ArrowRight');
    await expect(slider).toHaveAttribute(
      'aria-valuetext',
      `${label(runs.second)} (main at ${sha}): failed, new failure`,
    );
    expect(await marks(slider)).toEqual({ lifted: 1, rings: 0 });
  };
  await check();
  await page.getByRole('link', { name: 'Full history' }).click();
  await expect(page).toHaveURL(/\/p\/triage\/tests\/history\?/);
  await check();
});

for (const scheme of ['light', 'dark'] as const) {
  test(`nothing serious for assistive technology in a compared run, ${scheme}`, async ({
    page,
    watch,
  }) => {
    allow(watch, CLOSED());
    await page.emulateMedia({ colorScheme: scheme });
    const serious = async () => {
      const found = await new AxeBuilder({ page }).analyze();
      return found.violations
        .filter((v) => v.impact === 'serious' || v.impact === 'critical')
        .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
    };
    const runs = TRIAGE();
    await visit(page, '/p/triage/runs');
    await expect(row(page, runs.second).locator('.dl-run__changes')).toBeVisible();
    expect(await serious()).toEqual([]);
    await page.goto(`${CLOSED()}/runs/${runs.second}`);
    await expect(page.locator('.dl-runhead__base')).toContainText('Compared with');
    expect(await serious()).toEqual([]);
  });
}
