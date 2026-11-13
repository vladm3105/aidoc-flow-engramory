// canon/lib/check.mjs — `uts check` (adoption + integrity), `uts bdd-check`, `uts freeze-check`.
// check: errors block (exit 1); TODOs are adoption gaps (exit 0, or 1 with --strict).
import { readFileSync, existsSync, writeFileSync, accessSync, constants, readdirSync, mkdtempSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import {
  ROOT, CANON_DIR, VENDOR_DIR, CONFIG_FILE, RUNNERS, RESERVED_NAMES, NAME_RE, loadConfig, rawConfig, layerSteps, findFiles, repoFiles, readJson,
  canonVersion, globToRegExp, resultsDir, generatedDir,
} from './core.mjs';
import { loadIndex, bddIdPattern, registryFile, frameworkDir, docsRootRel } from './sdd.mjs';
import { checkJourneys } from './journeys.mjs';
import { verifyLock, renderGenerated, verifyUpstream } from './sync.mjs';

// ------------------------------------------------------------------ minimal JSON-schema validator
// Supports the subset config.schema.json uses: type, enum, const, required, properties,
// additionalProperties, propertyNames (pattern/not-enum), items, minItems, minProperties, oneOf, $ref.
export function validate(schema, value, at = '$', root = schema, errs = []) {
  if (schema.$ref) return validate(schema.$ref.replace('#/', '').split('/').reduce((o, k) => o[k], root), value, at, root, errs);
  const t = value === null ? 'null' : Array.isArray(value) ? 'array' : Number.isInteger(value) ? 'integer' : typeof value;
  if (schema.type) {
    const types = [schema.type].flat();
    if (!types.includes(t) && !(t === 'integer' && types.includes('number'))) { errs.push(`${at}: expected ${types.join('|')}, got ${t}`); return errs; }
  }
  if (schema.enum && !schema.enum.includes(value)) errs.push(`${at}: must be one of ${schema.enum.filter((x) => x !== null).join(', ')}`);
  if (schema.const !== undefined && value !== schema.const) errs.push(`${at}: must be ${schema.const}`);
  if (schema.oneOf) {
    const ok = schema.oneOf.filter((s2) => validate(s2, value, at, root, []).length === 0).length;
    if (ok !== 1) errs.push(`${at}: does not match exactly one allowed form`);
  }
  if (t === 'object') {
    for (const r of schema.required ?? []) if (!(r in value)) errs.push(`${at}: missing "${r}"`);
    if (schema.minProperties && Object.keys(value).length < schema.minProperties) errs.push(`${at}: needs at least ${schema.minProperties} entr${schema.minProperties > 1 ? 'ies' : 'y'}`);
    for (const [k, v] of Object.entries(value)) {
      if (schema.propertyNames?.pattern && !new RegExp(schema.propertyNames.pattern).test(k)) errs.push(`${at}: key "${k}" must match ${schema.propertyNames.pattern}`);
      if (schema.propertyNames?.not?.enum?.includes(k)) errs.push(`${at}: key "${k}" is reserved`);
      if (schema.properties?.[k]) validate(schema.properties[k], v, `${at}.${k}`, root, errs);
      else if (schema.additionalProperties === false) errs.push(`${at}: unknown key "${k}"${suggest(k, Object.keys(schema.properties ?? {}))}`);
      else if (typeof schema.additionalProperties === 'object') validate(schema.additionalProperties, v, `${at}.${k}`, root, errs);
    }
  }
  if ((t === 'number' || t === 'integer') && schema.minimum !== undefined && value < schema.minimum) errs.push(`${at}: must be ≥ ${schema.minimum}`);
  if (t === 'array') {
    if (schema.minItems && value.length < schema.minItems) errs.push(`${at}: needs at least ${schema.minItems} item(s)`);
    if (schema.items) value.forEach((v, i) => validate(schema.items, v, `${at}[${i}]`, root, errs));
  }
  return errs;
}
function suggest(k, known) {
  const d = (a, b) => { const m = Array.from({ length: a.length + 1 }, (_, i) => [i]); for (let j = 1; j <= b.length; j++) m[0][j] = j; for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) m[i][j] = Math.min(m[i - 1][j] + 1, m[i][j - 1] + 1, m[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)); return m[a.length][b.length]; };
  const best = known.map((x) => [x, d(k, x)]).sort((a, b) => a[1] - b[1])[0];
  return best && best[1] <= 3 ? ` (did you mean "${best[0]}"?)` : '';
}

export function check({ strict = false, json = false, upstream = null } = {}) {
  const errors = []; const todos = []; const notes = [];
  const E = (m) => errors.push(m); const T = (m) => todos.push(m);
  let cfg;
  try { cfg = loadConfig(); } catch (e) { E(e.message); return report(); }
  const schema = readJson(path.join(CANON_DIR, 'schemas', 'config.schema.json'));
  for (const e of validate(schema, rawConfig())) E(`config ${e}`);

  // --- integrity of the vendored canon
  const lock = verifyLock();
  lock.errors.forEach(E);
  if (upstream) {
    try { const up = verifyUpstream(upstream); up.forEach(E); if (!up.length) notes.push(`lock matches upstream ${upstream}${lock.lock?.ref ? `@${lock.lock.ref}` : ''}`); } catch (e) { E(`upstream verification failed: ${e.message}`); }
  }
  if (lock.lock) notes.push(`canon ${lock.lock.version}${lock.lock.ref ? ` (${lock.lock.ref})` : ''} · vendored copy ${lock.ok ? 'intact' : 'MODIFIED'}`);

  // --- config structure
  const names = Object.keys(cfg.layers);
  if (!names.length) E('config: no layers configured');
  const taskNames = new Map();
  const claim = (kind, n) => { if (taskNames.has(n)) E(`config: ${kind} "${n}" collides with ${taskNames.get(n)} "${n}" (both would be task test:${n})`); taskNames.set(n, kind); };
  for (const [n, L] of Object.entries(cfg.layers)) {
    claim('layer', n);
    if (L.dir && L.runner === 'playwright') E(`layer "${n}": "dir" is not supported for playwright (use layer.config)`);
    if (L.dir && !existsSync(L.dir)) E(`layer "${n}": dir ${L.dir} not found`);
    if ((L.needsStack || L.needsBackend) && !cfg.stack.backendUrl && !process.env.BACKEND_URL) E(`layer "${n}": needs a backend but stack.backendUrl is not set`);
    if (!RUNNERS.includes(L.runner)) { E(`layer "${n}": unknown runner "${L.runner}" (one of ${RUNNERS.join(', ')})`); continue; }
    if (L.runner === 'node-command' && !L.command) E(`layer "${n}": node-command needs "command"`);
    if (L.runner === 'command' && !L.run) E(`layer "${n}": command runner needs "run"`);
    if (L.runner === 'steps') {
      if (typeof L.steps === 'string' && !existsSync(L.steps)) E(`layer "${n}": step list ${L.steps} not found`);
      else if (!layerSteps(L).length) E(`layer "${n}": has zero steps${typeof L.steps === 'string' ? ` (${L.steps} is empty)` : ''} — add "name :: command" lines or remove the layer; an empty layer reports infra_error`);
      const seen = new Set();
      for (const st of layerSteps(L)) {
        if (!st.name) E(`layer "${n}": a step has an empty name`);
        if (!st.run) E(`layer "${n}": step "${st.name}" has an empty command`);
        if (seen.has(st.name)) E(`layer "${n}": duplicate step name "${st.name}"`);
        seen.add(st.name);
      }
    }
    if (L.runner === 'playwright') {
      const conf = L.config ?? cfg.e2e.config;
      if (!conf) E(`layer "${n}": playwright needs e2e.config (or layer.config)`);
      else if (!existsSync(conf)) E(`layer "${n}": playwright config ${conf} not found`);
      else if (!readFileSync(conf, 'utf8').includes('defineCanonConfig')) E(`${conf}: must export defineCanonConfig(...) from ${VENDOR_DIR}/playwright/config (output isolation, reporters, identity workers)`);
    }
    if (L.needsApp && !cfg.stack.appUrl) E(`layer "${n}": needsApp but stack.appUrl is not set`);
  }
  for (const [kind, names2] of [['group', Object.keys(cfg.groups ?? {})], ['gate', Object.keys(cfg.gates ?? {})], ['layer', names]]) {
    for (const g of names2) {
      if (!NAME_RE.test(g)) E(`${kind} "${g}": name must match ${NAME_RE}`);
      if (RESERVED_NAMES.includes(g)) E(`${kind} "${g}": reserved name (${RESERVED_NAMES.join(', ')})`);
    }
  }
  for (const [g, layers] of Object.entries(cfg.groups ?? {})) {
    claim('group', g);
    for (const l of layers) if (!cfg.layers[l]) E(`group "${g}": unknown layer "${l}"`);
  }
  for (const [g, gate] of Object.entries(cfg.gates ?? {})) {
    claim('gate', g);
    for (const l of gate.layers ?? []) if (!cfg.layers[l]) E(`gate "${g}": unknown layer "${l}"`);
  }
  if (!cfg.gates.pr) T('gates: no "pr" gate — agents default to `task test:pr` before opening a PR');

  // --- wiring
  const genDir = generatedDir();
  const gen = path.join(genDir, 'Taskfile.yml');
  const genRel = path.relative(ROOT, gen);
  if (!existsSync(gen) || readFileSync(gen, 'utf8') !== renderGenerated(cfg)) E(`${genRel} is out of sync with ${CONFIG_FILE} — run \`task uts:gen\` (or node ${VENDOR_DIR}/bin/uts.mjs gen) and commit it`);
  const tf = existsSync('Taskfile.yml') ? readFileSync('Taskfile.yml', 'utf8') : '';
  if (!tf) E('Taskfile.yml missing at repo root');
  else if (!tf.includes(`${VENDOR_DIR}/Taskfile.yml`) || !/flatten:\s*true/.test(tf)) E(`Taskfile.yml must include ${VENDOR_DIR}/Taskfile.yml with flatten: true`);
  if (/^\s*dotenv\s*:/m.test(tf)) E('Taskfile.yml uses dotenv: — the canon forbids loading .env into task/test processes (CANON.md §3.2)');
  const rd = cfg.resultsDir.replace(/\/$/, '');
  if (spawnSync('git', ['check-ignore', '-q', `${rd}/x`]).status !== 0) E(`${rd}/ (resultsDir) is not gitignored (results must never be committed)`);
  const eff = resultsDir(cfg).replace(/\/$/, '');
  if (eff !== rd && spawnSync('git', ['check-ignore', '-q', `${eff}/x`]).status !== 0) T(`RESULTS_DIR=${eff} is not gitignored — do not commit it (freshness is unaffected)`);
  // Prove the Task wiring: duplicate or colliding task names make every `task` command fail.
  const tl = spawnSync('task', ['--list-all', '--json'], { encoding: 'utf8' });
  if (tl.error) notes.push('task not installed — Task wiring not verified');
  else if (tl.status !== 0) E(`task cannot load the Taskfile: ${(tl.stderr || tl.stdout).trim().split('\n').slice(-1)[0]}`);
  if (existsSync(genDir)) for (const f of readdirSync(genDir)) if (f !== 'Taskfile.yml') E(`${path.relative(ROOT, genDir)}/${f}: unexpected file (only Taskfile.yml is generated)`);

  // --- adoption TODOs
  const stackLayers = names.filter((n) => cfg.layers[n].needsStack);
  if (stackLayers.length && cfg.stack.compose && !cfg.stack.dbReset) T(`stack.dbReset not set — stateful layers (${stackLayers.join(', ')}) will share state between runs on managed stacks`);
  if (cfg.stack.dbReset) {
    const first = cfg.stack.dbReset.split(/\s+/)[0];
    if (/\//.test(first) && !existsSync(first)) E(`stack.dbReset: ${first} not found`);
    else if (/\//.test(first)) { try { accessSync(first, constants.X_OK); } catch { if (!/^(bash|sh|node|python3?)$/.test(first)) E(`stack.dbReset: ${first} is not executable`); } }
  }
  for (const n of names) {
    const L = cfg.layers[n];
    const measurable = L.runner === 'go' || (L.runner === 'pytest' && L.coverage);
    if (measurable && cfg.coverage?.[n]?.minPercent == null) T(`coverage.${n}.minPercent not set — coverage is reported but not enforced`);
    if (L.runner === 'pytest' && cfg.coverage?.[n]?.minPercent != null && !L.coverage) E(`coverage.${n}.minPercent set but layer ${n} has no "coverage" (pytest-cov) configured`);
  }
  if (stackLayers.length && cfg.providers == null) T('providers not declared — list each third-party provider with its env switch and mode per gate, or set "providers": {} if there are none');
  for (const [p, P] of Object.entries(cfg.providers ?? {})) {
    if (!P.env) E(`providers.${p}: "env" (the switch your app reads) is required`);
    for (const [g, gate] of Object.entries(cfg.gates)) {
      const touchesStack = (gate.layers ?? []).some((l) => cfg.layers[l]?.needsStack);
      if (touchesStack && P.modes?.[g] == null && P.modes?.default == null) T(`providers.${p}: no mode for gate "${g}" (and no default)`);
    }
  }
  const pw = names.filter((n) => cfg.layers[n].runner === 'playwright');
  if (pw.length) {
    const poolEnv = cfg.e2e.identityPoolEnv;
    const per = cfg.e2e.identitiesPerWorker ?? 1;
    const need = per * (cfg.e2e.minWorkersInCI ?? 1);
    if (process.env.CI) {
      let size = 0; try { size = JSON.parse(process.env[poolEnv] ?? '[]').length; } catch { E(`${poolEnv} is not valid JSON`); }
      if (size < need) T(`${poolEnv} has ${size} identities; CI wants ≥ ${need} (${per}/worker × ${cfg.e2e.minWorkersInCI ?? 1} workers) — E2E runs serially until then`);
    } else notes.push(`E2E identity pool: ${poolEnv} (${per} per worker)`);
  }
  if (cfg.bdd.enabled && !cfg.sdd) {
    const cat = findFiles(cfg.bdd.catalogGlobs ?? []);
    if (!cat.length) (cfg.bdd.requireCatalog ? E : T)(`bdd: no catalog found at ${JSON.stringify(cfg.bdd.catalogGlobs)} — IDs are format-checked only${cfg.bdd.requireCatalog ? '' : ' (set bdd.requireCatalog: true once found)'}`);
    else if (!cfg.bdd.requireCatalog) T('bdd.requireCatalog is false — typo IDs can count toward coverage');
    if (!findFiles(cfg.bdd.specGlobs ?? []).length) T(`bdd.specGlobs ${JSON.stringify(cfg.bdd.specGlobs)} match no spec files`);
  }
  if (cfg.selection) {
    const globs = [...(cfg.selection.rules ?? []).flatMap((r) => r.paths), ...(cfg.selection.fullRunTriggers ?? [])];
    const all = repoFiles();
    const dead = globs.filter((g) => { const re = globToRegExp(g); return !all.some((f) => re.test(f)); });
    if (dead.length) (strict ? E : T)(`selection: globs matching no file: ${dead.join(', ')}`);
    for (const r of cfg.selection.rules ?? []) for (const l of r.layers) if (!cfg.layers[l]) E(`selection rule → unknown layer "${l}"`);
    for (const k of ['always', 'fallback']) for (const l of cfg.selection[k] ?? []) if (!cfg.layers[l]) E(`selection.${k} → unknown layer "${l}"`);
  } else T('selection not configured — `task test:changed` falls back to all layers');
  for (const f of cfg.freeze ?? []) if (!existsSync(f.baseline)) E(`freeze "${f.name}": baseline ${f.baseline} missing — run \`node ${VENDOR_DIR}/bin/uts.mjs freeze-check --init\` once`);
  // --- SDD mode
  const sddLayers = names.filter((n) => cfg.layers[n].runner === 'sdd-lint');
  if (!cfg.sdd) for (const n of sddLayers) E(`layer "${n}": runner sdd-lint needs an sdd block in ${CONFIG_FILE}`);
  if (!cfg.sdd) for (const n of names.filter((x) => cfg.layers[x].runner === 'journeys')) E(`layer "${n}": runner journeys needs an sdd block in ${CONFIG_FILE}`);
  for (const [g, gate] of Object.entries(cfg.gates)) if (gate.sddMode && !cfg.sdd) E(`gate "${g}": sddMode needs an sdd block in ${CONFIG_FILE}`);
  if (cfg.sdd) {
    if (!docsRootRel(cfg)) E(`sdd.docsRoot "${cfg.sdd.docsRoot}" must be a relative path inside the repository`);
    else if (!existsSync(cfg.sdd.docsRoot)) E(`sdd.docsRoot "${cfg.sdd.docsRoot}" does not exist`);
    else if (spawnSync(cfg.sdd.python, ['-c', 'import yaml'], { encoding: 'utf8' }).status !== 0) E(`sdd: ${cfg.sdd.python} with PyYAML is required to read the SDD corpus (python3 -m pip install pyyaml)`);
    else {
      const { index, error } = loadIndex(cfg);
      if (error) E(`sdd: index failed: ${error}`);
      else {
        for (const e of index.errors) E(`sdd: ${e.file}: ${e.message}`);
        notes.push(`SDD corpus ${cfg.sdd.docsRoot}: ${index.ears.length} EARS, ${index.bdd.length} BDD, ${index.tdd.length} TDD cases (mode ${cfg.sdd.mode})`);
        if (!index.tdd.length) T(`sdd: no TDD test cases found under ${cfg.sdd.docsRoot} — nothing can bind`);
        const jc = checkJourneys(cfg, index);
        jc.errors.forEach(E); jc.todos.forEach(T);
        if ((index.journeys ?? []).length) notes.push(`journey cards ${cfg.sdd.journeys}: ${index.journeys.length} (${index.journeys.filter((c) => c.draft).length} draft)`);
      }
    }
    if (!existsSync(registryFile(cfg))) (sddLayers.length ? T : (m) => notes.push(m))(`sdd: aidoc-flow framework checkout not found at ${frameworkDir(cfg)} — ${sddLayers.length ? `sdd-lint layers (${sddLayers.join(', ')}) will be infra_error; ` : ''}set SDD_FRAMEWORK or sdd.framework (registry ID grammar falls back to the built-in default)`);
    if (!sddLayers.length) T('sdd: no sdd-lint layer — add { "runner": "sdd-lint" } to a gate so document lint runs with the tests');
  }
  const nKeys = (cfg.secrets.envKeys?.length ?? 0) + (cfg.secrets.keysFile ? 1 : 0);
  if (!nKeys) T('secrets: no envKeys / keysFile — only pattern redaction (tokens, JWTs, PEM, DSN passwords) protects outputs');
  if (cfg.secrets.keysFile && !existsSync(cfg.secrets.keysFile)) E(`secrets.keysFile ${cfg.secrets.keysFile} not found`);

  return report();

  function report() {
    const out = { canonVersion: canonVersion(), errors, todos, notes, ok: errors.length === 0 && (!strict || todos.length === 0) };
    if (json) console.log(JSON.stringify(out, null, 2));
    else {
      for (const n of notes) console.log(`  · ${n}`);
      for (const e of errors) console.log(`  ✖ ${e}`);
      for (const t of todos) console.log(`  ○ TODO ${t}`);
      console.log(`uts check: ${errors.length} error(s), ${todos.length} adoption TODO(s)${strict ? ' [strict: TODOs fail]' : ''} → ${out.ok ? 'OK' : 'FAIL'}`);
    }
    return out.ok ? 0 : 1;
  }
}

// ------------------------------------------------------------------ BDD IDs (static tags only)
export function bddCheck() {
  const cfg = loadConfig();
  const { bdd } = cfg;
  if (!bdd.enabled) { console.log('bdd-check: bdd.enabled is false — skipped'); return 0; }
  const errors = []; const catalog = new Set();
  // SDD mode: the registry element grammar and the SDD index are the catalog (EARS-02 G10).
  const idPattern = cfg.sdd ? bddIdPattern(cfg) : bdd.idPattern;
  const idRe = new RegExp(`^${idPattern}$`);
  const catalogName = cfg.sdd ? 'SDD index' : 'BDD catalog';
  if (cfg.sdd) {
    const { index, error } = loadIndex(cfg);
    if (error) errors.push(`SDD index failed: ${error}`);
    else index.bdd.forEach((b) => catalog.add(b.id));
    if (!error && !catalog.size) errors.push(`no BDD elements in the SDD index (${cfg.sdd.docsRoot})`);
  } else {
    for (const f of findFiles(bdd.catalogGlobs ?? [])) for (const m of readFileSync(f, 'utf8').matchAll(new RegExp(bdd.idPattern, 'g'))) catalog.add(m[0]);
    if (!catalog.size) (bdd.requireCatalog ? errors : []).push('no BDD catalog found');
  }
  const tagged = new Set();
  for (const file of findFiles(bdd.specGlobs ?? [])) {
    readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
      const at = `${file}:${i + 1}`;
      if (/annotations\s*\.\s*push\s*\(\s*\{[^}]*type\s*:\s*['"]bdd['"]/.test(line)) errors.push(`${at}: runtime BDD annotation — use a static tag: test('…', { tag: '@BDD.xx.yy.zzzz' }, …)`);
      if (/['"`@]BDD-\d/.test(line)) errors.push(`${at}: dash-form BDD ID — canonical form matches ${idPattern}`);
      for (const m of line.matchAll(/['"`]@(BDD\.[^'"`\s]+)['"`]/g)) {
        if (!idRe.test(m[1])) errors.push(`${at}: malformed BDD tag @${m[1]}`);
        else if (catalog.size && !catalog.has(m[1])) errors.push(`${at}: @${m[1]} is not in the ${catalogName}`);
        else tagged.add(m[1]);
      }
    });
  }
  if (errors.length) { console.error(`bdd-check: ${errors.length} problem(s)\n  ${errors.join('\n  ')}`); return 1; }
  console.log(`bdd-check: ${tagged.size} distinct BDD IDs tagged${catalog.size ? ` (catalog ${catalog.size})` : ' (no catalog: format-only)'}`);
  return 0;
}

// ------------------------------------------------------------------ freeze ratchets
export function freezeCheck({ mode = 'check' } = {}) {
  const cfg = loadConfig();
  if (!cfg.freeze?.length) { console.log('freeze-check: no frozen suites configured'); return 0; }
  let rc = 0;
  for (const f of cfg.freeze) {
    const re = new RegExp(f.testPattern ?? '^\\s*(?:async\\s+)?def\\s+test_\\w+', 'gm');
    const current = {};
    for (const file of findFiles([f.glob])) current[file] = (readFileSync(file, 'utf8').match(re) ?? []).length;
    if (mode === 'init') {
      if (existsSync(f.baseline)) { console.error(`${f.baseline} exists; use --update`); rc = 2; continue; }
      writeFileSync(f.baseline, JSON.stringify(current, null, 2) + '\n');
      console.log(`freeze "${f.name}": baseline written (${Object.keys(current).length} files)`);
      continue;
    }
    const base = readJson(f.baseline, null);
    if (!base) { console.error(`freeze "${f.name}": ${f.baseline} missing — run freeze-check --init once`); rc = 1; continue; }
    const errors = [];
    for (const [file, n] of Object.entries(current)) {
      if (!(file in base)) errors.push(`new file ${file} in frozen suite "${f.name}"${f.instead ? ` — ${f.instead}` : ''}`);
      else if (n > base[file]) errors.push(`${file}: ${n} tests > baseline ${base[file]}`);
    }
    if (mode === 'update') {
      if (errors.length) { console.error(`freeze "${f.name}": refusing to raise baseline:\n  ${errors.join('\n  ')}`); rc = 1; continue; }
      writeFileSync(f.baseline, JSON.stringify(current, null, 2) + '\n');
      console.log(`freeze "${f.name}": baseline ratcheted down`);
      continue;
    }
    if (errors.length) { console.error(`freeze "${f.name}" grew:\n  ${errors.join('\n  ')}`); rc = 1; }
    else console.log(`freeze "${f.name}": OK (${Object.keys(current).length} files, none grew)`);
  }
  return rc;
}
