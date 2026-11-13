// .unified-test-suite/playwright/identity.ts — one set of test identities per Playwright worker.
//
// The pool is a JSON array in the env var named by e2e.identityPoolEnv (default E2E_USER_POOL),
// e.g. [{"email":"qa1@example.test","otp":"000000"}, …]. Each worker gets a disjoint slice of
// `identitiesPerWorker` entries (e2e.identitiesPerWorker; 2 for suites with two-user tests), so
// parallel workers never share a session. The pool value is redacted from every output.
//
//   import { identity, identities } from '<rel>/.unified-test-suite/playwright/identity';
//   const me = identity<{ email: string; otp: string }>(test.info());
//   const [alice, bob] = identities(test.info(), 2);
import type { TestInfo } from '@playwright/test';

const poolEnv = () => process.env.PW_IDENTITY_POOL_ENV || 'E2E_USER_POOL';
const perWorker = () => Math.max(1, Number(process.env.PW_IDENTITIES_PER_WORKER || 1));

export function pool<T = Record<string, unknown>>(): T[] {
  const raw = process.env[poolEnv()];
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    throw new Error(`${poolEnv()} is not valid JSON (expected an array of identities)`);
  }
}

/** Workers the pool can support without sharing identities (≥ 1). */
export function poolWorkers(): number {
  return Math.max(1, Math.floor(pool().length / perWorker()));
}

/** The n identities reserved for this worker. */
export function identities<T = Record<string, unknown>>(testInfo: TestInfo, n = perWorker()): T[] {
  const all = pool<T>();
  const per = perWorker();
  if (n > per) throw new Error(`requested ${n} identities but e2e.identitiesPerWorker is ${per}`);
  if (!all.length) throw new Error(`${poolEnv()} is empty — set it (CI secret / local env) to a JSON array of test identities`);
  const start = testInfo.parallelIndex * per;
  const slice = all.slice(start, start + n);
  if (slice.length < n) {
    throw new Error(`${poolEnv()} has ${all.length} identities; worker ${testInfo.parallelIndex} needs ${start + n}. Lower workers or grow the pool.`);
  }
  return slice;
}

export function identity<T = Record<string, unknown>>(testInfo: TestInfo): T {
  return identities<T>(testInfo, 1)[0];
}
