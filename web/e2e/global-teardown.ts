import { rmSync } from 'node:fs';

// Stops both servers and removes their databases.
export default async function globalTeardown(): Promise<void> {
  for (const pid of (process.env.VANTAGE_E2E_PIDS ?? '').split(',').filter(Boolean)) {
    try {
      process.kill(Number(pid), 'SIGTERM');
    } catch {
      // already gone
    }
  }
  await new Promise((r) => setTimeout(r, 500));
  const dir = process.env.VANTAGE_E2E_DIR;
  if (dir) rmSync(dir, { recursive: true, force: true });
}
