// Two real vantage servers, on loopback ports of their own: a closed one,
// with users alice (an admin) and bob (a viewer of firmware), and an open
// one serving a local store's database. Each has recorded the suite in
// suite.ts. Their addresses and the passwords reach the tests through the
// environment.
import { type ChildProcess, execFileSync, spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { SUITE } from './suite.ts';

const REPO = resolve(import.meta.dirname, '../..');

export const servers: ChildProcess[] = [];

// Without a token or a database of the developer's, whatever their shell holds.
function cleanEnv(extra: Record<string, string> = {}): NodeJS.ProcessEnv {
  const env = { ...process.env, ...extra };
  if (!('VANTAGE_TOKEN' in extra)) delete env.VANTAGE_TOKEN;
  delete env.VANTAGE_DATABASE;
  delete env.PYTEST_ADDOPTS;
  return env;
}

function vantage(args: string[], input?: string): string {
  return execFileSync('uv', ['run', '--no-sync', 'vantage', ...args], {
    cwd: REPO,
    env: cleanEnv(),
    input,
    encoding: 'utf8',
    stdio: ['pipe', 'pipe', 'pipe'],
  }).trim();
}

function pytest(dir: string, args: string[], env: Record<string, string> = {}): void {
  try {
    execFileSync(
      'uv',
      [
        'run',
        '--no-sync',
        '--project',
        REPO,
        'python',
        '-m',
        'pytest',
        '-p',
        'no:cacheprovider',
        ...args,
        'suite.py',
      ],
      { cwd: dir, env: cleanEnv(env), encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
    );
  } catch (error) {
    // The suite fails on purpose; only a run that was not recorded is a failure here.
    const status = (error as { status?: number }).status;
    if (status !== 1) throw error;
  }
}

function freePort(): Promise<number> {
  const tryPort = (port: number) =>
    new Promise<boolean>((done) => {
      const probe = createServer();
      probe.once('error', () => done(false));
      probe.listen(port, '127.0.0.1', () => probe.close(() => done(true)));
    });
  return (async () => {
    for (let attempt = 0; attempt < 200; attempt++) {
      const port = 20000 + Math.floor(Math.random() * 1000);
      if (await tryPort(port)) return port;
    }
    throw new Error('No free port between 20000 and 20999');
  })();
}

async function serve(database: string, port: number): Promise<string> {
  const child = spawn(
    'uv',
    [
      'run',
      '--no-sync',
      'vantage',
      '--database',
      database,
      '--host',
      '127.0.0.1',
      '--port',
      String(port),
    ],
    { cwd: REPO, env: cleanEnv(), stdio: ['ignore', 'ignore', 'pipe'], detached: false },
  );
  servers.push(child);
  const base = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null)
      throw new Error(`vantage on port ${port} exited with ${child.exitCode}`);
    try {
      const answer = await fetch(`${base}/api/v1/capabilities`);
      if (answer.ok) return base;
    } catch {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`vantage on port ${port} did not start`);
}

function password(): string {
  return randomBytes(18).toString('base64url');
}

export default async function globalSetup(): Promise<void> {
  const dir = mkdtempSync(join(tmpdir(), 'vantage-e2e-'));
  writeFileSync(join(dir, 'suite.py'), SUITE);
  const closed = join(dir, 'closed.db');
  const alice = password();
  const bob = password();
  vantage(['user', 'add', 'alice', '--admin', '--database', closed]);
  vantage(['user', 'password', 'alice', '--password-stdin', '--database', closed], alice);
  vantage(['user', 'add', 'bob', '--database', closed]);
  vantage(['user', 'password', 'bob', '--password-stdin', '--database', closed], bob);
  vantage(['project', 'add', 'firmware', '--database', closed]);
  vantage(['project', 'add', 'hardware', '--database', closed]);
  vantage(['project', 'member', 'set', 'firmware', 'bob', 'viewer', '--database', closed]);
  const token = vantage(['token', 'create', 'alice', '--label', 'e2e', '--database', closed]);
  const [closedPort, openPort] = [await freePort(), await freePort()];
  const closedBase = await serve(closed, closedPort);
  pytest(
    dir,
    [
      '--vantage',
      '--vantage-server',
      closedBase,
      '--vantage-project',
      'firmware',
      '--vantage-failure-text',
    ],
    { VANTAGE_TOKEN: token },
  );

  const open = join(dir, 'open.db');
  pytest(dir, [
    '--vantage',
    '--vantage-mode',
    'local',
    '--vantage-local-database',
    open,
    '--vantage-failure-text',
  ]);
  const openBase = await serve(open, openPort === closedPort ? await freePort() : openPort);

  process.env.VANTAGE_E2E_DIR = dir;
  process.env.VANTAGE_E2E_CLOSED = closedBase;
  process.env.VANTAGE_E2E_CLOSED_DB = closed;
  process.env.VANTAGE_E2E_OPEN = openBase;
  process.env.VANTAGE_E2E_ALICE = alice;
  process.env.VANTAGE_E2E_BOB = bob;
  process.env.VANTAGE_E2E_PIDS = servers.map((s) => s.pid).join(',');
}
