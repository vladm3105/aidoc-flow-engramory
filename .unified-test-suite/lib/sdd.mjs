// canon/lib/sdd.mjs — SDD mode (aidoc-flow framework): index the project's SDD corpus, bind fresh test
// results to TDD test cases, roll status up to BDD scenarios and EARS requirements, emit runtime
// findings, and run the framework's document linter as a layer. Inert unless the config has an
// `sdd` block (standalone projects never reach this module's side effects).
//
// The canon reads SDD documents and never writes them (ADR-02). YAML is parsed by the vendored
// python3 + PyYAML helper sdd/sdd_index.py, so this file keeps the canon's zero-dependency rule.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync, statSync, readdirSync, cpSync, rmSync } from 'node:fs';
import path from 'node:path';
import { ROOT, CANON_DIR, loadConfig, resultsDir, toPosix, shq, matchesAny } from './core.mjs';

export const SDD_DEFAULTS = { docsRoot: 'docs/sdd', framework: '.aidoc/framework', python: 'python3', mode: 'build', profile: '.aidoc/profile.yaml', journeys: 'tests/journeys' };
/** Registry element grammar (framework/registry/LAYER_REGISTRY.yaml id_patterns.element), used when no checkout is found. */
export const DEFAULT_ELEMENT_ID = '[A-Z]+\\.\\d{2,}\\.\\d{2,}\\.[a-f0-9]{4,8}';
const RULES = {
  'UTS-SDD-000': 'index',
  'UTS-SDD-001': 'TDD-SYNC-C',
  'UTS-SDD-004': 'EVAL-COV-003',
};

export const sddEnabled = (cfg) => Boolean(cfg.sdd);
/** Effective mode for a target: layer override → gate sddMode → sdd.mode → build. */
export function sddMode(cfg, target, layerMode) {
  return layerMode ?? cfg.gates?.[target]?.sddMode ?? cfg.sdd?.mode ?? 'build';
}
export function frameworkDir(cfg) {
  const fw = process.env.SDD_FRAMEWORK || cfg.sdd?.framework || SDD_DEFAULTS.framework;
  return path.resolve(ROOT, fw);
}
/** docsRoot as a normalized repo-relative path, or null when absolute or outside the repo. */
export function docsRootRel(cfg) {
  const raw = String(cfg.sdd?.docsRoot ?? '');
  if (!raw || path.isAbsolute(raw)) return null;
  const n = path.posix.normalize(toPosix(raw)).replace(/^\.\//, '').replace(/\/$/, '');
  return n === '..' || n.startsWith('../') || n === '.' ? null : n;
}
export const registryFile = (cfg) => path.join(frameworkDir(cfg), 'framework', 'registry', 'LAYER_REGISTRY.yaml');

/** Element-ID grammar from the framework registry (no YAML parser needed for one scalar). */
export function elementPattern(cfg) {
  try {
    const m = readFileSync(registryFile(cfg), 'utf8').match(/^\s*element:\s*"([^"]+)"/m);
    if (m) return m[1].replace(/\\\\/g, '\\').replace(/^\^/, '').replace(/\$$/, '');
  } catch { /* no checkout: registry default */ }
  return DEFAULT_ELEMENT_ID;
}
export const bddIdPattern = (cfg) => elementPattern(cfg).replace(/^\[A-Z\]\+/, 'BDD');

// ------------------------------------------------------------------ index
/** Run sdd_index.py. Returns { index, error } and writes <results>/sdd/index.json. */
export function loadIndex(cfg) {
  const s = cfg.sdd;
  const helper = path.join(CANON_DIR, 'sdd', 'sdd_index.py');
  const args = [helper, '--root', s.docsRoot, '--id-pattern', elementPattern(cfg)];
  if (s.profile && existsSync(s.profile)) args.push('--profile', s.profile);
  if (s.journeys && existsSync(path.resolve(ROOT, s.journeys))) args.push('--journeys', s.journeys);
  const r = spawnSync(s.python ?? 'python3', args, { cwd: ROOT, encoding: 'utf8', maxBuffer: 1 << 28 });
  let index = null; let error = null;
  if (r.error) error = `cannot run ${s.python ?? 'python3'}: ${r.error.message}`;
  else {
    try { index = JSON.parse(r.stdout); } catch { error = `sdd_index.py produced no JSON (exit ${r.status}): ${(r.stderr || r.stdout).trim().split('\n').slice(-1)[0] ?? ''}`; }
    if (index?.fatal) { error = index.fatal; index = null; }
  }
  const out = path.join(resultsDir(cfg), 'sdd', 'index.json');
  mkdirSync(path.dirname(out), { recursive: true });
  writeFileSync(out, JSON.stringify(index ?? { error }, null, 2) + '\n');
  return { index, error, path: out };
}

// ------------------------------------------------------------------ binding
const normFile = (f) => (f ? path.posix.normalize(toPosix(String(f))).replace(/^\.\//, '').replace(/\/$/, '') : null);
const lastSeg = (fn) => String(fn).split(/::|\//).filter(Boolean).pop() ?? String(fn);
const NODE_RUNNERS = new Set(['node-test', 'node-command']);

/** Repo-relative forms of a TDD test_file: as written, and under the layer's `dir` (monorepos). */
const fileForms = (testFile, L) => [normFile(testFile), ...(L?.dir ? [normFile(path.posix.join(toPosix(L.dir), toPosix(String(testFile))))] : [])].filter(Boolean);

function fileMatches(t, testFile, L, uniqueNodeTitle, testFunction, onDisk) {
  for (const want of fileForms(testFile, L)) {
    if (t.file && normFile(t.file) === want) return true;
    // Go reports passing tests (and build/package failures) by package directory, not file.
    if (t.srcDir != null && /_test\.go$/.test(want) && normFile(path.posix.dirname(want)) === normFile(t.srcDir)) return true;
  }
  // node's JUnit carries no file for passing tests: bind by title only when the case names a
  // function, the title is unique among file-less node results, the file exists and contains
  // that title, and (node-test) the layer's globs cover the file.
  if (!t.file && testFunction && NODE_RUNNERS.has(L?.runner) && uniqueNodeTitle(t) && /\.[cm]?[jt]sx?$/.test(String(testFile))
    && onDisk && sourceHas(onDisk, testFunction)) {
    if (L.runner === 'node-test') {
      const rel = (f) => (L.dir ? normFile(path.posix.relative(toPosix(L.dir), f)) : f);
      return fileForms(testFile, L).some((f) => matchesAny(rel(f), L.files ?? ['test/**/*.test.mjs']) || matchesAny(f, L.files ?? []));
    }
    return true;
  }
  return false;
}
function fnMatches(t, testFunction) {
  if (!testFunction || t.wholeFile || t.wholeDir) return true;
  const w = String(testFunction).trim().replace(/^.*?\.py::/, '');
  const names = [t.fn, t.rawName, t.fnQualified, t.fnFull, t.title].filter(Boolean);
  if (names.includes(w)) return true;
  if (t.fnQualified && t.fnQualified === w.replace(/\./g, '::')) return true;
  if (t.fnQualified && t.rawName && `${t.fnQualified.split('::').slice(0, -1).concat(t.rawName).join('::')}` === w) return true;
  return false;
}
function sourceHas(testFile, testFunction) {
  try {
    if (statSync(testFile).isDirectory()) return true;
    const src = readFileSync(testFile, 'utf8');
    const raw = String(testFunction).replace(/^.*?\.py::/, '');
    const base = raw.replace(/\[.*\]$/, ''); // pytest parameters may contain "/" (URL paths)
    const needles = /^[A-Z]+\.\d/.test(raw) ? [raw] : [...new Set([lastSeg(base), lastSeg(base).replace(/_/g, ' ')])];
    return needles.some((n) => (/^[\w$]+$/.test(n) ? new RegExp(`\\b${n.replace(/\$/g, '\\$')}\\b`).test(src) : src.includes(n)));
  } catch { return false; }
}
const statusOf = (bound) => {
  if (!bound.length) return null;
  if (bound.some((t) => t.status === 'failed')) return 'failed';
  if (bound.some((t) => t.status === 'flaky')) return 'flaky';
  if (bound.every((t) => t.status === 'skipped')) return 'skipped';
  return 'passed';
};
const pct = (n, d) => (d ? Math.round((n / d) * 1000) / 10 : null);
const scalar = (v) => (v == null || typeof v === 'object' ? null : String(v));

/**
 * Build summary.sdd from the index and the parsed tests of fresh layers.
 * @param tests  [{ layer, title, file, line, status, fn, rawName, fnQualified, fnFull, srcDir, tags, wholeFile, wholeDir }]
 */
export function sddReport(cfg, target, tests, loaded = loadIndex(cfg)) {
  const mode = sddMode(cfg, target);
  const severity = mode === 'gate-code' ? 'error' : 'warning';
  const findings = [];
  const seen = new Set();
  const F = (code, ref, file, message) => {
    const key = `${code}|${ref}|${message}`;
    if (seen.has(key)) return;
    seen.add(key);
    findings.push({ code, implements: RULES[code], severity, ref: String(ref ?? ''), file: file ?? null, message });
  };
  const base = { mode, docsRoot: cfg.sdd.docsRoot, index: { ok: !loaded.error, error: loaded.error, path: loaded.path } };
  if (loaded.error) {
    F('UTS-SDD-000', cfg.sdd.docsRoot, null, `SDD index failed: ${loaded.error}`);
    return { ...base, totals: null, tdd: null, bdd: null, ears: null, unmapped: null, findings };
  }
  const ix = loaded.index;
  for (const e of ix.errors ?? []) F('UTS-SDD-000', e.file, e.file, e.message);
  const ears = new Map(ix.ears.map((e) => [e.id, e]));
  const bdd = new Map(ix.bdd.map((b) => [b.id, b]));
  const tdd = new Map(ix.tdd.map((c) => [c.id, c]));
  const known = (id) => ears.has(id) || bdd.has(id) || tdd.has(id);
  const unknownRef = (id, file, where) => { if (!known(id) && /^(EARS|BDD|TDD)\./.test(id)) F('UTS-SDD-004', id, file, `${where} references ${id}, which is not in the SDD corpus (${cfg.sdd.docsRoot})`); };

  const layerOf = (t) => cfg.layers[t.layer];
  const nodeTitles = new Map();
  for (const t of tests) if (!t.file && NODE_RUNNERS.has(layerOf(t)?.runner)) nodeTitles.set(t.title, (nodeTitles.get(t.title) ?? 0) + 1);
  const uniqueNodeTitle = (t) => nodeTitles.get(t.title) === 1;
  const layerDirs = [...new Set(Object.values(cfg.layers).map((L) => L.dir).filter(Boolean))];
  const resolveFile = (f) => [f, ...layerDirs.map((d) => path.join(d, f))].map((x) => path.resolve(ROOT, x)).find((x) => existsSync(x)) ?? null;

  const bound = new Set();
  const bind = (entry) => {
    const testFile = scalar(entry.testFile); const testFunction = scalar(entry.testFunction);
    const tagged = tests.filter((t) => entry.id && (t.tags ?? []).some((g) => g.replace(/^@/, '') === entry.id));
    const onDiskFile = testFile ? resolveFile(testFile) : null;
    const byName = testFile ? tests.filter((t) => fileMatches(t, testFile, layerOf(t), uniqueNodeTitle, testFunction, onDiskFile) && fnMatches(t, testFunction)) : [];
    const hits = [...new Set([...tagged, ...byName])];
    hits.forEach((t) => bound.add(t));
    let status = statusOf(hits); let reason = null;
    if (!status) {
      const onDisk = onDiskFile;
      if (!testFile) { status = 'not_run'; reason = 'no test_file and no tagged test'; }
      else if (!onDisk) { status = 'missing'; reason = `test_file ${testFile} does not exist`; }
      else if (testFunction && !sourceHas(onDisk, testFunction) && !(entry.id && sourceHas(onDisk, entry.id))) { status = 'missing'; reason = `test_function ${testFunction} not found in ${testFile}`; }
      else { status = 'not_run'; reason = 'no fresh result for this test'; }
    }
    return { testFile, testFunction, status, reason, tests: hits.map(({ layer, title, file, line, status: st }) => ({ layer, title, file, line, status: st })) };
  };

  const cases = ix.tdd.map((c) => ({ id: c.id, doc: c.doc, type: c.type ?? null, bdd: c.bdd, ears: c.ears, ...bind(c) }));
  const maps = ix.mappings.map((m) => ({ ...m, ...bind(m) }));
  for (const c of cases) {
    if (c.status === 'missing') F('UTS-SDD-001', c.id, c.testFile, `${c.id}: ${c.reason}`);
    for (const id of [...c.bdd, ...c.ears]) unknownRef(id, tdd.get(c.id)?.file, c.id);
  }
  for (const m of maps) {
    if (m.status === 'missing') F('UTS-SDD-001', m.bdd[0] ?? m.testFile, m.file, `test_mapping ${m.bdd.join(', ') || '(no scenario)'} → ${m.testFile}${m.testFunction ? `::${m.testFunction}` : ''}: ${m.reason}`);
    for (const id of m.bdd) unknownRef(id, m.file, `test_mapping in ${m.doc || m.file}`);
  }
  for (const b of ix.bdd) for (const id of b.ears) unknownRef(id, b.file, b.id);
  for (const e of ix.evals) for (const c of e.cases) if (c.sourceId) unknownRef(String(c.sourceId).replace(/^@\w+:\s*/, ''), e.file, `${e.id} ${c.id ?? ''}`.trim());
  for (const p of ix.iplans) for (const f of p.manifest) for (const id of f.tdd) unknownRef(id, p.file, `${p.id} file_manifest ${f.path}`);
  for (const t of tests) for (const g of t.tags ?? []) unknownRef(g.replace(/^@/, ''), t.file, `test "${t.title}" tag`);

  // --- roll-up: TDD → BDD scenario → BDD feature → EARS
  const ok = (st) => st === 'passed' || st === 'flaky';
  const rollup = (entries) => (!entries.length ? 'unpaired' : entries.some((p) => p.status === 'failed' || p.status === 'failing') ? 'failing'
    : entries.every((p) => ok(p.status) || p.status === 'verified') ? 'verified' : 'at_risk');
  const pairedOf = (id) => [...cases.filter((c) => c.bdd.includes(id)), ...maps.filter((m) => m.bdd.includes(id))];
  const label = (p) => p.id ?? `${p.testFile}${p.testFunction ? `::${p.testFunction}` : ''}`;
  const scen = new Map();
  for (const b of ix.bdd.filter((x) => x.kind !== 'feature')) {
    const paired = pairedOf(b.id);
    scen.set(b.id, { id: b.id, title: b.title, kind: 'scenario', status: rollup(paired), pairedWith: [...new Set(paired.map(label))] });
  }
  // A feature covers the scenarios of its BDD document (template: feature coverage = union of its scenarios).
  for (const b of ix.bdd.filter((x) => x.kind === 'feature')) {
    const doc = b.id.split('.').slice(0, 2).join('.');
    const own = pairedOf(b.id);
    const kids = [...scen.values()].filter((s) => s.id.startsWith(`${doc}.`));
    // An unpaired scenario keeps its feature from being verified (it counts as at_risk).
    const anyPaired = own.length || kids.some((k) => k.status !== 'unpaired');
    const entries = [...own, ...kids.map((k) => (k.status === 'unpaired' ? { status: 'at_risk' } : k))];
    scen.set(b.id, { id: b.id, title: b.title, kind: 'feature', status: anyPaired ? rollup(entries) : 'unpaired', pairedWith: [...new Set(own.map(label))], scenarios: kids.map((k) => k.id) });
  }
  const scenarios = ix.bdd.map((b) => scen.get(b.id));
  const requirements = ix.ears.map((e) => {
    const bs = ix.bdd.filter((b) => b.ears.includes(e.id)).map((b) => scen.get(b.id));
    const cs = cases.filter((c) => c.ears.includes(e.id));
    let status = 'uncovered';
    if (bs.length || cs.length) {
      if (bs.some((s) => s.status === 'failing') || cs.some((c) => c.status === 'failed')) status = 'failing';
      else if (bs.every((s) => s.status === 'verified') && cs.every((c) => ok(c.status))) status = 'verified';
      else status = 'at_risk';
    }
    return { id: e.id, title: e.title, status, scenarios: bs.map((s) => s.id), cases: cs.map((c) => c.id) };
  });
  const count = (arr, keys) => Object.fromEntries(keys.map((k) => [k, arr.filter((x) => x.status === k).length]));
  const onlyScenarios = scenarios.filter((s) => s.kind === 'scenario');
  const tddCounts = count(cases, ['passed', 'failed', 'flaky', 'skipped', 'missing', 'not_run']);
  const bddCounts = count(onlyScenarios, ['verified', 'failing', 'at_risk', 'unpaired']);
  const earsCounts = count(requirements, ['verified', 'failing', 'at_risk', 'uncovered']);
  const counted = (t) => !['sdd-lint', 'steps'].includes(layerOf(t)?.runner);
  return {
    ...base,
    totals: { ears: ix.ears.length, bdd: ix.bdd.length, scenarios: onlyScenarios.length, tdd: ix.tdd.length, mappings: ix.mappings.length },
    tdd: { counts: tddCounts, cases: cases.map(({ bdd: _b, ears: _e, ...c }) => c) },
    bdd: { counts: bddCounts, verifiedPercent: pct(bddCounts.verified, onlyScenarios.length), scenarios },
    ears: { counts: earsCounts, verifiedPercent: pct(earsCounts.verified, requirements.length), requirements },
    unmapped: tests.filter((t) => counted(t) && !bound.has(t)).length,
    findings,
  };
}

/** summary.bdd in SDD mode: derived from TDD pairing, not from tags. */
export function sddBdd(sdd) {
  const sc = (sdd.bdd?.scenarios ?? []).filter((s) => s.kind === 'scenario');
  return {
    source: 'sdd',
    covered: sc.filter((s) => s.status === 'verified').map((s) => s.id).sort(),
    failing: sc.filter((s) => s.status === 'failing').map((s) => s.id).sort(),
    totalScenarios: sdd.bdd ? sc.length : null,
    coveredPercent: sdd.bdd?.verifiedPercent ?? null,
  };
}

// ------------------------------------------------------------------ sdd-lint runner
/** Preflight for sdd-lint layers: the framework checkout must exist (exit 3 = infra). */
export function sddLintPreflight() {
  const cfg = loadConfig();
  if (!cfg.sdd) { console.error('INFRA: sdd-lint layer but the config has no sdd block'); return 3; }
  if (!existsSync(registryFile(cfg))) {
    console.error(`INFRA: aidoc-flow framework checkout not found at ${frameworkDir(cfg)} (set SDD_FRAMEWORK or sdd.framework)`);
    return 3;
  }
  const r = spawnSync(cfg.sdd.python ?? 'python3', ['-c', 'import yaml'], { encoding: 'utf8' });
  if (r.status !== 0) { console.error(`INFRA: ${cfg.sdd.python ?? 'python3'} with PyYAML is required for sdd_doc_lint`); return 3; }
  return 0;
}

// eslint-disable-next-line no-control-regex
const xmlEsc = (s) => String(s ?? '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
function corpusFiles(root) {
  const out = [];
  const walk = (d) => {
    for (const e of readdirSync(d, { withFileTypes: true })) {
      if (e.isDirectory()) { if (!['archive', 'reports', 'node_modules', '.git'].includes(e.name)) walk(path.join(d, e.name)); }
      else if (/\.(md|ya?ml)$/.test(e.name)) out.push(toPosix(path.relative(root, path.join(d, e.name))));
    }
  };
  walk(root);
  return out.sort();
}

/**
 * Run sdd_doc_lint --format json over sdd.docsRoot and write JUnit: one failing testcase per error
 * finding (file:line), one passing testcase per clean document. The corpus is copied without
 * archive/ and reports/ first (upstream U8, U10) and linted as one corpus.
 */
export function sddLint({ layer, reportDir, mode: modeArg } = {}) {
  const cfg = loadConfig();
  if (!cfg.sdd) { console.error('sdd-lint: the config has no sdd block'); return 2; }
  const mode = modeArg ?? sddMode(cfg, process.env.UTS_GATE, cfg.layers[layer]?.mode);
  reportDir = reportDir ?? path.join(resultsDir(cfg), 'reports', layer ?? 'sdd-lint');
  const fw = frameworkDir(cfg);
  const root = docsRootRel(cfg);
  if (!root) { console.error(`sdd-lint: sdd.docsRoot "${cfg.sdd.docsRoot}" must be a relative path inside the repository`); return 2; }
  // One staging corpus per layer: two sdd-lint layers may run in parallel in one gate.
  const stage = path.resolve(ROOT, resultsDir(cfg), 'sdd', 'lint-corpus', layer ?? 'default');
  rmSync(stage, { recursive: true, force: true });
  mkdirSync(reportDir, { recursive: true });
  for (const stale of ['junit.xml', 'sdd-lint.json']) rmSync(path.join(reportDir, stale), { force: true });
  const files = corpusFiles(path.resolve(ROOT, root));
  for (const f of files) {
    mkdirSync(path.dirname(path.join(stage, root, f)), { recursive: true });
    cpSync(path.resolve(ROOT, root, f), path.join(stage, root, f));
  }
  // sdd_doc_lint discovers the profile only at .aidoc/profile.yaml (walking up from the corpus).
  if (cfg.sdd.profile && existsSync(cfg.sdd.profile)) { mkdirSync(path.join(stage, '.aidoc'), { recursive: true }); cpSync(cfg.sdd.profile, path.join(stage, '.aidoc', 'profile.yaml')); }
  const env = { ...process.env, PYTHONPATH: [fw, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter), PYTHONDONTWRITEBYTECODE: '1' };
  const r = spawnSync(cfg.sdd.python ?? 'python3', ['-m', 'sdd_doc_lint', '--format', 'json', '--mode', mode, '--registry', registryFile(cfg), root], { cwd: stage, env, encoding: 'utf8', maxBuffer: 1 << 28 });
  let findings;
  try { findings = JSON.parse(r.stdout); } catch {
    console.error(`sdd-lint: sdd_doc_lint produced no JSON (exit ${r.status}): ${(r.stderr || '').trim().split('\n').slice(-3).join(' | ')}`);
    return r.status || 2;
  }
  const errors = findings.filter((f) => f.severity === 'error');
  const warnings = findings.filter((f) => f.severity !== 'error');
  const cases = [];
  const norm = (f) => normFile(f);
  for (const f of errors) {
    const file = norm(f.file);
    cases.push(`  <testcase classname="sdd_doc_lint" name="${xmlEsc(`${f.code} ${file}:${f.line}`)}" file="${xmlEsc(file)}" line="${Math.max(1, Number(f.line) || 1)}"><failure message="${xmlEsc(`[${f.code}] ${f.message}`)}">${xmlEsc(`${file}:${Math.max(1, Number(f.line) || 1)}: [${f.code}] ${f.message}`)}</failure></testcase>`);
  }
  const dirty = new Set(errors.map((f) => norm(f.file)));
  for (const f of files) if (!dirty.has(`${root}/${f}`)) cases.push(`  <testcase classname="sdd_doc_lint" name="${xmlEsc(`${root}/${f}`)}" file="${xmlEsc(`${root}/${f}`)}"/>`);
  writeFileSync(path.join(reportDir, 'junit.xml'), `<?xml version="1.0" encoding="UTF-8"?>\n<testsuites><testsuite name="sdd-lint (${mode})" tests="${cases.length}" failures="${errors.length}">\n${cases.join('\n')}\n</testsuite></testsuites>\n`);
  writeFileSync(path.join(reportDir, 'sdd-lint.json'), JSON.stringify({ mode, framework: fw, findings }, null, 2) + '\n');
  for (const w of warnings.slice(0, 20)) console.log(`warning ${w.file}:${w.line} [${w.code}] ${w.message}`);
  console.log(`sdd-lint (${mode}): ${files.length} document(s), ${errors.length} error(s), ${warnings.length} warning(s)`);
  return 0;
}

export const sddLintCommand = (uts, layer, out) => `${uts} sdd-lint --layer ${shq(layer)} --report-dir ${shq(out)}`;
