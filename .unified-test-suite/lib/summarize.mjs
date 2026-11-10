// canon/lib/summarize.mjs — THE gate. Reads meta/<layer>.json (run-layer.sh) + each layer's native
// report, classifies every configured layer, writes summary.json / summary.md / history/, and
// returns 0 (pass) or 1 (fail). A crash writes verdict "error" and returns 2.
import { readFileSync, writeFileSync, existsSync, readdirSync, mkdirSync, rmSync } from 'node:fs';
import path from 'node:path';
import {
  ROOT, CANON_REL, readJson, loadConfig, resultsDir, redact, redactDeep, stripAnsi, shq, sh, canonVersion, layerSteps, toPosix, fingerprint as treeFingerprint,
} from './core.mjs';
import { layerReportDir } from './run.mjs';
import { sddReport, sddBdd } from './sdd.mjs';

export const SCHEMA_VERSION = '1.1.0';
const STATUS_ICON = { passed: '✅', failed: '❌', infra_error: '🛠️', stale: '⏳', not_run: '·' };

const rel = (p) => {
  if (!p) return null;
  const clean = p.replace(/^file:\/\//, '');
  return path.isAbsolute(clean) ? path.relative(ROOT, clean).split(path.sep).join('/') : clean;
};
const unescapeXml = (s) => s
  .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
  .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(Number(d)))
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, '&');
const attrs = (s) => Object.fromEntries([...s.matchAll(/([\w:-]+)="([^"]*)"/g)].map((m) => [m[1], unescapeXml(m[2])]));
const clip = (s, n = 2000) => { const t = stripAnsi(s ?? '').trim(); return t.length > n ? t.slice(0, n) + '\n…[truncated]' : t; };
const reRun = (s) => `^${s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`;
const xmlFiles = (d) => (existsSync(d) ? readdirSync(d).filter((f) => f.endsWith('.xml')).map((f) => `${d}/${f}`) : []);

function locate(text) {
  for (const m of String(text ?? '').matchAll(/(?:file:\/\/)?((?:\/|\.{0,2}\/?)?[\w@./-]+\.(?:ts|tsx|mjs|cjs|js|jsx|py|go|sh|rs|java|kt|rb)):(\d+)/g)) {
    const f = rel(m[1]);
    if (!f || f.startsWith('node:') || f.includes('node_modules/') || f.startsWith('..') || f.startsWith('internal/') || f.startsWith(CANON_REL + '/')) continue;
    return { file: f, line: Number(m[2]) };
  }
  return { file: null, line: null };
}
const test = (o) => ({ bdd: [], artifacts: [], retries: null, reproduce: null, kind: null, message: null, file: null, line: null, durationMs: 0, ...o });

// ------------------------------------------------------------------ parsers (by runner)
function parseJunit(files, layer, L) {
  const tests = [];
  const stepCmds = {};
  if (L.runner === 'steps') for (const st of layerSteps(L)) stepCmds[st.name] = st.run;
  const inDir = (c) => (c && L.dir ? `(cd ${shq(L.dir)} && ${c})` : c);
  const fromDir = (f) => (f && L.dir && !existsSync(f) && existsSync(path.join(L.dir, f)) ? `${L.dir.replace(/\/$/, '')}/${f}` : f);
  for (const f of files) {
    const xml = readFileSync(f, 'utf8');
    for (const m of xml.matchAll(/<testcase\b((?:[^>"']|"[^"]*"|'[^']*')*?)(?:\/>|>([\s\S]*?)<\/testcase>)/g)) {
      const a = attrs(m[1]); const body = m[2] ?? '';
      a.name = a.name ?? '(unnamed)';
      const fail = body.match(/<(failure|error)\b((?:[^>"']|"[^"]*"|'[^']*')*?)(?:\/>|>([\s\S]*?)<\/\1>)/);
      const skipped = /<skipped\b/.test(body);
      const message = fail ? unescapeXml(fail[3]?.trim() ? fail[3] : attrs(fail[2]).message ?? 'failed') : null;
      let file = rel(a.file) || null;
      // pytest xunit1 `line` is 0-based
      let line = a.line != null && a.line !== '' ? Number(a.line) + (L.runner === 'pytest' ? 1 : 0) : null;
      if (fail && !line) { const loc = locate(message); if (loc.file) ({ file, line } = loc); }
      let reproduce = null;
      // SDD binding keys (never serialized in standalone summaries): function name per runner.
      let fn = a.name; let fnQualified = null; let wholeFile = false;
      if (L.runner === 'pytest') {
        const pyFile = file ?? (a.classname ?? '').replace(/\./g, '/') + '.py';
        const modPath = pyFile.replace(/\.py$/, '').replace(/\//g, '.');
        const cls = a.classname && a.classname.startsWith(`${modPath}.`) ? a.classname.slice(modPath.length + 1).split('.').join('::') : '';
        const collection = !a.classname || a.name === modPath || a.name.includes('/');
        fn = a.name.replace(/\[.*\]$/, ''); fnQualified = cls ? `${cls}::${fn}` : fn;
        // SDD only: a collection error fails every case of its file (a '/' inside [params] is not one).
        wholeFile = Boolean(fail) && (!a.classname || a.name === modPath || fn.includes('/'));
        reproduce = inDir(`${L.python ?? 'python3'} -m pytest ${shq(collection ? pyFile : `${pyFile}::${cls ? `${cls}::` : ''}${a.name}`)}`);
      } else if (L.runner === 'node-test' || L.runner === 'node-command') {
        const nodeFile = file ?? (/\.(m|c)?[jt]s$/.test(a.name) ? a.name : null);
        // stack paths are repo-relative; inside `cd <dir>` they must be relative to the layer dir
        const local = nodeFile && L.dir && file ? toPosix(path.relative(L.dir, nodeFile)) : nodeFile;
        reproduce = nodeFile ? inDir(nodeFile === a.name ? `node --test ${shq(local)}` : `node --test --test-name-pattern=${shq(reRun(a.name))} ${shq(local)}`) : null;
      } else if (L.runner === 'steps') reproduce = stepCmds[a.name] ? inDir(stepCmds[a.name]) : null;
      else if (L.runner === 'command') reproduce = L.reproduce ? inDir(L.reproduce.replaceAll('{name}', a.name).replaceAll('{file}', file ?? '')) : null;
      file = fromDir(file);
      tests.push(test({
        title: L.runner === 'pytest' && a.classname ? `${a.classname}.${a.name}` : a.name,
        file, line, durationMs: Math.round(Number(a.time ?? 0) * 1000),
        status: fail ? 'failed' : skipped ? 'skipped' : 'passed',
        kind: fail ? (L.runner === 'steps' ? 'step' : L.runner === 'sdd-lint' ? 'doc' : L.runner === 'journeys' ? 'journey' : fail[1] === 'error' ? 'error' : 'test') : null,
        message: fail ? clip(message) : null, reproduce, fn, rawName: a.name, fnQualified, wholeFile,
      }));
    }
  }
  return { found: files.length > 0, tests, errors: [], extra: {} };
}

function parseGo(dir, L) {
  const base = L.dir ? L.dir.replace(/\/$/, '') : '';
  const under = (p) => (base ? `${base}/${p}` : p);
  const file = `${dir}/go.json`;
  if (!existsSync(file)) return { found: false, tests: [], errors: [], extra: {} };
  const byKey = new Map(); const pkgOut = new Map(); const pkgFailed = new Set(); const builds = new Map();
  for (const line of readFileSync(file, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    let e; try { e = JSON.parse(line); } catch { continue; }
    if (e.Action === 'build-output') { builds.set(e.ImportPath, (builds.get(e.ImportPath) ?? '') + e.Output); continue; }
    if (e.Action === 'build-fail') { builds.set(e.ImportPath, (builds.get(e.ImportPath) ?? '') + '\u0000FAIL'); continue; }
    if (!e.Test) {
      if (e.Action === 'output') pkgOut.set(e.Package, (pkgOut.get(e.Package) ?? '') + e.Output);
      if (e.Action === 'fail') pkgFailed.add(e.Package);
      continue;
    }
    const key = `${e.Package}\u0000${e.Test}`;
    const t = byKey.get(key) ?? { pkg: e.Package, name: e.Test, out: '', status: null, elapsed: 0 };
    if (e.Action === 'output') t.out += e.Output;
    if (['pass', 'fail', 'skip'].includes(e.Action)) { t.status = e.Action; t.elapsed = e.Elapsed ?? 0; }
    byKey.set(key, t);
  }
  const dirCache = new Map();
  const modFile = under('go.mod');
  const modPath = existsSync(modFile) ? (readFileSync(modFile, 'utf8').match(/^module\s+(\S+)/m)?.[1] ?? null) : null;
  const pkgDir = (pkg) => {
    if (dirCache.has(pkg)) return dirCache.get(pkg);
    if (modPath && (pkg === modPath || pkg.startsWith(`${modPath}/`))) { const r = pkg === modPath ? '.' : pkg.slice(modPath.length + 1); dirCache.set(pkg, r); return r; }
    const parts = pkg.split('/'); let d = null;
    for (let i = 0; i < parts.length && !d; i++) { const cand = parts.slice(i).join('/'); if (cand && existsSync(under(cand))) d = cand; }
    dirCache.set(pkg, d); return d;
  };
  const dot = (p) => (p.startsWith('.') ? p : `./${p}`);
  const race = L.race === false ? '' : ' -race';
  const inDir = (c) => (base ? `(cd ${shq(base)} && ${c})` : c);
  const all = [...byKey.values()];
  const tests = [];
  for (const t of all) {
    // Count leaves; keep a parent only when it failed on its own (none of its descendants failed).
    const kids = all.filter((o) => o.pkg === t.pkg && o.name.startsWith(t.name + '/'));
    if (kids.length && !(t.status === 'fail' && !kids.some((k) => k.status === 'fail'))) continue;
    const d = pkgDir(t.pkg);
    const m = t.out.match(/^\s+([\w.-]+_test\.go):(\d+):/m);
    const failed = t.status === 'fail' || !t.status;
    tests.push(test({
      title: `${t.pkg.split('/').slice(-2).join('/')}.${t.name}`,
      file: m ? under(d && d !== '.' ? `${d}/${m[1]}` : m[1]) : null, line: m ? Number(m[2]) : null,
      status: t.status === 'pass' ? 'passed' : t.status === 'skip' ? 'skipped' : 'failed',
      durationMs: Math.round(t.elapsed * 1000), kind: failed ? (t.status ? 'test' : 'timeout') : null,
      message: failed ? clip(t.out || 'test did not finish (panic/timeout?)') : null,
      reproduce: inDir(`go test ${dot(d ?? t.pkg)} -run ${shq(t.name.split('/').map(reRun).join('/'))} -count=1${race}`),
      fn: t.name.split('/')[0], fnFull: t.name, srcDir: d == null ? null : under(d === '.' ? '' : d).replace(/\/$/, '') || '.',
    }));
  }
  for (const [ip, out] of builds) if (out.includes('\u0000FAIL')) {
    const text = out.replace('\u0000FAIL', ''); const bp = ip.split(' ')[0];
    const loc = locate(text);
    tests.push(test({ title: `build ${bp}`, file: loc.file ? under(loc.file) : null, line: loc.line, status: 'failed', kind: 'build', message: clip(text), reproduce: inDir(`go test -count=1 -run '^$' ${dot(pkgDir(bp) ?? bp)}`), srcDir: pkgDir(bp) == null ? null : (under(pkgDir(bp) === '.' ? '' : pkgDir(bp)).replace(/\/$/, '') || '.'), wholeDir: true }));
  }
  for (const pkg of pkgFailed) {
    const short = pkg.split('/').slice(-2).join('/');
    const covered = tests.some((t) => t.status === 'failed' && (t.title.startsWith(short + '.') || t.title === `build ${pkg}`));
    if (!covered && !/\[build failed\]|\[setup failed\]/.test(pkgOut.get(pkg) ?? '')) {
      tests.push(test({ title: `package ${pkg}`, file: pkgDir(pkg) ? under(pkgDir(pkg)).replace(/\/\.$/, '') : null, status: 'failed', kind: 'package', message: clip(pkgOut.get(pkg)), reproduce: inDir(`go test ${dot(pkgDir(pkg) ?? pkg)} -count=1${race}`), srcDir: pkgDir(pkg) == null ? null : (under(pkgDir(pkg) === '.' ? '' : pkgDir(pkg)).replace(/\/$/, '') || '.'), wholeDir: true }));
    }
  }
  const extra = {};
  if (existsSync(`${dir}/coverage.out`)) {
    let total = 0; let covered = 0;
    for (const l of readFileSync(`${dir}/coverage.out`, 'utf8').split('\n').slice(1)) {
      const mm = l.match(/ (\d+) (\d+)$/); if (!mm) continue;
      total += Number(mm[1]); if (Number(mm[2]) > 0) covered += Number(mm[1]);
    }
    extra.coveragePercent = total ? Math.round((covered / total) * 1000) / 10 : null;
  }
  return { found: true, tests, errors: [], extra };
}

function parsePlaywright(dir, L, cfg) {
  const file = `${dir}/results.json`;
  if (!existsSync(file)) return { found: false, tests: [], errors: [], extra: {} };
  const report = readJson(file);
  const rootDir = report.config?.rootDir ?? ROOT;
  const conf = L.config ?? cfg.e2e.config;
  const tests = [];
  const walk = (suite) => {
    for (const s of suite.suites ?? []) walk(s);
    for (const spec of suite.specs ?? []) for (const t of spec.tests ?? []) {
      const status = { expected: 'passed', unexpected: 'failed', flaky: 'flaky', skipped: 'skipped' }[t.status] ?? 'failed';
      const last = t.results?.[t.results.length - 1] ?? {};
      const err = last.error ?? (last.errors ?? [])[0];
      const f = rel(path.join(rootDir, spec.file));
      tests.push(test({
        title: spec.title, file: f, line: spec.line, status,
        durationMs: (t.results ?? []).reduce((a, r) => a + (r.duration ?? 0), 0),
        kind: status === 'failed' ? (/Timeout|timed out/i.test(err?.message ?? '') ? 'timeout' : 'test') : null,
        message: status === 'failed' ? clip(err?.message ?? 'failed') : null,
        bdd: (spec.tags ?? []).map((x) => x.replace(/^@/, '')).filter((x) => x.startsWith('BDD.')),
        retries: Math.max(0, (t.results?.length ?? 1) - 1),
        artifacts: (last.attachments ?? []).filter((a) => a.path).map((a) => rel(a.path)),
        reproduce: `npx playwright test --config ${conf} --project=${t.projectName ?? L.project ?? L.name} ${f}:${spec.line}`,
        fn: spec.title, tags: spec.tags ?? [],
      }));
    }
  };
  for (const s of report.suites ?? []) walk(s);
  const errors = (report.errors ?? []).map((e) => clip(e.message ?? String(e), 800));
  return { found: true, tests, errors, extra: { html: `${dir}/html/index.html` } };
}

function coberturaPercent(file) {
  if (!existsSync(file)) return undefined;
  const m = readFileSync(file, 'utf8').match(/<coverage\b[^>]*\bline-rate="([\d.]+)"/);
  return m ? Math.round(Number(m[1]) * 1000) / 10 : undefined;
}

function parseLayer(cfg, name, L) {
  const dir = layerReportDir(cfg, name);
  switch (L.runner) {
    case 'go': return parseGo(dir, L);
    case 'playwright': return parsePlaywright(dir, L, cfg);
    case 'node-command': return parseJunit(xmlFiles(`${dir}/node`), name, L);
    case 'pytest': { const r = parseJunit(xmlFiles(dir), name, L); const c = coberturaPercent(`${dir}/coverage.xml`); if (c !== undefined) r.extra.coveragePercent = c; return r; }
    default: { const r = parseJunit(xmlFiles(dir), name, L); const c = coberturaPercent(`${dir}/coverage.xml`); if (c !== undefined) r.extra.coveragePercent = c; return r; }
  }
}

// ------------------------------------------------------------------ main
const NO_FRESH_JOURNEYS = 'no fresh journey evidence for this tree — run the QA engineer (uts journeys plan → browser-subagent), then re-run this layer; ignored records are listed in journeys.json';

export function summarize({ target = 'report', require = [], since = null, strict: strictArg = false, allowEmpty = false } = {}) {
  const cfg = loadConfig();
  const R = resultsDir(cfg);
  const logTail = (layer, n = 40) => { const f = `${R}/logs/${layer}.log`; return existsSync(f) ? readFileSync(f, 'utf8').split('\n').slice(-n).join('\n') : ''; };
  const gateCfg = cfg.gates[target];
  const strict = strictArg || Boolean(gateCfg?.strict);
  const names = Object.keys(cfg.layers);
  for (const l of require) if (!names.includes(l)) throw new Error(`unknown layer in --require: ${l}`);

  const fingerprint = treeFingerprint(R);
  const layers = {};
  const all = {};
  for (const name of names) {
    const Lc = cfg.layers[name];
    const meta = readJson(`${R}/meta/${name}.json`, null);
    const L = {
      runner: Lc.runner, status: 'not_run', required: false, fresh: false, reason: null, granularity: 'fine',
      exitCode: meta?.exitCode ?? null, phase: meta?.phase ?? null, startedAt: meta?.startedAt ?? null, gate: meta?.gate ?? null,
      durationMs: meta?.durationMs ?? 0, counts: { total: 0, passed: 0, failed: 0, skipped: 0, flaky: 0 },
      failures: [], warnings: meta?.warnings ?? [], artifacts: { log: meta ? `${R}/logs/${name}.log` : null, reportDir: layerReportDir(cfg, name) },
    };
    layers[name] = L; all[name] = [];
    if (!meta) { L.reason = 'no results for this layer'; continue; }
    L.fresh = meta.fingerprint === fingerprint;
    const parsed = parseLayer(cfg, name, Lc);
    all[name] = parsed.tests;
    if (parsed.extra.html && existsSync(parsed.extra.html)) L.artifacts.html = parsed.extra.html;
    if (parsed.extra.coveragePercent !== undefined) L.coveragePercent = parsed.extra.coveragePercent;
    for (const t of parsed.tests) { L.counts.total++; L.counts[t.status]++; }
    const failed = parsed.tests.filter((t) => t.status === 'failed');
    L.failures = failed.map(({ title, file, line, kind, message, bdd, reproduce, artifacts, retries }) => ({ layer: name, title, file, line, kind, message, bdd, reproduce, artifacts, retries }));
    if (parsed.errors.length) L.warnings.push(...parsed.errors.map((e) => `runner error: ${e}`));

    if (!L.fresh) { L.status = 'stale'; L.reason = `results are from another tree state (fingerprint ${meta.fingerprint} ≠ ${fingerprint}); re-run the layer`; continue; }
    if (meta.phase === 'preflight') { L.status = 'infra_error'; L.reason = `preflight failed (exit ${meta.exitCode}): ${clip(logTail(name, 8), 600)}`; continue; }
    if ([126, 127].includes(meta.exitCode)) { L.status = 'infra_error'; L.reason = `command not runnable (exit ${meta.exitCode}): ${clip(logTail(name, 4), 300)}`; continue; }
    if ([130, 137, 143].includes(meta.exitCode)) { L.status = 'infra_error'; L.reason = `interrupted/killed (exit ${meta.exitCode})`; continue; }
    const coarseAllowed = ['node-command', 'command'].includes(Lc.runner);
    if (!parsed.found && coarseAllowed) {
      L.granularity = 'coarse';
      L.status = meta.exitCode === 0 ? 'passed' : 'failed';
      if (meta.exitCode !== 0) L.failures.push({ layer: name, title: Lc.command ?? Lc.run ?? name, file: null, line: null, kind: 'test', message: clip(logTail(name)), bdd: [], reproduce: Lc.command ?? null, artifacts: [], retries: null });
      continue;
    }
    if (!parsed.found) { L.status = 'infra_error'; L.reason = `runner exited ${meta.exitCode} but produced no report`; continue; }
    if (failed.length) { L.status = 'failed'; continue; }
    if (L.counts.total === 0) {
      L.status = 'infra_error';
      L.reason = Lc.runner === 'steps' ? 'zero steps configured/executed — add steps to this layer or remove it from the gate' : Lc.runner === 'journeys' ? NO_FRESH_JOURNEYS : `zero tests executed${parsed.errors[0] ? `: ${parsed.errors[0]}` : ''}`;
      continue;
    }
    if (meta.exitCode !== 0) {
      if (Lc.runner === 'node-command') {
        L.status = 'failed';
        L.failures.push({ layer: name, title: `${Lc.command} failed outside node --test`, file: null, line: null, kind: 'step', message: clip(logTail(name)), bdd: [], reproduce: Lc.command, artifacts: [], retries: null });
      } else if (Lc.runner === 'journeys' && meta.exitCode === 3) {
        L.status = 'infra_error';
        const tail = logTail(name, 12).split('\n');
        const why = tail.find((l) => l.startsWith('journeys: not verified')) ?? 'blocked runs or cards without fresh evidence';
        L.reason = tail.some((l) => l.startsWith('journeys: no fresh evidence'))
          ? NO_FRESH_JOURNEYS
          : `${clip(why.replace(/^journeys: /, ''), 600)} — see journeys.json; re-plan and re-run the missing journeys, file blocked ones as environment issues`;
      } else { L.status = 'infra_error'; L.reason = `runner exited ${meta.exitCode} with no failing test: ${clip(logTail(name, 8), 600)}`; }
      continue;
    }
    if (L.counts.total === L.counts.skipped) {
      // The runner claimed success, but skipped testcases ran no assertion: unverified, not passed.
      L.status = 'infra_error';
      L.reason = `all ${L.counts.total} test(s) skipped — nothing was verified`;
      continue;
    }
    L.status = 'passed';
  }

  for (const name of names) for (const f of layers[name].failures) if (!f.reproduce) f.reproduce = `task test:${name}`;
  const required = require.length ? require : allowEmpty ? [] : names.filter((l) => layers[l].fresh);
  for (const l of required) layers[l].required = true;

  const reasons = []; const warnings = [];
  if (!required.length) {
    if (allowEmpty) warnings.push(`no layers selected for ${target}`);
    else reasons.push('nothing ran: no required layers and no fresh results (wrong RESULTS_DIR, or run a test task first)');
  }
  for (const l of required) {
    const L = layers[l];
    if (L.status !== 'passed') reasons.push(`${l}: ${L.status}${L.reason ? ` — ${L.reason}` : L.failures.length ? ` (${L.failures.length} failing)` : ''}`);
  }
  for (const l of names) {
    const L = layers[l];
    if (!L.required && L.fresh && ['failed', 'infra_error'].includes(L.status)) warnings.push(`${l} (not required for ${target}) is ${L.status}`);
    if (L.fresh) for (const w of L.warnings) warnings.push(`${l}: ${w}`);
    const floor = cfg.coverage?.[l]?.minPercent;
    if (L.required && L.fresh && floor != null) {
      if (L.coveragePercent == null) reasons.push(`${l}: coverage floor ${floor}% configured but no coverage report produced`);
      else if (L.coveragePercent < floor) reasons.push(`${l}: coverage ${L.coveragePercent}% < floor ${floor}%`);
    }
  }
  const wallSeconds = since ? Math.round(Date.now() / 1000 - since) : Math.round(names.reduce((a, l) => a + (layers[l].fresh ? layers[l].durationMs : 0), 0) / 1000);
  const budget = gateCfg?.budgetSeconds;
  if (budget != null && wallSeconds > budget) (strict ? reasons : warnings).push(`duration ${wallSeconds}s > ${target} budget ${budget}s`);
  const pwLayers = names.filter((l) => cfg.layers[l].runner === 'playwright' && layers[l].fresh);
  const e2eTests = pwLayers.flatMap((l) => all[l]);
  const flaky = e2eTests.filter((t) => t.status === 'flaky');
  const flakyPct = e2eTests.length ? Math.round((flaky.length / e2eTests.length) * 1000) / 10 : 0;
  if (flaky.length) {
    const msg = `${flaky.length} flaky E2E test(s) (${flakyPct}%): ${flaky.slice(0, 5).map((t) => `${t.file}:${t.line}`).join(', ')}`;
    if (strict && flakyPct > cfg.flaky.strictFailPercent) reasons.push(msg); else warnings.push(msg);
  }
  for (const [name, p] of Object.entries(cfg.providers ?? {})) if (p.env && process.env[p.env] === undefined && !(p.modes?.[target] ?? p.modes?.default)) warnings.push(`provider ${name}: no mode for gate "${target}" (env ${p.env} unset)`);

  const bddPass = new Set(); const bddFail = new Set();
  for (const t of e2eTests) for (const id of t.bdd) { if (t.status === 'failed') bddFail.add(id); else if (['passed', 'flaky'].includes(t.status)) bddPass.add(id); }
  const covered = [...bddPass].filter((id) => !bddFail.has(id)).sort();

  // --- SDD mode (config `sdd` block): evidence against the project's SDD chain. Standalone: skipped.
  let sdd = null;
  if (cfg.sdd) {
    const fresh = names.filter((l) => layers[l].fresh && !['infra_error'].includes(layers[l].status));
    sdd = sddReport(cfg, target, fresh.flatMap((l) => all[l].map((t) => ({ ...t, layer: l }))));
    const n = sdd.findings.length;
    if (sdd.mode === 'gate-code') for (const f of sdd.findings) { if (f.severity === 'error') reasons.push(`sdd: ${f.code} ${f.ref} — ${f.message}`); }
    else if (n) {
      const by = Object.entries(sdd.findings.reduce((a, f) => ({ ...a, [f.code]: (a[f.code] ?? 0) + 1 }), {})).map(([c, k]) => `${c} ×${k}`).join(', ');
      warnings.push(`sdd: ${n} finding(s) (${by}) — see summary.sdd.findings`);
    }
  }

  const failIds = names.flatMap((l) => layers[l].failures.map((f) => `${f.layer}|${f.file}:${f.line}|${f.title}`));
  const histDir = `${R}/history`; mkdirSync(histDir, { recursive: true });
  const hist = readdirSync(histDir).filter((f) => f.endsWith('.json')).sort();
  const prev = hist.length ? readJson(`${histDir}/${hist[hist.length - 1]}`, null) : null;
  const diff = prev ? { previousGeneratedAt: prev.generatedAt, newFailures: failIds.filter((x) => !prev.failIds.includes(x)), fixed: prev.failIds.filter((x) => !failIds.includes(x)) } : null;

  const status = reasons.length ? 'fail' : 'pass';
  const generatedAt = new Date().toISOString();
  const slowest = names.filter((l) => !['steps'].includes(cfg.layers[l].runner) && layers[l].fresh)
    .flatMap((l) => all[l].map((t) => ({ layer: l, title: t.title, file: t.file, line: t.line, durationMs: t.durationMs })))
    .sort((a, b) => b.durationMs - a.durationMs).slice(0, 5);
  const totalScenarios = cfg.bdd?.totalScenarios ?? null;

  const summary = {
    schemaVersion: SCHEMA_VERSION, canonVersion: canonVersion(), project: cfg.project ?? null,
    generatedAt, target, strict,
    verdict: { status, reasons },
    git: { sha: sh('git rev-parse HEAD') || null, branch: sh('git rev-parse --abbrev-ref HEAD') || null, dirty: sh('git status --porcelain') !== '', fingerprint },
    env: { ci: Boolean(process.env.CI), backendUrl: process.env.BACKEND_URL ?? cfg.stack.backendUrl ?? null, appUrl: process.env.APP_URL ?? cfg.stack.appUrl ?? null, backendManaged: process.env.BACKEND_MANAGED ?? null, testRunId: process.env.TEST_RUN_ID ?? null },
    durationSeconds: wallSeconds,
    layers,
    failures: names.flatMap((l) => (layers[l].fresh ? layers[l].failures : [])),
    warnings,
    flaky: { count: flaky.length, percent: flakyPct, tests: flaky.map((t) => ({ title: t.title, file: t.file, line: t.line, retries: t.retries })) },
    bdd: sdd ? sddBdd(sdd) : { covered, failing: [...bddFail].sort(), totalScenarios, coveredPercent: totalScenarios ? Math.round((covered.length / totalScenarios) * 1000) / 10 : null },
    slowest, diff,
    doctor: existsSync(`${R}/doctor.json`) ? `${R}/doctor.json` : null,
  };
  if (sdd) summary.sdd = sdd;

  const md = [];
  md.push(`# Test summary — ${status === 'pass' ? '✅ PASS' : '❌ FAIL'} (target: \`${target}\`${strict ? ', strict' : ''})`, '');
  md.push(`${cfg.project ?? ''} \`${(summary.git.sha ?? 'nogit').slice(0, 10)}\`${summary.git.dirty ? ' (dirty)' : ''} · fingerprint \`${fingerprint}\` · ${generatedAt} · ${summary.env.ci ? 'CI' : 'local'} · backend ${summary.env.backendUrl ?? '—'} · ${wallSeconds}s · canon ${summary.canonVersion}`, '');
  if (reasons.length) md.push('**Why it failed**', '', ...reasons.map((r) => `- ${r}`), '');
  md.push('| Layer | Runner | Required | Status | Total | Pass | Fail | Skip | Flaky | Cov | Time |', '|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|');
  for (const [k, L] of Object.entries(layers)) {
    md.push(`| ${k}${L.granularity === 'coarse' ? ' (coarse)' : ''} | ${L.runner} | ${L.required ? 'yes' : ''} | ${STATUS_ICON[L.status]} ${L.status} | ${L.counts.total} | ${L.counts.passed} | ${L.counts.failed} | ${L.counts.skipped} | ${L.counts.flaky} | ${L.coveragePercent != null ? L.coveragePercent + '%' : ''} | ${L.phase ? Math.round(L.durationMs / 1000) + 's' : ''} |`);
  }
  if (summary.failures.length) {
    md.push('', `## Failures (${summary.failures.length})`);
    for (const f of summary.failures.slice(0, 50)) {
      md.push('', `### ${f.layer} · ${f.file ? `\`${f.file}${f.line ? `:${f.line}` : ''}\` — ` : ''}${f.title}${f.kind && f.kind !== 'test' ? ` [${f.kind}]` : ''}`);
      if (f.message) md.push('```text', f.message.split('\n').slice(0, 25).join('\n'), '```');
      if (f.reproduce) md.push(`Reproduce: \`${f.reproduce}\``);
      if (f.artifacts?.length) md.push(`Artifacts: ${f.artifacts.map((a) => `\`${a}\``).join(', ')}`);
    }
  }
  const infra = Object.entries(layers).filter(([, L]) => ['infra_error', 'stale', 'not_run'].includes(L.status) && L.required);
  if (infra.length) md.push('', '## Environment / freshness problems', '', ...infra.map(([k, L]) => `- **${k}** ${L.status}: ${L.reason}`), '', 'Run `task test:doctor` to see which layers this environment can run.');
  if (warnings.length) md.push('', '## Warnings', '', ...warnings.map((w) => `- ${w}`));
  if (diff && (diff.newFailures.length || diff.fixed.length)) md.push('', '## Since previous local run', '', `- new failures: ${diff.newFailures.length}`, `- fixed: ${diff.fixed.length}`);
  if (slowest.length) md.push('', '## Slowest tests', '', ...slowest.map((s) => `- ${Math.round(s.durationMs / 100) / 10}s · ${s.layer} · ${s.file ? `\`${s.file}${s.line ? `:${s.line}` : ''}\` ` : ''}${s.title}`));
  if (sdd?.bdd) {
    md.push('', '## SDD evidence', '', `mode \`${sdd.mode}\` · corpus \`${sdd.docsRoot}\``, '',
      `- TDD cases: ${Object.entries(sdd.tdd.counts).filter(([, v]) => v).map(([k, v]) => `${v} ${k}`).join(', ') || 'none'} (of ${sdd.totals.tdd})`,
      `- BDD: ${sdd.bdd.counts.verified}/${sdd.totals.bdd} verified (${sdd.bdd.verifiedPercent ?? 0}%) · ${sdd.bdd.counts.failing} failing · ${sdd.bdd.counts.at_risk} at risk · ${sdd.bdd.counts.unpaired} unpaired`,
      `- EARS: ${sdd.ears.counts.verified}/${sdd.totals.ears} verified (${sdd.ears.verifiedPercent ?? 0}%) · ${sdd.ears.counts.failing} failing · ${sdd.ears.counts.at_risk} at risk · ${sdd.ears.counts.uncovered} uncovered`);
    if (sdd.findings.length) md.push('', ...sdd.findings.slice(0, 30).map((f) => `- ${f.severity === 'error' ? '❌' : '⚠️'} ${f.code} (${f.implements}) \`${f.ref}\`: ${f.message}`));
  } else if (sdd) md.push('', '## SDD evidence', '', ...sdd.findings.map((f) => `- ${f.code}: ${f.message}`));
  if (pwLayers.length && !sdd) md.push('', '## BDD traceability', '', `${covered.length} scenario IDs covered by passing E2E${totalScenarios ? ` of ${totalScenarios} (${summary.bdd.coveredPercent}%)` : ''}; ${bddFail.size} with failing tests.`);
  const html = Object.values(layers).map((L) => L.artifacts.html).filter(Boolean);
  if (html.length) md.push('', '## Reports', '', ...html.map((h) => `- \`${h}\``));

  mkdirSync(R, { recursive: true });
  writeFileSync(`${R}/summary.json`, JSON.stringify(redactDeep(summary), null, 2) + '\n');
  writeFileSync(`${R}/summary.md`, redact(md.join('\n')) + '\n');
  writeFileSync(`${histDir}/${generatedAt.replace(/[:.]/g, '-')}.json`, JSON.stringify({ generatedAt, fingerprint, target, status, failIds }) + '\n');
  for (const old of readdirSync(histDir).filter((f) => f.endsWith('.json')).sort().slice(0, -20)) rmSync(`${histDir}/${old}`);

  console.log(redact(md.slice(0, 2 + (reasons.length ? reasons.length + 3 : 0)).join('\n')));
  console.log(`→ ${R}/summary.json · ${R}/summary.md — verdict: ${status.toUpperCase()}`);
  return status === 'pass' ? 0 : 1;
}

export function writeIncompleteSummary(target) {
  const cfg = loadConfig();
  const R = resultsDir(cfg);
  mkdirSync(R, { recursive: true });
  const summary = { schemaVersion: SCHEMA_VERSION, canonVersion: canonVersion(), project: cfg.project ?? null, generatedAt: new Date().toISOString(), target, verdict: { status: 'error', reasons: [`run incomplete: ${target} started but no report was written (aborted before \`uts report\`)`] } };
  writeFileSync(`${R}/summary.json`, JSON.stringify(summary, null, 2) + '\n');
  writeFileSync(`${R}/summary.md`, `# Test summary — ⚠️ RUN INCOMPLETE (target: \`${target}\`)\n\nThe run started at ${summary.generatedAt} and ended before the report step. Re-run it.\n`);
}

export function writeCrashSummary(err) {
  try {
    let R = process.env.RESULTS_DIR || 'test-results';
    try { R = resultsDir(loadConfig()); } catch { /* keep default */ }
    mkdirSync(R, { recursive: true });
    const summary = { schemaVersion: SCHEMA_VERSION, canonVersion: canonVersion(), generatedAt: new Date().toISOString(), verdict: { status: 'error', reasons: [`summarizer crashed: ${err.message}`] } };
    writeFileSync(`${R}/summary.json`, JSON.stringify(redactDeep(summary), null, 2) + '\n');
    writeFileSync(`${R}/summary.md`, `# Test summary — ⚠️ ERROR\n\nSummarizer crashed: \`${redact(err.message)}\`\n`);
  } catch { /* nothing more we can do */ }
}
