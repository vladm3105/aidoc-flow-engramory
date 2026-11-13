// canon/lib/exec.mjs — process execution with streaming redaction (portable: Node + bash only).
//
// execLayer: run ONE layer (optional preflight, then command), stream redacted output to the console
//   and <results>/logs/<layer>.log, redact the layer's own report dir in place, and write
//   <results>/meta/<layer>.json. Resolves to 0 (the gate is `uts report`), or to the real exit code
//   when RUN_LAYER_STRICT=1.
// runSteps: run a `steps` layer — every step runs (stdin closed, so no step can swallow the list),
//   one JUnit testcase per step.
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, appendFileSync, readFileSync, readdirSync, statSync, existsSync, rmSync } from 'node:fs';
import path from 'node:path';
import { redact, redactDocument, fingerprint, resultsDir, loadConfig, layerSteps, stripAnsi } from './core.mjs';

/** Line-buffered redacting writer (keeps multi-line PEM blocks together). */
function redactingSink(onText) {
  let buf = '';
  let pem = null;
  const emit = (line) => {
    if (pem !== null) {
      pem.push(line);
      if (/-----END [A-Z ]*PRIVATE KEY-----/.test(line)) { onText(redact(pem.join('\n')) + '\n'); pem = null; }
      return;
    }
    if (/-----BEGIN [A-Z ]*PRIVATE KEY-----/.test(line) && !/-----END/.test(line)) { pem = [line]; return; }
    onText(redact(line) + '\n');
  };
  return {
    write(chunk) {
      buf += chunk.toString('utf8');
      let i;
      while ((i = buf.indexOf('\n')) >= 0) { emit(buf.slice(0, i)); buf = buf.slice(i + 1); }
    },
    end() {
      if (buf) { emit(buf); buf = ''; }
      if (pem) { onText('[REDACTED:PEM-UNTERMINATED]\n'); pem = null; }
    },
  };
}

// Interrupts: forward to the running child, remember the signal, and re-raise it once our own
// bookkeeping (meta / JUnit) is written, so Task aborts the run as it would without the canon.
let interruptedBy = null;
export const interrupted = () => interruptedBy;
export function reraiseIfInterrupted() {
  if (!interruptedBy) return;
  process.removeAllListeners(interruptedBy);
  process.kill(process.pid, interruptedBy);
}

/** Run `bash -o pipefail -c cmd`, stdin closed, stdout+stderr redacted into onText. Resolves exit code. */
export function runShell(cmd, { env = process.env, onText, cwd } = {}) {
  return new Promise((resolve) => {
    const child = spawn('bash', ['-o', 'pipefail', '-c', cmd], { env, cwd, stdio: ['ignore', 'pipe', 'pipe'] });
    const sink = redactingSink(onText);
    child.stdout.on('data', (d) => sink.write(d));
    child.stderr.on('data', (d) => sink.write(d));
    const forward = (sig) => () => { interruptedBy = interruptedBy ?? sig; try { child.kill(sig); } catch { /* gone */ } };
    const onInt = forward('SIGINT'); const onTerm = forward('SIGTERM');
    process.on('SIGINT', onInt); process.on('SIGTERM', onTerm);
    child.on('close', (code, signal) => {
      process.off('SIGINT', onInt); process.off('SIGTERM', onTerm);
      sink.end();
      resolve(code ?? (signal === 'SIGINT' ? 130 : signal === 'SIGKILL' ? 137 : 143));
    });
    child.on('error', (err) => { onText(`uts: cannot start bash: ${err.message}\n`); resolve(127); });
  });
}

function walkFiles(dir) {
  const out = [];
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) { if (name !== 'html') out.push(...walkFiles(p)); } else out.push(p);
  }
  return out;
}

export async function execLayer({ layer, runner, outputs, preflight, cmd, env }) {
  const cfg = loadConfig();
  const R = resultsDir(cfg);
  mkdirSync(`${R}/meta`, { recursive: true });
  mkdirSync(`${R}/logs`, { recursive: true });
  if (outputs) mkdirSync(outputs, { recursive: true });
  const log = `${R}/logs/${layer}.log`;
  const warnFile = `${R}/meta/${layer}.warnings`;
  writeFileSync(log, '');
  writeFileSync(warnFile, '');
  rmSync(`${R}/meta/${layer}.json`, { force: true });
  const fp = fingerprint(R);
  const startedAt = new Date().toISOString();
  const t0 = Date.now();
  const onText = (s) => { process.stdout.write(s); appendFileSync(log, s); };
  const fullEnv = { ...env, LAYER_WARN_FILE: warnFile, RESULTS_DIR: R };

  onText(`── layer:${layer} ${'─'.repeat(Math.max(3, 60 - layer.length))}\n`);
  let phase = 'run';
  let rc = 0;
  if (preflight) {
    rc = await runShell(preflight, { env: fullEnv, onText });
    if (rc !== 0) { phase = 'preflight'; onText(`uts: preflight for '${layer}' failed (exit ${rc}) — layer NOT run (infra_error)\n`); }
  }
  if (phase === 'run') rc = await runShell(cmd, { env: fullEnv, onText });
  const durationMs = Date.now() - t0;

  // Runner-written reports embed captured output → redact this layer's own dir in place.
  let masked = 0;
  if (outputs) {
    for (const f of walkFiles(outputs)) {
      if (!/\.(json|xml|txt|md|log|list)$/.test(f)) continue;
      const before = readFileSync(f, 'utf8');
      const after = redactDocument(before, f);
      if (after !== before) { writeFileSync(f, after); masked++; }
    }
  }
  if (masked) onText(`uts: masked secrets in ${masked} report file(s)\n`);

  const warnings = readFileSync(warnFile, 'utf8').split('\n').filter(Boolean).map(redact);
  writeFileSync(`${R}/meta/${layer}.json`, JSON.stringify({
    layer, runner, reportDir: outputs ?? null, fingerprint: fp, sha: null, startedAt, durationMs, exitCode: rc, phase,
    command: redact(cmd), preflight: preflight ? redact(preflight) : null, gate: env.UTS_GATE ?? null,
    warnings, ci: Boolean(process.env.CI), backendUrl: env.BACKEND_URL ?? null,
  }, null, 2) + '\n');
  onText(`── layer:${layer} finished: exit=${rc} phase=${phase} (${Math.round(durationMs / 1000)}s)${interruptedBy ? ` — interrupted (${interruptedBy})` : ''}\n`);
  reraiseIfInterrupted();
  return process.env.RUN_LAYER_STRICT === '1' ? rc : 0;
}

// ------------------------------------------------------------------ steps runner
const xml = (s) => stripAnsi(String(s)).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export async function runSteps(layer, out) {
  const cfg = loadConfig();
  const L = cfg.layers[layer];
  if (!L) throw new Error(`unknown layer ${layer}`);
  const steps = layerSteps(L);
  mkdirSync(path.dirname(out), { recursive: true });
  const cases = [];
  let failed = 0;
  const t0 = Date.now();
  for (const s of steps) {
    if (interruptedBy) break; // stop the list on Ctrl-C; the layer records exit 130 → infra_error
    process.stdout.write(`▶ [${layer}] ${s.name}: ${redact(s.run)}\n`);
    const tail = [];
    const t = Date.now();
    const rc = await runShell(L.dir ? `cd '${L.dir.replace(/'/g, `'\\''`)}' && ${s.run}` : s.run, {
      onText: (text) => { process.stdout.write(text); tail.push(text); if (tail.length > 200) tail.shift(); },
    });
    const secs = ((Date.now() - t) / 1000).toFixed(3);
    const file = (s.run.split(/\s+/).find((w) => /\.(sh|py|mjs|cjs|js|ts|rb)$/.test(w)) ?? '');
    if (rc === 0) {
      cases.push(`  <testcase classname="${xml(layer)}" name="${xml(s.name)}" file="${xml(file)}" time="${secs}"/>`);
      process.stdout.write(`✔ [${layer}] ${s.name} (${secs}s)\n`);
    } else {
      failed++;
      const body = tail.join('').split('\n').slice(-60).join('\n');
      cases.push(`  <testcase classname="${xml(layer)}" name="${xml(s.name)}" file="${xml(file)}" time="${secs}">\n    <failure message="exit ${rc}" type="step">${xml(body)}</failure>\n  </testcase>`);
      process.stdout.write(`✖ [${layer}] ${s.name} failed (exit ${rc}, ${secs}s)\n`);
    }
  }
  const total = ((Date.now() - t0) / 1000).toFixed(3);
  writeFileSync(out, `<?xml version="1.0" encoding="UTF-8"?>\n<testsuites><testsuite name="${xml(layer)}" tests="${steps.length}" failures="${failed}" time="${total}">\n${cases.join('\n')}\n</testsuite></testsuites>\n`);
  process.stdout.write(`[${layer}] ${steps.length - failed}/${steps.length} steps passed → ${out}\n`);
  reraiseIfInterrupted();
  return failed ? 1 : 0;
}
