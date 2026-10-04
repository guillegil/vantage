// The project triage on the closed server, recorded round by round from
// triage.ts: what each run was compared with, the changes its row, its line
// and a test's history mark, and its page's queue of what changed.
import AxeBuilder from '@axe-core/playwright';
import type { APIRequestContext, BrowserContext, Locator, Page } from '@playwright/test';
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
    const line = page.locator('.dl-baseline');
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
  await page.locator('.dl-baseline').getByRole('link').click();
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

const node = (name: string) => `triage.py::${name}`;

// A run's page, with the test the address names selected.
function runAt(id: string, name?: string): string {
  return `${CLOSED()}/runs/${id}${name ? `?node_id=${encodeURIComponent(node(name))}` : ''}`;
}

function queueOf(page: Page): Locator {
  return page.locator('.dl-queue');
}

// The queue's detail: the selected test, under its id.
function detailOf(page: Page): Locator {
  return page.getByRole('region', { name: /^Selected test/ });
}

async function clipboard(page: Page): Promise<string> {
  return page.evaluate(() => navigator.clipboard.readText());
}

async function allowClipboard(context: BrowserContext): Promise<void> {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: CLOSED() });
}

test('a compared run’s page opens on its first new failure and works down its changes', async ({
  page,
  context,
  watch,
}) => {
  allow(watch, CLOSED());
  await allowClipboard(context);
  const runs = TRIAGE();
  const a = label(runs.first);
  await visit(page, `/runs/${runs.second}`);
  const queue = queueOf(page);
  const detail = detailOf(page);
  // The first new failure, chosen without touching the address.
  await expect(detail.getByRole('heading', { level: 2 })).toHaveText(node('test_breaks'));
  await expect(detail.locator('.dl-triage__head')).toContainText(`New failure: passed in ${a}`);
  await expect(page).toHaveURL(runAt(runs.second));
  await expect(queue.locator('.dl-queue__title')).toHaveText(`Changed since ${a}`);
  // The groups in the order a person works through them, the removed tests closed.
  await expect(queue.locator('.dl-queue__ghead')).toHaveText(
    ['New failures 2', 'Still failing 1', 'Fixed 1', 'New tests 3', 'Removed tests 1'],
    { useInnerText: true },
  );
  await expect(queue.getByRole('button', { name: /^Removed tests/ })).toHaveAttribute(
    'aria-expanded',
    'false',
  );
  await expect(queue.getByRole('option', { name: /test_goes_away/ })).toHaveCount(0);
  await expect(queue.getByRole('option', { name: /test_stays_failing/ })).toContainText(
    `failing 2 runs since ${a}`,
  );
  await expect(queue.getByRole('option', { name: /test_gets_fixed/ })).toContainText(
    `failed in ${a}`,
  );

  // The rerun of every new failure, from the line that says what the run was compared with.
  await page
    .locator('.dl-runhead__base')
    .getByRole('button', { name: 'Copy rerun of 2 new failures' })
    .click();
  await expect
    .poll(() => clipboard(page))
    .toBe(`pytest ${node('test_breaks')} ${node('test_arrives_failing')}`);

  // j and k move the selection, each move replacing the address rather than adding to history.
  const depth = await page.evaluate(() => history.length);
  await page.keyboard.press('j');
  await expect(page).toHaveURL(runAt(runs.second, 'test_arrives_failing'));
  await expect(queue.getByRole('option', { selected: true })).toContainText('test_arrives_failing');
  await expect(detail.getByRole('heading', { level: 2 })).toHaveText(node('test_arrives_failing'));
  await expect(detail.locator('.dl-triage__head')).toContainText(
    `New failure: not in ${a}; its first run failed`,
  );
  await page.keyboard.press('j');
  await expect(page).toHaveURL(runAt(runs.second, 'test_stays_failing'));
  await expect(detail.locator('.dl-triage__head')).toContainText(
    `Still failing: 2 runs, since ${a}`,
  );
  await page.keyboard.press('k');
  await page.keyboard.press('k');
  await expect(page).toHaveURL(runAt(runs.second, 'test_breaks'));
  await expect(detail.getByRole('heading', { level: 2 })).toHaveText(node('test_breaks'));
  expect(await page.evaluate(() => history.length)).toBe(depth);

  // c copies the selected test's rerun.
  await page.keyboard.press('c');
  await expect(queue.locator('.dl-queue__foot')).toHaveText(`Copied pytest ${node('test_breaks')}`);
  await expect.poll(() => clipboard(page)).toBe(`pytest ${node('test_breaks')}`);

  // Enter opens its result page; Back comes to the test it left, its heading focused.
  await queue.getByRole('option', { selected: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(
    `${CLOSED()}/runs/${runs.second}/result?node_id=${encodeURIComponent(node('test_breaks'))}`,
  );
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(node('test_breaks'));
  await page.goBack();
  await expect(page).toHaveURL(runAt(runs.second, 'test_breaks'));
  await expect(page.getByRole('heading', { level: 1 })).toBeFocused();
  await expect(queue.getByRole('option', { selected: true })).toContainText('test_breaks');

  // A test the run lacks opens its result in the baseline.
  await queue.getByRole('button', { name: /^Removed tests/ }).click();
  const gone = queue.getByRole('option', { name: /test_goes_away/ });
  await expect(gone).toContainText(`passed in ${a}, not collected in this run`);
  await gone.click();
  await expect(page).toHaveURL(runAt(runs.second, 'test_goes_away'));
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(
    `${CLOSED()}/runs/${runs.first}/result?node_id=${encodeURIComponent(node('test_goes_away'))}`,
  );
});

test('a run’s address selects the test it names, changed or not', async ({ page, watch }) => {
  allow(watch, CLOSED());
  const runs = TRIAGE();
  await visit(page, `/runs/${runs.second}?node_id=${encodeURIComponent(node('test_gets_fixed'))}`);
  const queue = queueOf(page);
  const detail = detailOf(page);
  await expect(queue.getByRole('option', { selected: true })).toContainText('test_gets_fixed');
  await expect(detail.locator('.dl-triage__head')).toContainText(
    `Fixed: failed in ${label(runs.first)}`,
  );
  // A test that did not change is shown outside every group, no row selected.
  await page.goto(runAt(runs.stopped, 'test_gets_fixed'));
  await expect(detail.getByRole('heading', { level: 2 })).toHaveText(node('test_gets_fixed'));
  await expect(detail.locator('.dl-badge--passed')).toHaveText('passed');
  await expect(queue.getByRole('option', { selected: true })).toHaveCount(0);
  // One the run lacks is said so, and the page opens on its first change.
  await page.goto(runAt(runs.first, 'test_arrives'));
  await expect(page.getByText(`This run has no result for ${node('test_arrives')}`)).toBeVisible();
  await expect(detail.getByRole('heading', { level: 2 })).toHaveText(node('test_stays_failing'));
});

test('each run’s queue holds what its comparison found', async ({ page, request, watch }) => {
  allow(watch, CLOSED());
  const runs = TRIAGE();
  const queue = queueOf(page);
  // Stopped by pytest.exit: what it lacks was not reached, never removed.
  await visit(page, `/runs/${runs.stopped}`);
  await expect(page.locator('.dl-pagehead .dl-status')).toContainText('interrupted');
  await expect(queue.locator('.dl-queue__ghead')).toHaveText(['Still failing 3', 'Not reached 2'], {
    useInnerText: true,
  });
  await expect(queue.getByRole('option', { name: /test_after_the_stop/ })).toContainText(
    `passed in ${label(runs.second)}; the run stopped first`,
  );
  // The project's first run: nothing to compare with, so its failures.
  await page.goto(runAt(runs.first));
  await expect(page.locator('.dl-baseline')).toHaveText('Nothing to compare with yet.');
  await expect(queue.locator('.dl-queue__title')).toHaveText('This run');
  await expect(queue.locator('.dl-queue__note')).toHaveText('Nothing to compare with yet.');
  await expect(queue.locator('.dl-queue__ghead')).toHaveText(['Failures 2'], {
    useInnerText: true,
  });
  await expect(queue.getByRole('option')).toHaveText([/test_stays_failing/, /test_gets_fixed/]);
  // On feat/x, compared with main: what still fails, and nothing else changed.
  await page.goto(runAt(runs.branch));
  await expect(page.locator('.dl-baseline')).toHaveText(
    new RegExp(
      `^No earlier complete run on feat/x; compared with ${label(runs.second)} on main, ${EARLIER}$`,
    ),
  );
  await expect(queue.locator('.dl-queue__note')).toHaveText(
    `Nothing changed since ${label(runs.second)}.`,
  );
  await expect(queue.locator('.dl-queue__ghead')).toHaveText(['Still failing 3'], {
    useInnerText: true,
  });
  // Abandoned: never compared, so its failures, and no rerun of new failures.
  await page.goto(runAt(runs.abandoned));
  await expect(page.locator('.dl-baseline')).toHaveText('Not compared: no end was recorded.');
  await expect(page.locator('.dl-runhead__base').getByRole('button')).toHaveCount(0);
  // Running with no results yet: no queue until its session ends.
  await keepRunning(request);
  await page.goto(runAt(runs.running));
  await expect(page.getByText('Results arrive when the session finishes.')).toBeVisible();
  await expect(queue).toHaveCount(0);
});

for (const scheme of ['light', 'dark'] as const) {
  test(`axe finds nothing in a compared run’s row or its queue, ${scheme}`, async ({
    page,
    watch,
  }) => {
    allow(watch, CLOSED());
    await page.emulateMedia({ colorScheme: scheme });
    const violations = async () => {
      const found = await new AxeBuilder({ page }).analyze();
      return found.violations.map(
        (v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
      );
    };
    const runs = TRIAGE();
    await visit(page, '/p/triage/runs');
    await expect(row(page, runs.second).locator('.dl-run__changes')).toBeVisible();
    expect(await violations()).toEqual([]);
    await page.goto(runAt(runs.second));
    const queue = queueOf(page);
    const detail = detailOf(page);
    await expect(detail.locator('.dl-triage__head')).toContainText('New failure');
    // Recorded without failure text, so the evidence says how to record it.
    await expect(detail).toContainText('Failure text was not recorded');
    await expect(detail.getByRole('slider')).toBeVisible();
    await expect(page.locator('.dl-runhead__base').getByRole('button')).toBeEnabled();
    expect(await violations()).toEqual([]);
    await queue.getByRole('button', { name: /^Removed tests/ }).click();
    await expect(queue.getByRole('option', { name: /test_goes_away/ })).toBeVisible();
    expect(await violations()).toEqual([]);
    await queue.getByRole('button', { name: 'All 7 results' }).click();
    await expect(queue.getByRole('option')).toHaveCount(7);
    expect(await violations()).toEqual([]);
    await page.goto(runAt(runs.stopped));
    await expect(queue.getByRole('option', { name: /test_after_the_stop/ })).toBeVisible();
    expect(await violations()).toEqual([]);
  });
}
