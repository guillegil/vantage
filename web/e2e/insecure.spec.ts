import { allow, CLOSED, expect, test } from './fixtures';

// vantage.test resolves to this machine, but the browser does not know it is
// loopback: over plain HTTP it is no secure context, and keeps no cookie.
test.use({ launchOptions: { args: ['--host-resolver-rules=MAP vantage.test 127.0.0.1'] } });

test('plain HTTP to another host explains why sign-in cannot work', async ({ page, watch }) => {
  const port = new URL(CLOSED()).port;
  const origin = `http://vantage.test:${port}`;
  allow(watch, origin);
  // Chromium says so itself: the isolation header needs a secure context too.
  watch.expected.push(/Cross-Origin-Opener-Policy header has been ignored/);
  await page.goto(`${origin}/sign-in`);
  await expect(
    page.getByText('Signing in needs HTTPS or this machine’s own address'),
  ).toBeVisible();
  await expect(page.getByLabel('Password')).toHaveCount(0);
  await expect(page.getByText(`ssh -L ${port}:127.0.0.1:${port} vantage.test`)).toBeVisible();
});
