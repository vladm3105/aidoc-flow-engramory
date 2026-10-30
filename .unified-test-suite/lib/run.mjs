// canon/lib/run.mjs — turn config into layer runs, gate plans, stack env, selection and doctor output.
import { spawnSync } from 'node:child_process';
import { mkdirSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import {
  CANON_DIR, CANON_REL, ROOT, loadConfig, resultsDir, reportDir, shq, sh, matchesAny, findFiles, isLoopbackUrl, canonVersion,
} from './core.mjs';
import { execLayer } from './exec.mjs';
import { sddLintCommand } from './sdd.mjs';

const S = (f) => `${CANON_REL}/scripts/${f}`;
const UTS = () => `node ${shq(`${CANON_REL}/bin/uts.mjs`)}`;

// ------------------------------------------------------------------ stack env
/** Environment every layer and backend command sees. Exported env always wins over config. */
export function stackEnv(cfg, gate = 'local') {
  const env = {};
  const set = (k, v) => { if (v != null && v !== '' && process.env[k] === undefined) env[k] = String(v); };
  set('BACKEND_URL', cfg.stack.backendUrl);
  set('APP_URL', cfg.stack.appUrl);
  set('BACKEND_HEALTH_PATH', cfg.stack.healthPath);
  set('BACKEND_WAIT_SECONDS', cfg.stack.waitSeconds);
  set('RESULTS_DIR', cfg.resultsDir);
  if (cfg.stack.compose?.files?.length) set('UTS_COMPOSE_FILES', cfg.stack.compose.files.join(' '));
  if (cfg.stack.compose?.envFile && !process.env.CI && existsSync(cfg.stack.compose.envFile)) set('COMPOSE_ENV_FILE', cfg.stack.compose.envFile);
  set('UTS_DB_RESET', cfg.stack.dbReset);
  env.UTS_GATE = gate;
  env.UTS_CANON_DIR = CANON_REL;
  env.PW_IDENTITY_POOL_ENV = cfg.e2e.identityPoolEnv;
  env.PW_IDENTITIES_PER_WORKER = String(cfg.e2e.identitiesPerWorker ?? 1);
  // Provider modes: each provider declares the env switch the app honours and the mode per gate.
  for (const [, p] of Object.entries(cfg.providers ?? {})) {
    const mode = p.modes?.[gate] ?? p.modes?.default;
    if (p.env && mode != null) set(p.env, mode);
  }
  return { ...process.env, ...env };
}

// ------------------------------------------------------------------ layer command
/** Absolute report dir when the layer runs in a sub-directory (monorepos), else repo-relative. */
export function layerReportDir(cfg, layer) {
  const L = cfg.layers[layer];
  const out = reportDir(cfg, layer);
  return L?.dir ? path.resolve(ROOT, out) : out;
}

export function layerCommand(cfg, layer) {
  const L = cfg.layers[layer];
  if (!L) throw new Error(`layer "${layer}" is not configured (known: ${Object.keys(cfg.layers).join(', ') || 'none'})`);
  const out = layerReportDir(cfg, layer);
  const q = shq;
  const inDir = (c) => (L.dir ? `cd ${q(L.dir)} && ${c}` : c);
  switch (L.runner) {
    case 'go': {
      const pkgs = (L.packages ?? ['./...']).map(q).join(' ');
      const flags = [L.race === false ? '' : '-race', ...(L.args ?? []).map(q)].filter(Boolean).join(' ');
      return inDir(`go test ${pkgs} -count=1 ${flags} -json -coverprofile=${q(`${out}/coverage.out`)} > ${q(`${out}/go.json`)}`);
    }
    case 'pytest': {
      // COVERAGE_FILE keeps pytest-cov's data file out of the working tree (tree fingerprint stays stable).
      const cov = L.coverage ? ` --cov=${q(L.coverage === true ? '.' : L.coverage)} --cov-report=xml:${q(`${out}/coverage.xml`)}` : '';
      return inDir(`COVERAGE_FILE=${q(`${out}/.coverage`)} ${L.python ?? 'python3'} -m pytest ${(L.args ?? []).map(q).join(' ')} -q -p no:cacheprovider -o junit_family=xunit1 --junitxml=${q(`${out}/junit.xml`)}${cov}`);
    }
    case 'node-test':
      return inDir(`node --test --test-reporter=spec --test-reporter-destination=stdout --test-reporter=junit --test-reporter-destination=${q(`${out}/junit.xml`)} ${(L.files ?? ['test/**/*.test.mjs']).map(q).join(' ')}`);
    case 'node-command':
      return inDir(`NODE_TEST_JUNIT_DIR=${q(`${out}/node`)} bash ${q(path.resolve(ROOT, S('with-node-junit.sh')))} ${L.command}`);
    case 'steps':
      return `${UTS()} steps ${q(layer)} ${q(`${out}/junit.xml`)}`;
    case 'playwright': {
      if (L.dir) throw new Error(`layer "${layer}": "dir" is not supported for playwright — point layer.config at the sub-project's config`);
      const conf = L.config ?? cfg.e2e.config;
      return `PW_RUN_LABEL=${q(layer)} npx playwright test --config ${q(conf)} --project=${q(L.project ?? layer)}${(L.args ?? []).map((a) => ' ' + q(a)).join('')}`;
    }
    case 'command':
      // Escape hatch for any runner that can emit JUnit (cargo-nextest, jest-junit, vitest, …).
      // {reportDir} is substituted (quoted); JUnit must land in {reportDir}/*.xml or the layer is coarse.
      return inDir(L.run.replaceAll('{reportDir}', q(out)).replaceAll('{resultsDir}', q(path.resolve(ROOT, resultsDir(cfg)))));
    case 'journeys':
      // Browser-subagent evidence (CANON.md §12): import, fresh-check, redact, JUnit.
      return `${UTS()} journeys import --layer ${q(layer)} --report-dir ${q(out)}`;
    case 'sdd-lint':
      // aidoc-flow document lint as a layer (SDD mode): findings → JUnit failures with file:line.
      return sddLintCommand(UTS(), layer, out);
    default:
      throw new Error(`layer "${layer}": unknown runner "${L.runner}"`);
  }
}

export function layerPreflight(cfg, layer) {
  const L = cfg.layers[layer];
  const backend = `bash ${shq(S('backend.sh'))}`;
  if (L.runner === 'sdd-lint') return `${UTS()} sdd-lint --preflight`;
  if (L.needsStack) {
    const up = `${L.needsApp ? 'WAIT_FOR_APP=1 ' : ''}${backend} up`;
    return L.resetDb === false ? up : `${up} && ${backend} reset`;
  }
  // needsBackend: must be reachable, never started or reset (smoke against a deployed stack)
  if (L.needsBackend) return `BACKEND_MANAGED=0 ${L.needsApp ? 'WAIT_FOR_APP=1 ' : ''}${backend} up`;
  return '';
}

/** Run one layer. Resolves 0 unless RUN_LAYER_STRICT=1 (the report is the gate). */
export async function runLayer(layer, gate = 'local') {
  const cfg = loadConfig();
  const L = cfg.layers[layer];
  if (!L) throw new Error(`layer "${layer}" is not configured (known: ${Object.keys(cfg.layers).join(', ') || 'none'})`);
  const out = layerReportDir(cfg, layer);
  mkdirSync(out, { recursive: true });
  const env = stackEnv(cfg, gate);
  if (L.needsApp) env.UTS_NEEDS_APP = '1';
  if (L.needsStack || L.needsBackend) env.UTS_NEEDS_BACKEND = '1';
  return execLayer({ layer, runner: L.runner, outputs: out, preflight: layerPreflight(cfg, layer), cmd: layerCommand(cfg, layer), env });
}

// ------------------------------------------------------------------ plans
export function gatePlan(cfg, gateName) {
  const gate = cfg.gates[gateName];
  if (!gate) throw new Error(`gate "${gateName}" is not configured (known: ${Object.keys(cfg.gates).join(', ') || 'none'})`);
  const layers = gate.layers;
  for (const l of layers) if (!cfg.layers[l]) throw new Error(`gate "${gateName}" references unknown layer "${l}"`);
  return {
    all: layers,
    parallel: layers.filter((l) => !cfg.layers[l].stateful),
    serial: layers.filter((l) => cfg.layers[l].stateful),
    strict: Boolean(gate.strict),
    budgetSeconds: gate.budgetSeconds ?? null,
  };
}

export function groupPlan(cfg, name) {
  const g = cfg.groups[name];
  if (!g) throw new Error(`group "${name}" is not configured`);
  return { all: g, parallel: g.filter((l) => !cfg.layers[l].stateful), serial: g.filter((l) => cfg.layers[l].stateful) };
}

// ------------------------------------------------------------------ selection (test:changed)
export function selectLayers({ explain = false } = {}) {
  const cfg = loadConfig();
  const sel = cfg.selection;
  const all = Object.keys(cfg.layers);
  if (!sel) return { layers: all, why: ['no selection rules configured → all layers'] };
  const base = process.env.UTS_SELECT_BASE || sel.base || 'origin/main';
  const mergeBase = sh(`git merge-base HEAD ${base}`);
  const changed = new Set([
    mergeBase ? sh(`git diff --name-only ${mergeBase}`) : '',
    sh('git diff --name-only HEAD'),
    sh('git ls-files --others --exclude-standard'),
  ].join('\n').split('\n').filter((f) => f && !f.startsWith('.unified-test-suite/')));
  const selected = new Set(sel.always ?? []);
  const why = [];
  let fallback = !mergeBase;
  if (fallback) why.push(`no merge-base with ${base} → fallback`);
  const infra = ['unified-test-suite.config.json', 'unified-test-suite.lock.json', 'Taskfile.yml', ...(sel.fullRunTriggers ?? [])];
  for (const file of changed) {
    if (matchesAny(file, infra)) { fallback = true; why.push(`${file} → suite infrastructure → fallback`); continue; }
    const hits = (sel.rules ?? []).filter((r) => matchesAny(file, r.paths));
    if (!hits.length) { fallback = true; why.push(`${file} → unmapped → fallback`); continue; }
    for (const r of hits) for (const l of r.layers) { selected.add(l); why.push(`${file} → ${l}`); }
  }
  if (fallback) (sel.fallback ?? cfg.gates.pr?.layers ?? all).forEach((l) => selected.add(l));
  const layers = all.filter((l) => selected.has(l));
  if (explain) console.error(why.join('\n') || '(no changes) → always-layers only');
  return { layers, why };
}

// ------------------------------------------------------------------ doctor
export function doctor() {
  const cfg = loadConfig();
  const has = (bin) => sh(`command -v ${bin}`) !== '';
  const runners = new Set(Object.values(cfg.layers).map((l) => l.runner));
  const checks = {};
  const pinned = sh(`grep -o 'PINNED_TASK_VERSION: *[0-9.]*' ${CANON_REL}/Taskfile.yml | grep -o '[0-9.]*$'`);
  const tv = sh("task --version | grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+' | head -1");
  checks.task = { ok: tv === pinned, detail: `${tv || 'missing'} (pinned ${pinned})` };
  checks.node = { ok: has('node'), detail: process.version };
  if (runners.has('go')) {
    const gv = sh('go env GOVERSION').replace(/^go/, '');
    const ge124 = gv && sh(`printf '1.24\\n${gv}\\n' | sort -V | head -1`) === '1.24';
    checks.go = { ok: Boolean(gv), detail: gv || 'missing' };
    checks.go_json_build_events = { ok: Boolean(ge124), detail: 'go >= 1.24 reports build failures inside go test -json' };
    const needsRace = Object.values(cfg.layers).some((l) => l.runner === 'go' && l.race !== false);
    if (needsRace) checks.cgo_for_race = { ok: sh('go env CGO_ENABLED') === '1' && has('gcc'), detail: 'CGO_ENABLED=1 + gcc (for -race)' };
  }
  if (runners.has('pytest')) {
    checks.python3 = { ok: has('python3'), detail: sh('python3 --version') || 'missing' };
    checks.pytest = { ok: sh("python3 -c 'import pytest; print(pytest.__version__)'") !== '', detail: 'python3 -m pytest' };
  }
  if (cfg.sdd) {
    checks.python_yaml = { ok: sh(`${cfg.sdd.python} -c 'import yaml; print(1)'`) === '1', detail: `${cfg.sdd.python} + PyYAML (SDD index)` };
    if (runners.has('sdd-lint')) checks.sdd_framework = { ok: existsSync(path.resolve(ROOT, process.env.SDD_FRAMEWORK || cfg.sdd.framework, 'framework/registry/LAYER_REGISTRY.yaml')), detail: `aidoc-flow framework at ${process.env.SDD_FRAMEWORK || cfg.sdd.framework}` };
  }
  if (runners.has('node-command') || runners.has('playwright')) checks.node_modules = { ok: existsSync('node_modules'), detail: 'npm ci' };
  if (runners.has('playwright')) {
    checks.playwright = { ok: existsSync('node_modules/.bin/playwright'), detail: '@playwright/test installed' };
    const needsBrowser = Object.values(cfg.layers).some((l) => l.runner === 'playwright' && l.browser !== false && !/api/i.test(l.project ?? l.name));
    if (needsBrowser) checks.chromium = { ok: sh(`node -e "const fs=require('fs');process.exit(fs.existsSync(require('playwright-core').chromium.executablePath())?0:1)" && echo ok`) === 'ok', detail: 'task test:setup (npx playwright install chromium)' };
  }
  const env = stackEnv(cfg);
  const backendUrl = env.BACKEND_URL ?? '';
  const managed = process.env.BACKEND_MANAGED ? process.env.BACKEND_MANAGED === '1' : isLoopbackUrl(backendUrl) && Boolean(cfg.stack.compose);
  const needsAnyBackend = Object.values(cfg.layers).some((l) => l.needsStack || l.needsBackend);
  const needsStack = Object.values(cfg.layers).some((l) => l.needsStack);
  if (needsStack && managed) {
    checks.docker = { ok: sh('docker info >/dev/null 2>&1 && echo ok') === 'ok', detail: 'docker daemon' };
    checks.compose = { ok: sh('docker compose version >/dev/null 2>&1 && echo ok') === 'ok', detail: 'docker compose v2' };
  }
  if (needsAnyBackend) {
    const health = `${backendUrl.replace(/\/$/, '')}${env.BACKEND_HEALTH_PATH ?? cfg.stack.healthPath}`;
    checks.backend_reachable = { ok: Boolean(backendUrl) && sh(`curl -fsS -o /dev/null --max-time 3 ${shq(health)} && echo ok`) === 'ok', detail: backendUrl ? health : 'stack.backendUrl not set' };
  }

  const ok = (k) => checks[k]?.ok !== false;
  const req = (L) => {
    const need = [];
    if (L.runner === 'go') need.push('go', ...(L.race === false ? [] : ['cgo_for_race']));
    if (L.runner === 'pytest') need.push('python3', 'pytest');
    if (L.runner === 'sdd-lint') need.push('python_yaml', 'sdd_framework');
    if (L.runner === 'node-command') need.push('node_modules');
    if (L.runner === 'playwright') need.push('node_modules', 'playwright', ...(checks.chromium && L.browser !== false && !/api/i.test(L.project ?? L.name) ? ['chromium'] : []));
    if (L.needsStack) need.push(...(managed ? ['docker', 'compose'] : ['backend_reachable']));
    if (!L.needsStack && L.needsBackend) need.push('backend_reachable');
    return need;
  };
  const layers = {};
  for (const [name, L] of Object.entries(cfg.layers)) {
    const missing = req(L).filter((k) => !ok(k));
    layers[name] = { runnable: missing.length === 0, missing };
  }
  const report = { generatedAt: new Date().toISOString(), canonVersion: canonVersion(), backendManaged: managed, checks, layers };
  const R = resultsDir(cfg);
  mkdirSync(R, { recursive: true });
  writeFileSync(`${R}/doctor.json`, JSON.stringify(report, null, 2) + '\n');
  console.log(`unified-test-suite ${canonVersion()} · task ${checks.task.detail} · backend ${backendUrl || '—'} (managed=${managed})`);
  for (const [name, l] of Object.entries(layers)) {
    console.log(l.runnable ? `  ✔ ${name.padEnd(18)} runnable` : `  ✖ ${name.padEnd(18)} missing: ${l.missing.map((k) => `${k} (${checks[k]?.detail})`).join(', ')}`);
  }
  if (!checks.task.ok) console.log(`  ! task version ${checks.task.detail}`);
  console.log(`→ ${R}/doctor.json`);
  return 0;
}

export { path, readFileSync, CANON_DIR };
