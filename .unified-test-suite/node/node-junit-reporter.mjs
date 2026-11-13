// node-junit-reporter.mjs — node:test reporter injected via NODE_OPTIONS by
// with-node-junit.sh. Writes one JUnit file PER node process into $NODE_TEST_JUNIT_DIR,
// so several `node --test` invocations inside `npm run verify:product` never overwrite
// each other. Yields nothing (its destination is stderr, which stays empty).
import { junit } from 'node:test/reporters';
import { createWriteStream, mkdirSync } from 'node:fs';

export default async function* nodeJunitPerProcess(source) {
  const dir = process.env.NODE_TEST_JUNIT_DIR || 'test-results/junit/node';
  mkdirSync(dir, { recursive: true });
  const out = createWriteStream(`${dir}/node-${process.pid}-${Date.now()}.xml`);
  for await (const chunk of junit(source)) out.write(chunk);
  await new Promise((resolve) => out.end(resolve));
}
