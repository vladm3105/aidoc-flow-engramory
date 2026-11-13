// .unified-test-suite/playwright/config.ts — canon Playwright configuration (vendored; do not edit).
//
// Project usage (e.g. tests/e2e/playwright.config.ts):
//
//   import { defineCanonConfig, apiProject, browserProject } from '../../.unified-test-suite/playwright/config';
//   export default defineCanonConfig({
//     testDir: './tests',
//     projects: [
//       apiProject('api', { testMatch: '**/api/**/*.@(spec|test).ts' }),
//       browserProject('chromium', { testIgnore: '**/api/**' }),
//     ],
//   });
//
// What the canon enforces (CANON.md §4):
//  * Output isolation — Playwright DELETES outputDir and the HTML folder at the start of every run.
//    Everything goes to <results>/reports/<PW_RUN_LABEL>/ (label = layer name, set by `uts layer`);
//    ad-hoc runs use "adhoc-playwright" and can never clobber layer results.
//  * Reporters: list + html + json (read by the gate: flaky status, tags, file:line) + junit.
//  * Workers = size of the identity pool / identities per worker (identity.ts) — never two workers
//    sharing one account. No pool → 1 worker.
//  * Traces/screenshots: off for API projects; on-failure for browser projects (they contain tokens —
//    CI uploads them only on failure, short retention).
//  * forbidOnly in CI; a readiness globalSetup that fails with an INFRA: message.
import { defineConfig, devices, type PlaywrightTestConfig, type Project } from '@playwright/test';
import path from 'node:path';
import { readFileSync, existsSync } from 'node:fs';
import { poolWorkers } from './identity';

// Repo root = nearest ancestor holding unified-test-suite.config.json (so running Playwright from a
// sub-directory still finds the canon and the config).
function findRoot(start: string): string {
  let d = path.resolve(start);
  for (;;) {
    if (existsSync(path.join(d, 'unified-test-suite.config.json'))) return d;
    const up = path.dirname(d);
    if (up === d) return path.resolve(start);
    d = up;
  }
}
const ROOT = findRoot(process.cwd());
const CI = !!process.env.CI;

type UtsConfig = { resultsDir?: string; stack?: { backendUrl?: string | null; appUrl?: string | null; healthPath?: string }; e2e?: { identityPoolEnv?: string; identitiesPerWorker?: number }; providers?: Record<string, { env?: string; modes?: Record<string, string> }> | null };
function projectConfig(): UtsConfig {
  try {
    return JSON.parse(readFileSync(path.join(ROOT, 'unified-test-suite.config.json'), 'utf8'));
  } catch {
    return {};
  }
}
const UTS = projectConfig();

// Direct `npx playwright test` repro runs get the same defaults `uts layer` exports.
// Exported env always wins; workers inherit process.env from here.
const setDefault = (k: string, v: unknown) => { if (process.env[k] === undefined && v != null) process.env[k] = String(v); };
setDefault('BACKEND_URL', UTS.stack?.backendUrl);
setDefault('APP_URL', UTS.stack?.appUrl);
setDefault('BACKEND_HEALTH_PATH', UTS.stack?.healthPath);
setDefault('PW_IDENTITY_POOL_ENV', UTS.e2e?.identityPoolEnv);
setDefault('PW_IDENTITIES_PER_WORKER', UTS.e2e?.identitiesPerWorker);
// Provider switches: direct runs use the provider's "default" mode (gates get theirs from `uts layer`).
for (const p of Object.values(UTS.providers ?? {})) if (p?.env) setDefault(p.env, p.modes?.default);

function resultsDir(): string {
  return path.resolve(ROOT, process.env.RESULTS_DIR ?? UTS.resultsDir ?? 'test-results');
}

export function outputRoot(): string {
  return path.join(resultsDir(), 'reports', process.env.PW_RUN_LABEL ?? 'adhoc-playwright');
}

/** API project: request fixture only, deterministic (no retries), no traces (they would hold tokens). */
export function apiProject(name: string, overrides: Partial<Project> = {}): Project {
  return {
    name,
    timeout: 15_000,
    retries: 0,
    ...overrides,
    use: {
      baseURL: process.env.BACKEND_URL,
      extraHTTPHeaders: { 'X-Test-Run-Id': process.env.TEST_RUN_ID ?? 'local' },
      trace: 'off',
      screenshot: 'off',
      video: 'off',
      ...(overrides.use ?? {}),
    },
  };
}

/** Browser project: one retry (a retried pass is reported as `flaky`, never silently green). */
export function browserProject(name: string, overrides: Partial<Project> = {}, device = 'Desktop Chrome'): Project {
  return {
    name,
    timeout: 60_000,
    retries: 1,
    ...overrides,
    use: {
      ...devices[device],
      baseURL: process.env.APP_URL,
      navigationTimeout: 15_000,
      trace: 'retain-on-failure',
      screenshot: 'only-on-failure',
      video: 'off',
      extraHTTPHeaders: { 'X-Test-Run-Id': process.env.TEST_RUN_ID ?? 'local' },
      ...(overrides.use ?? {}),
    },
  };
}

type CanonOptions = Omit<PlaywrightTestConfig, 'outputDir' | 'reporter' | 'workers'> & {
  /** Extra reporters appended after the canon ones. */
  extraReporters?: PlaywrightTestConfig['reporter'];
};

export function defineCanonConfig(options: CanonOptions) {
  const OUT = outputRoot();
  const { extraReporters, globalSetup, ...rest } = options;
  // Absolute path from the repo root (works for CJS and ESM projects; Playwright runs from the root).
  const setups = [path.join(ROOT, '.unified-test-suite', 'playwright', 'global-setup.ts'), ...(globalSetup ? [globalSetup].flat() : [])];
  return defineConfig({
    forbidOnly: CI,
    ...rest,
    globalSetup: setups as unknown as string,
    outputDir: path.join(OUT, 'artifacts'),
    workers: poolWorkers(),
    reporter: [
      ['list'],
      ['html', { outputFolder: path.join(OUT, 'html'), open: 'never' }],
      ['json', { outputFile: path.join(OUT, 'results.json') }],
      ['junit', { outputFile: path.join(OUT, 'junit.xml') }],
      ...((extraReporters as []) ?? []),
    ],
    use: { trace: 'off', screenshot: 'off', video: 'off', ...(rest.use ?? {}) },
  });
}
