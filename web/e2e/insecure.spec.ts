import { allow, CLOSED, expect, OPEN, test } from './fixtures';

// vantage.test and rebound.test resolve to this machine, but the browser does
// not know it is loopback: over plain HTTP neither is a secure context, and
// neither keeps a cookie. The closed server is told vantage.test is one of its
// names; rebound.test is what a page on any domain can point its own name at.
test.use({
  launchOptions: {
    args: ['--host-resolver-rules=MAP vantage.test 127.0.0.1, MAP rebound.test 127.0.0.1'],
  },
});

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

test('a name nobody allowed reaches nothing, not even an open server', async ({ page, watch }) => {
  // What a browser asks once a page on rebound.test has pointed that name here.
  const origin = `http://rebound.test:${new URL(OPEN()).port}`;
  allow(watch, origin);
  // The refusal carries every security header; plain HTTP makes Chromium ignore this one.
  watch.expected.push(/Cross-Origin-Opener-Policy header has been ignored/);
  for (const path of ['/', '/api/v1/projects/default/runs']) {
    const answer = await page.goto(`${origin}${path}`);
    expect(answer?.status(), path).toBe(421);
    const body = await answer?.text();
    expect(JSON.parse(body ?? '{}').error, path).toBe('misdirected_request');
    expect(body, path).not.toContain('rebound');
  }
});
