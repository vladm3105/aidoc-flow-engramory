#!/usr/bin/env node
// redact.mjs — mask secrets before anything reaches disk or a CI artifact.
//   cmd | node scripts/test/redact.mjs            stream filter (console + logs/<layer>.log)
//   node scripts/test/redact.mjs --in-place f…    rewrite report files a layer produced
// `uts layer` does both internally (lib/exec.mjs); this CLI is for ad-hoc use (e.g. piping a log).
// Binary artifacts (trace.zip, screenshots, the HTML report's embedded data) cannot be redacted;
// see CANON.md §5.4 for how they are contained.
import readline from 'node:readline';
import { readFileSync, writeFileSync } from 'node:fs';
import { redact, redactDocument } from '../lib/core.mjs';

if (process.argv[2] === '--in-place') {
  let changed = 0;
  for (const f of process.argv.slice(3)) {
    const before = readFileSync(f, 'utf8');
    const after = redactDocument(before, f);
    if (after !== before) { writeFileSync(f, after); changed++; }
  }
  if (changed) console.error(`redact: masked secrets in ${changed} report file(s)`);
  process.exit(0);
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
let pemBuffer = null; // multi-line PEM blocks must be redacted as a whole
rl.on('line', (line) => {
  if (pemBuffer !== null) {
    pemBuffer.push(line);
    if (/-----END [A-Z ]*PRIVATE KEY-----/.test(line)) {
      process.stdout.write(redact(pemBuffer.join('\n')) + '\n');
      pemBuffer = null;
    }
    return;
  }
  if (/-----BEGIN [A-Z ]*PRIVATE KEY-----/.test(line) && !/-----END/.test(line)) {
    pemBuffer = [line];
    return;
  }
  process.stdout.write(redact(line) + '\n');
});
rl.on('close', () => {
  if (pemBuffer) process.stdout.write('[REDACTED:PEM-UNTERMINATED]\n');
});
