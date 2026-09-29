import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { ALICE, allow, BOB, CLOSED, expect, signIn, test } from './fixtures';

const REPO = resolve(import.meta.dirname, '../..');

test('a deep link asks to sign in, and returns there', async ({ page, watch }) => {
  allow(watch, CLOSED());
  await page.goto(`${CLOSED()}/p/firmware/runs`);
  await expect(page).toHaveURL(`${CLOSED()}/sign-in?next=%2Fp%2Ffirmware%2Fruns`);
  await signIn(page, 'alice', 'not the password at all');
  await expect(page.getByRole('alert')).toHaveText(
    "That username and password don't match an account on this server.",
  );
  await signIn(page, 'alice', ALICE());
  await expect(page).toHaveURL(`${CLOSED()}/p/firmware/runs`);
  await expect(page.getByRole('heading', { level: 1, name: 'firmware' })).toBeVisible();
  expect(await page.evaluate(() => document.cookie)).toBe('');
  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: 'firmware' })).toBeVisible();
});

test('signing out ends the session for good', async ({ page, watch }) => {
  allow(watch, CLOSED());
  await page.goto(`${CLOSED()}/sign-in`);
  await signIn(page, 'alice', ALICE());
  await expect(page).toHaveURL(/\/p\/[^/]+\/runs$/);
  const [cookie] = await page.context().cookies();
  expect(cookie?.name).toBe('__Host-vantage_session');
  expect(cookie?.httpOnly).toBe(true);
  await page.getByRole('button', { name: 'Account: alice' }).click();
  await expect(page.getByRole('menu', { name: 'Account' })).toContainText(
    'Signed in as alice until',
  );
  await page.getByRole('menuitem', { name: 'Sign out' }).click();
  await expect(page).toHaveURL(`${CLOSED()}/sign-in`);
  const replay = await page.request.get(`${CLOSED()}/api/v1/session`, {
    headers: { Cookie: `${cookie?.name}=${cookie?.value}` },
  });
  expect(replay.status()).toBe(401);
});

test('a session revoked elsewhere asks to sign in again, keeping where it was', async ({
  page,
  watch,
}) => {
  allow(watch, CLOSED());
  await page.goto(`${CLOSED()}/p/firmware/runs`);
  await signIn(page, 'alice', ALICE());
  await expect(page.getByRole('heading', { level: 1, name: 'firmware' })).toBeVisible();
  // Setting alice's password revokes every login token she holds.
  execFileSync(
    'uv',
    [
      'run',
      '--no-sync',
      'vantage',
      'user',
      'password',
      'alice',
      '--password-stdin',
      '--database',
      process.env.VANTAGE_E2E_CLOSED_DB as string,
    ],
    { cwd: REPO, input: ALICE(), stdio: ['pipe', 'ignore', 'pipe'] },
  );
  const link = page.locator('.dl-run__id').first();
  const id = await link.getAttribute('title');
  await link.click();
  await expect(page.getByText(/^Your session ended at \d\d:\d\d UTC$/)).toBeVisible();
  await page.getByRole('button', { name: 'Sign in again' }).click();
  await expect(page).toHaveURL(`${CLOSED()}/sign-in?next=%2Fruns%2F${id}`);
  await signIn(page, 'alice', ALICE());
  await expect(page).toHaveURL(`${CLOSED()}/runs/${id}`);
});

test('a viewer reads the runs of their project', async ({ page, watch }) => {
  allow(watch, CLOSED());
  await page.goto(`${CLOSED()}/p/firmware/runs`);
  await signIn(page, 'bob', BOB());
  await expect(page.getByRole('heading', { level: 1, name: 'firmware' })).toBeVisible();
  await expect(page.locator('.dl-pagehead__sub')).toContainText('Viewer');
  await expect(page.getByRole('listitem').first()).toContainText('2 failed');
});

test('a project the viewer is not a member of says so', async ({ page, watch }) => {
  allow(watch, CLOSED());
  await page.goto(`${CLOSED()}/p/hardware/runs`);
  await signIn(page, 'bob', BOB());
  await expect(page.getByText('You are not a member of hardware')).toBeVisible();
});
