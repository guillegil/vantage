// Two real vantage servers, on loopback ports of their own: a closed one,
// with users alice (an admin) and bob (a viewer of firmware), and an open
// one serving a local store's database. The closed one has recorded the
// suite in suite.ts once, and the rounds of triage.ts into the project
// triage; the open one the suite twice. Their addresses, the passwords,
// alice's token and the triage runs reach the tests through the
// environment.
import { type ChildProcess, execFileSync, spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { SUITE } from './suite.ts';
import { TRIAGE } from './triage.ts';

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

// The closed server's grace period: short, so a session killed early in the setup reads as
// abandoned by the time the tests run, while the triage tests keep another one running by
// sending its heartbeat themselves.
export const GRACE_SECONDS = 15;

function pytest(
  dir: string,
  args: string[],
  env: Record<string, string> = {},
  file = 'suite.py',
): void {
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
        // A parameter's id keeps the characters it was given, as the suite's U+202E.
        '-o',
        'disable_test_id_escaping_and_forfeit_all_rights_to_community_support=true',
        ...args,
        file,
      ],
      { cwd: dir, env: cleanEnv(env), encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
    );
  } catch (error) {
    // The suites fail on purpose, or stop (2); only a run that was not recorded is a failure
    // here, which the triage rounds check by reading their run back.
    const status = (error as { status?: number }).status;
    if (status !== 1 && status !== 2) throw error;
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

async function serve(database: string, port: number, extra: string[] = []): Promise<string> {
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
      ...extra,
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

function git(dir: string, args: string[]): string {
  return execFileSync(
    'git',
    [
      '-c',
      'user.name=vantage e2e',
      '-c',
      'user.email=e2e@vantage.invalid',
      '-c',
      'commit.gpgsign=false',
      ...args,
    ],
    { cwd: dir, env: cleanEnv(), encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
  ).trim();
}

export interface TriageRuns {
  // Killed first, so it is abandoned once the grace period has passed.
  abandoned: string;
  // Round a on main: the project's first run, with nothing to compare with.
  first: string;
  // Round b on main: compared with first on its branch.
  second: string;
  // Round c on main: stopped by pytest.exit, compared with second.
  stopped: string;
  // Round b on feat/x: no earlier complete run there, so compared with second on main.
  branch: string;
  // Round b outside any repository: compared with branch.
  norepo: string;
  // Round b at a detached HEAD: compared with norepo, which has no branch.
  detached: string;
  // The commit every round in the repository ran at.
  commit: string;
  // Killed last, and kept running by the tests' own heartbeats.
  running: string;
}

// Records each round of triage.py into the project triage on the closed server, and names
// the run each made, read back through the API.
async function recordTriage(
  dir: string,
  base: string,
  token: string,
): Promise<{ runs: TriageRuns; abandonedAt: number }> {
  const repo = join(dir, 'triage');
  const norepo = join(dir, 'norepo');
  mkdirSync(repo);
  mkdirSync(norepo);
  for (const at of [repo, norepo]) writeFileSync(join(at, 'triage.py'), TRIAGE);
  writeFileSync(join(repo, '.gitignore'), '__pycache__/\n');
  git(repo, ['init', '--quiet', '--initial-branch=main']);
  git(repo, ['add', 'triage.py', '.gitignore']);
  git(repo, ['commit', '--quiet', '-m', 'Add the triage suite']);
  const commit = git(repo, ['rev-parse', 'HEAD']);

  let last: string | null = null;
  const round = async (at: string, name: string, env: Record<string, string> = {}) => {
    pytest(
      at,
      ['--vantage', '--vantage-server', base, '--vantage-project', 'triage'],
      { VANTAGE_TOKEN: token, TRIAGE_ROUND: name, ...env },
      'triage.py',
    );
    const answer = await fetch(`${base}/api/v1/projects/triage/runs?limit=1`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const page = (await answer.json()) as { items: { id: string }[] };
    const id = page.items[0]?.id;
    if (!id || id === last) throw new Error(`triage round ${name} recorded no run`);
    last = id;
    return id;
  };

  const abandoned = await round(repo, 'killed');
  const abandonedAt = Date.now();
  const first = await round(repo, 'a');
  const second = await round(repo, 'b');
  const stopped = await round(repo, 'c');
  git(repo, ['checkout', '--quiet', '-b', 'feat/x']);
  const branch = await round(repo, 'b');
  // Git looks for a repository no higher than the directory holding both.
  const norepoRun = await round(norepo, 'b', { GIT_CEILING_DIRECTORIES: dir });
  git(repo, ['checkout', '--quiet', '--detach', 'main']);
  const detached = await round(repo, 'b');
  const running = await round(repo, 'killed');
  return {
    runs: {
      abandoned,
      first,
      second,
      stopped,
      branch,
      norepo: norepoRun,
      detached,
      commit,
      running,
    },
    abandonedAt,
  };
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
  vantage(['project', 'add', 'triage', '--database', closed]);
  vantage(['project', 'member', 'set', 'firmware', 'bob', 'viewer', '--database', closed]);
  const token = vantage(['token', 'create', 'alice', '--label', 'e2e', '--database', closed]);
  const [closedPort, openPort] = [await freePort(), await freePort()];
  const closedBase = await serve(closed, closedPort, ['--grace-period', String(GRACE_SECONDS)]);
  const triage = await recordTriage(dir, closedBase, token);
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

  // Recorded twice, so a test there has a history of two runs.
  const open = join(dir, 'open.db');
  for (let i = 0; i < 2; i++) {
    pytest(dir, [
      '--vantage',
      '--vantage-mode',
      'local',
      '--vantage-local-database',
      open,
      '--vantage-failure-text',
    ]);
  }
  const openBase = await serve(open, openPort === closedPort ? await freePort() : openPort);
  // The first killed session is abandoned only once the grace period has passed.
  const wait = triage.abandonedAt + (GRACE_SECONDS + 1) * 1000 - Date.now();
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));

  process.env.VANTAGE_E2E_DIR = dir;
  process.env.VANTAGE_E2E_CLOSED = closedBase;
  process.env.VANTAGE_E2E_CLOSED_DB = closed;
  process.env.VANTAGE_E2E_OPEN = openBase;
  process.env.VANTAGE_E2E_ALICE = alice;
  process.env.VANTAGE_E2E_BOB = bob;
  process.env.VANTAGE_E2E_TOKEN = token;
  process.env.VANTAGE_E2E_TRIAGE = JSON.stringify(triage.runs);
  process.env.VANTAGE_E2E_PIDS = servers.map((s) => s.pid).join(',');
}
