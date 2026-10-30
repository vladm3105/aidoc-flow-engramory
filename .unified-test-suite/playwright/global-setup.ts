// .unified-test-suite/playwright/global-setup.ts — fail fast, and legibly, when the stack is not there.
// `uts layer` already health-checks via scripts/backend.sh; this covers direct `npx playwright test`
// repro runs. A thrown error yields zero executed tests, which the gate classifies as infra_error
// (not a test failure) and quotes this message.
import type { FullConfig } from '@playwright/test';

async function reachable(url: string): Promise<string | null> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(5_000) });
    return res.ok ? null : `HTTP ${res.status}`;
  } catch (err) {
    return (err as Error).message;
  }
}

export default async function globalSetup(_config: FullConfig): Promise<void> {
  const problems: string[] = [];
  // Layers declare what they need; `uts layer` sets UTS_NEEDS_BACKEND / UTS_NEEDS_APP. Direct repro runs
  // (no UTS_GATE) check the backend whenever one is configured.
  const backend = process.env.BACKEND_URL;
  const needsBackend = process.env.UTS_NEEDS_BACKEND === '1' || (!process.env.UTS_GATE && Boolean(backend));
  if (backend && needsBackend) {
    const health = `${backend.replace(/\/$/, '')}${process.env.BACKEND_HEALTH_PATH ?? '/health'}`;
    const b = await reachable(health);
    if (b) problems.push(`backend ${health}: ${b}`);
  }
  // The app is checked only for layers that declare needsApp (uts sets UTS_NEEDS_APP=1).
  if (process.env.UTS_NEEDS_APP === '1' && process.env.APP_URL) {
    const a = await reachable(process.env.APP_URL);
    if (a) problems.push(`app ${process.env.APP_URL}: ${a}`);
  }
  if (problems.length) {
    throw new Error(
      `INFRA: test stack not reachable — ${problems.join('; ')}. ` +
        'Run `task test:stack:up` (managed stack) or check BACKEND_URL/APP_URL (unmanaged). `task test:doctor` shows what is missing.',
    );
  }
}
