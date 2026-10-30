// canon/lib/journeys.mjs — agentic testing (CANON.md §12, SPEC-03): journey cards, the work order for
// browser subagents, evidence import (the gate for agent runs), card checks and `uts adapt`.
// The canon never drives a model or a browser: agents run in their own runtime and write evidence
// records; this module plans, validates, fresh-checks, redacts and converts them to JUnit.
import { existsSync, mkdirSync, readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { ROOT, CANON_DIR, loadConfig, resultsDir, toPosix, redactDeep, redact, redactDocument, canonVersion, fingerprint as treeFingerprint } from './core.mjs';
import { loadIndex } from './sdd.mjs';

const VERDICTS = ['passed', 'failed', 'blocked', 'inconclusive'];
const CLASSES = ['product', 'test', 'environment'];
const STEP_STATUS = ['passed', 'failed', 'skipped'];
const norm = (f) => (f ? path.posix.normalize(toPosix(String(f))).replace(/^\.\//, '') : '');
const xmlEsc = (s) => String(s ?? '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function requireSdd(cfg, cmd) {
  if (!cfg.sdd) throw new Error(`${cmd}: journeys need SDD mode — add an "sdd" block to unified-test-suite.config.json (CANON.md §11–§12)`);
}
function indexOrThrow(cfg) {
  const { index, error } = loadIndex(cfg);
  if (error) throw new Error(`SDD index failed: ${error}`);
  return index;
}
const evidenceDirOf = (cfg, layer) => cfg.layers[layer]?.evidenceDir ?? path.posix.join(toPosix(resultsDir(cfg)), 'agent-evidence');
/** Evidence inside the worktree but outside resultsDir would change the tree fingerprint and make every record stale. */
export function evidenceDirProblem(cfg, layer) {
  const dir = path.resolve(ROOT, evidenceDirOf(cfg, layer));
  const inTree = !path.relative(ROOT, dir).startsWith('..') && !path.isAbsolute(path.relative(ROOT, dir));
  const underResults = !path.relative(path.resolve(ROOT, resultsDir(cfg)), dir).startsWith('..');
  return inTree && !underResults ? `evidenceDir ${evidenceDirOf(cfg, layer)} is inside the repo but outside resultsDir — evidence files would change the tree fingerprint; keep it under ${toPosix(resultsDir(cfg))}/ (default ${toPosix(resultsDir(cfg))}/agent-evidence)` : null;
}
const PLAN_FILE = '_plan.json';
const TEXT_ARTIFACT = /\.(html?|txt|md|log|xml|ya?ml|har|csv|json)$/i;

// ------------------------------------------------------------------ checks (uts check)
/** Card findings for `uts check`: { errors[], todos[] }. */
export function checkJourneys(cfg, index) {
  const errors = []; const todos = [];
  const bdd = new Map(index.bdd.map((b) => [b.id, b]));
  const ears = new Set(index.ears.map((e) => e.id));
  const tdd = new Map(index.tdd.map((c) => [c.id, c]));
  const draftFiles = new Set((index.journeys ?? []).filter((c) => c.draft).map((c) => c.file));
  for (const e of index.journeyErrors ?? []) (draftFiles.has(e.file) ? todos : errors).push(`journey ${e.file}: ${e.message}`);
  for (const c of index.journeys ?? []) {
    if (c.draft) { todos.push(`journey ${c.journey ?? c.file}: draft — finish the card and remove "draft: true" (JOURNEYS.md §3)`); continue; }
    if (!c.valid || !c.journey) continue;
    const b = bdd.get(c.journey);
    if (!b) errors.push(`journey ${c.file}: ${c.journey} is not a BDD element in ${cfg.sdd.docsRoot}`);
    for (const e of c.ears) if (!ears.has(e.id)) errors.push(`journey ${c.file}: ${e.id} is not an EARS element in ${cfg.sdd.docsRoot}`);
    if (!c.tdd) { todos.push(`journey ${c.journey}: no TDD case — add an e2e case with test_file ${c.file} and test_function ${c.journey}`); continue; }
    const t = tdd.get(c.tdd);
    if (!t) { errors.push(`journey ${c.file}: TDD case ${c.tdd} is not in ${cfg.sdd.docsRoot}`); continue; }
    if (norm(t.testFile) !== norm(c.file) || String(t.testFunction ?? '') !== c.journey) errors.push(`journey ${c.file}: TDD case ${c.tdd} must point back at the card (test_file ${c.file}, test_function ${c.journey}); it names ${t.testFile ?? '—'} / ${t.testFunction ?? '—'}`);
    else if (!t.bdd.includes(c.journey)) errors.push(`journey ${c.file}: TDD case ${c.tdd} must pair the scenario (bdd_ref ${c.journey})`);
  }
  const jl = Object.entries(cfg.layers).filter(([, L]) => L.runner === 'journeys').map(([n]) => n);
  for (const l of jl) { const p = evidenceDirProblem(cfg, l); if (p) errors.push(`layer ${l}: ${p}`); }
  if (jl.length && treeFingerprint(resultsDir(cfg)) === 'nogit') todos.push('journeys layer outside a git checkout: the tree fingerprint is "nogit", so evidence cannot go stale when code changes — run journeys from a git checkout');
  const inPr = jl.filter((l) => (cfg.gates.pr?.layers ?? []).includes(l));
  if (inPr.length) todos.push(`journeys layer(s) ${inPr.join(', ')} in the "pr" gate — agent evidence is staging evidence; move them to a functional/staging gate (CANON.md §12.5)`);
  return { errors, todos };
}

// ------------------------------------------------------------------ plan (work order)
export async function plan({ ids = [], env = null, layer = null } = {}) {
  const cfg = loadConfig();
  requireSdd(cfg, 'journeys plan');
  const index = indexOrThrow(cfg);
  const { stackEnv } = await import('./run.mjs');
  const senv = stackEnv(cfg);
  const jl = layer ?? Object.entries(cfg.layers).find(([, L]) => L.runner === 'journeys')?.[0] ?? null;
  const evidenceDir = evidenceDirOf(cfg, jl);
  const runId = process.env.TEST_RUN_ID || `j${Date.now().toString(36)}`;
  const fp = treeFingerprint(resultsDir(cfg));
  const appUrl = senv.APP_URL ?? null; const backendUrl = senv.BACKEND_URL ?? null;
  const skipped = [];
  const items = [];
  for (const c of index.journeys ?? []) {
    if (ids.length && !ids.includes(c.journey)) continue;
    if (c.draft) { skipped.push({ file: c.file, reason: 'draft' }); continue; }
    if (!c.valid) { skipped.push({ file: c.file, reason: 'invalid card (uts check lists why)' }); continue; }
    if (env && c.environments.length && !c.environments.includes(env)) { skipped.push({ file: c.file, reason: `not allowed on ${env}` }); continue; }
    // Each item is self-contained: a browser subagent given only its item can write a fresh record.
    items.push({
      journey: c.journey, title: c.title, file: c.file, sha256: c.sha256, tdd: c.tdd, ears: c.ears,
      identitySlots: [...new Set(c.setup.map((s) => s.identity).filter((x) => x != null))],
      environments: c.environments, external: c.external,
      environment: env, appUrl, backendUrl, identityPoolEnv: cfg.e2e.identityPoolEnv,
      evidenceFile: path.posix.join(evidenceDir, runId, `${c.journey}.json`),
      record: { schema: 'uts.agent-evidence/1', journey: c.journey, journeyFile: c.file, journeySha256: c.sha256, fingerprint: fp, runId },
    });
  }
  for (const id of ids) if (!(index.journeys ?? []).some((c) => c.journey === id)) skipped.push({ file: null, journey: id, reason: 'no card' });
  const order = {
    schema: 'uts.journey-plan/1', canonVersion: canonVersion(), generatedAt: new Date().toISOString(),
    fingerprint: fp, runId, evidenceDir, environment: env,
    appUrl, backendUrl, backendManaged: process.env.BACKEND_MANAGED ?? null,
    identityPoolEnv: cfg.e2e.identityPoolEnv, identitiesPerWorker: cfg.e2e.identitiesPerWorker ?? 1,
    protocol: '.unified-test-suite/agents/JOURNEYS.md', journeys: items, skipped,
  };
  // Persist the plan next to the evidence: import requires every card it planned (JOURNEYS.md §6).
  const planDir = path.resolve(ROOT, evidenceDir, runId);
  mkdirSync(planDir, { recursive: true });
  writeFileSync(path.join(planDir, PLAN_FILE), JSON.stringify(order, null, 2) + '\n');
  console.log(JSON.stringify(order, null, 2));
  return 0;
}

// ------------------------------------------------------------------ import (runner journeys)
function walkFiles(dir) {
  const out = [];
  if (!existsSync(dir)) return out;
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...walkFiles(p));
    else if (e.isFile()) out.push(p);
  }
  return out.sort();
}
/** Redact a text file in place (agent-written evidence may quote secrets it saw). */
function redactFile(f) {
  const raw = readFileSync(f, 'utf8');
  const clean = redactDocument(raw, path.basename(f));
  if (clean !== raw) writeFileSync(f, clean);
}
function evidenceProblems(r) {
  const p = [];
  if (!r || typeof r !== 'object' || Array.isArray(r)) return ['not a JSON object'];
  if (r.schema !== 'uts.agent-evidence/1') p.push('schema must be uts.agent-evidence/1');
  for (const k of ['journey', 'journeyFile', 'fingerprint', 'startedAt', 'finishedAt']) if (typeof r[k] !== 'string' || !r[k]) p.push(`${k} is required`);
  if (typeof r.journeySha256 !== 'string' || !/^[0-9a-f]{64}$/.test(r.journeySha256)) p.push('journeySha256 must be a sha256 hex digest');
  if (!VERDICTS.includes(r.verdict)) p.push(`verdict must be one of ${VERDICTS.join(', ')}`);
  if (r.classification != null && !CLASSES.includes(r.classification)) p.push(`classification must be one of ${CLASSES.join(', ')} or null`);
  if (!r.agent || typeof r.agent.runtime !== 'string') p.push('agent.runtime is required');
  if (!Array.isArray(r.steps)) p.push('steps must be a list');
  else r.steps.forEach((s, i) => { if (!s || typeof s.id !== 'string' || !STEP_STATUS.includes(s.status)) p.push(`steps[${i}] needs id and status (${STEP_STATUS.join('|')})`); });
  if (r.finishedAt && Number.isNaN(Date.parse(r.finishedAt))) p.push('finishedAt must be an ISO date-time');
  return p;
}

export function importEvidence({ layer, reportDir } = {}) {
  const cfg = loadConfig();
  requireSdd(cfg, 'journeys import');
  const index = indexOrThrow(cfg);
  const R = resultsDir(cfg);
  const dir = path.resolve(ROOT, evidenceDirOf(cfg, layer));
  reportDir = reportDir ?? path.join(R, 'reports', layer ?? 'journeys');
  mkdirSync(reportDir, { recursive: true });
  const problem = evidenceDirProblem(cfg, layer);
  if (problem) throw new Error(problem);
  const fp = treeFingerprint(R);
  const cards = new Map((index.journeys ?? []).filter((c) => c.valid && !c.draft).map((c) => [norm(c.file), c]));
  const ignored = []; const fresh = new Map(); const plans = [];
  for (const f of walkFiles(dir)) {
    const rel = toPosix(path.relative(ROOT, f));
    // Redact in place first, every text file (records, DOM snapshots, logs): evidence is agent-written.
    if (TEXT_ARTIFACT.test(f)) redactFile(f);
    if (!f.endsWith('.json')) continue;
    let rec;
    try { rec = JSON.parse(readFileSync(f, 'utf8')); } catch (e) { ignored.push({ record: rel, reason: `unparseable JSON: ${e.message}` }); continue; }
    if (path.basename(f) === PLAN_FILE) { if (rec?.schema === 'uts.journey-plan/1' && rec.fingerprint === fp) plans.push(rec); continue; }
    // A record is <BDD-ID>.json or declares the evidence schema; other JSON files are artifacts (already redacted).
    if (rec?.schema !== 'uts.agent-evidence/1' && !/^BDD\..+\.json$/.test(path.basename(f))) continue;
    const probs = evidenceProblems(rec);
    if (probs.length) { ignored.push({ record: rel, reason: `invalid: ${probs.join('; ')}` }); continue; }
    const card = cards.get(norm(rec.journeyFile));
    if (!card) { ignored.push({ record: rel, reason: `${rec.journeyFile} is not a finished journey card` }); continue; }
    if (card.journey !== rec.journey) { ignored.push({ record: rel, reason: `journey ${rec.journey} does not match card ${card.journey}` }); continue; }
    if (card.sha256 !== rec.journeySha256) { ignored.push({ record: rel, reason: 'card changed since the plan (sha256 differs) — re-plan and re-run' }); continue; }
    if (rec.fingerprint !== fp) { ignored.push({ record: rel, reason: `stale: recorded at tree ${rec.fingerprint}, tree is now ${fp} — re-plan and re-run` }); continue; }
    const prev = fresh.get(card.journey);
    if (!prev || Date.parse(rec.finishedAt) > Date.parse(prev.rec.finishedAt)) {
      if (prev) ignored.push({ record: prev.rel, reason: `superseded by newer ${rel}` });
      fresh.set(card.journey, { rec, rel, card });
    } else ignored.push({ record: rel, reason: `superseded by newer ${prev.rel}` });
  }

  const cases = []; const used = []; let failures = 0; let blocked = 0;
  for (const { rec, rel, card } of [...fresh.values()].sort((a, b) => a.card.journey.localeCompare(b.card.journey))) {
    let verdict = rec.verdict; let note = '';
    let cls = rec.classification ?? null;
    // A pass must carry a passed step for every setup and act id and an observation for every expect
    // oracle of the card, and must not break an enforced EARS time bound (JOURNEYS.md §2, §5).
    if (verdict === 'passed') {
      const byId = new Map(rec.steps.map((s) => [s.id, s]));
      const notPassed = [...card.setup.map((x) => x.id), ...card.act].filter((id) => byId.get(id)?.status !== 'passed');
      const unobserved = card.expect.filter((id) => byId.get(id)?.status !== 'passed' || !String(byId.get(id)?.observed ?? '').trim());
      const timings = Array.isArray(rec.timings) ? rec.timings : [];
      const enforced = card.ears.filter((e) => e.within?.enforce && !e.coveredBy);
      const broken = enforced.filter((e) => timings.some((t) => t?.ears === e.id && t.ok === false)).map((e) => e.id);
      const untimed = enforced.filter((e) => !timings.some((t) => t?.ears === e.id)).map((e) => e.id);
      if (broken.length) { verdict = 'failed'; cls = 'product'; note = `enforced time bound exceeded for: ${broken.join(', ')}`; }
      else if (notPassed.length || unobserved.length || untimed.length) {
        verdict = 'inconclusive'; cls = 'test';
        note = `passed verdict without ${[notPassed.length ? `a passed setup/act step for: ${notPassed.join(', ')}` : '', unobserved.length ? `a passed observation for: ${unobserved.join(', ')}` : '', untimed.length ? `timings for enforced bound(s): ${untimed.join(', ')}` : ''].filter(Boolean).join('; ')}`;
      }
    }
    const env = cls === 'environment';
    const secs = Math.max(0, (Date.parse(rec.finishedAt) - Date.parse(rec.startedAt)) / 1000 || 0);
    const detail = [
      `${verdict}${cls ? ` (${cls})` : ''} — ${rel}`,
      note,
      ...rec.steps.filter((s) => s.status !== 'passed').map((s) => `${s.id}: ${s.status}${s.observed ? ` — observed: ${String(s.observed).slice(0, 300)}` : ''}`),
      rec.notes ? `notes: ${String(rec.notes).slice(0, 500)}` : '',
    ].filter(Boolean).join('\n');
    const attrs = `classname="journey" name="${xmlEsc(card.journey)}" file="${xmlEsc(card.file)}" line="1" time="${secs.toFixed(3)}"`;
    let body = '';
    if (verdict === 'blocked' || (env && verdict !== 'passed')) { blocked++; body = `<skipped message="${xmlEsc(`blocked: ${detail.split('\n')[0]}`)}"/>`; }
    else if (verdict !== 'passed') { failures++; body = `<failure message="${xmlEsc(`${verdict}${cls ? ` (${cls})` : ''}: ${card.title}`)}">${xmlEsc(`${card.file}:1: ${detail}`)}</failure>`; }
    cases.push(`  <testcase ${attrs}>${body}</testcase>`);
    used.push({ journey: card.journey, file: card.file, tdd: card.tdd, record: rel, verdict, classification: cls, agent: rec.agent });
  }
  // Every finished card allowed in the planned environment needs fresh evidence; none drops out silently.
  const envs = [...new Set(plans.map((p) => p.environment ?? null))];
  const allowed = (c) => !c.environments.length || envs.length === 0 || envs.includes(null) || envs.some((e) => c.environments.includes(e));
  const missing = [...cards.values()].filter((c) => c.journey && !fresh.has(c.journey) && allowed(c)).sort((a, b) => a.journey.localeCompare(b.journey));
  if (fresh.size || plans.length) for (const c of missing) {
    blocked++;
    cases.push(`  <testcase classname="journey" name="${xmlEsc(c.journey)}" file="${xmlEsc(c.file)}" line="1" time="0"><skipped message="${xmlEsc('not verified: no fresh evidence for this tree (not run, stale or ignored record)')}"/></testcase>`);
  }
  writeFileSync(path.join(reportDir, 'junit.xml'), `<?xml version="1.0" encoding="UTF-8"?>\n<testsuites><testsuite name="journeys" tests="${cases.length}" failures="${failures}" skipped="${blocked}">\n${cases.join('\n')}\n</testsuite></testsuites>\n`);
  const miss = fresh.size || plans.length ? missing.map((c) => ({ journey: c.journey, file: c.file, tdd: c.tdd })) : [];
  writeFileSync(path.join(reportDir, 'journeys.json'), JSON.stringify(redactDeep({ evidenceDir: toPosix(path.relative(ROOT, dir)), fingerprint: fp, environments: envs, used, missing: miss, ignored }), null, 2) + '\n');
  for (const i of ignored) console.log(redact(`ignored ${i.record}: ${i.reason}`));
  const blockedRuns = blocked - miss.length;
  console.log(`journeys: ${used.length} fresh journey(s) — ${used.length - failures - blockedRuns} passed, ${failures} failed, ${blockedRuns} blocked; ${miss.length} card(s) without fresh evidence; ${ignored.length} record(s) ignored`);
  if (!used.length) console.log('journeys: no fresh evidence — run the QA engineer (uts journeys plan → browser-subagent) on this tree, then re-import');
  // No failure but something unverified (blocked runs or cards without fresh evidence) → infra_error, never a pass.
  if (!failures && blocked) console.log(`journeys: not verified — ${blockedRuns} blocked${miss.length ? `, no fresh evidence for ${miss.map((m) => m.journey).join(', ')}` : ''}`);
  return !failures && blocked ? 3 : 0;
}

// ------------------------------------------------------------------ adapt
const tpl = (name) => readFileSync(path.join(CANON_DIR, 'agents', 'templates', name), 'utf8');
const fill = (text, vars) => text.replace(/\{\{([A-Z_]+)\}\}/g, (m, k) => (k in vars ? vars[k] : m));
const q = (s) => JSON.stringify(String(s ?? ''));

function listDocs(dir) {
  const out = [];
  const walk = (d) => {
    if (!existsSync(d)) return;
    for (const e of readdirSync(d, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const p = path.join(d, e.name);
      if (e.isDirectory() && e.name !== 'archive') walk(p);
      else if (e.name.endsWith('.md')) out.push(p);
    }
  };
  walk(path.resolve(ROOT, dir));
  return out.map((p) => {
    const m = readFileSync(p, 'utf8').match(/document_control\s*:\s*\n(?:.*\n){0,12}?\s*version\s*:\s*["']?([0-9][^"'\s]*)/) ?? readFileSync(p, 'utf8').match(/\|\s*Version\s*\|\s*([^|]+?)\s*\|/);
    return `\`${toPosix(path.relative(ROOT, p))}\`${m ? ` v${m[1]}` : ''}`;
  });
}

export function inventory(cfg, index) {
  const cardsBy = new Map((index.journeys ?? []).map((c) => [c.journey, c]));
  const scen = index.bdd.filter((b) => b.kind !== 'feature');
  const pairs = (id) => [...index.tdd.filter((c) => c.bdd.includes(id)).map((c) => `${c.id} (${c.type ?? '?'})`), ...index.mappings.filter((m) => m.bdd.includes(id)).map((m) => `mapping → ${m.testFile}`)];
  const cardCell = (id) => { const c = cardsBy.get(id); return !c ? '—' : c.draft ? `draft \`${c.file}\`` : c.valid ? `\`${c.file}\`` : `invalid \`${c.file}\``; };
  const byPattern = {};
  for (const e of index.ears) (byPattern[e.pattern ?? 'unknown'] ??= []).push(e);
  const cut = (s, n = 140) => (s && s.length > n ? `${s.slice(0, n - 1)}…` : s ?? '');
  const cell = (s) => String(s ?? '').replace(/\|/g, '\\|').replace(/\n/g, ' ');
  const md = [
    '# SDD test inventory', '',
    `Generated by \`uts adapt\` from \`${cfg.sdd.docsRoot}\` and \`${cfg.sdd.journeys}\`. Do not edit — re-run \`node .unified-test-suite/bin/uts.mjs adapt\`. Project decisions go in \`docs/testing/TESTING_PROFILE.md\`.`, '',
    `**Totals:** ${index.ears.length} EARS · ${scen.length} BDD scenarios · ${index.tdd.length} TDD cases · ${index.mappings.length} test mappings · ${(index.journeys ?? []).length} journey cards (${(index.journeys ?? []).filter((c) => c.draft).length} draft)`, '',
    '## EARS requirements by pattern (journey state model, JOURNEYS.md §2)', '',
  ];
  for (const pat of Object.keys(byPattern).sort()) {
    md.push(`### ${pat}`, '', '| EARS | Title | Statement | Scenarios |', '|---|---|---|---|');
    for (const e of byPattern[pat]) md.push(`| ${e.id} | ${cell(e.title)} | ${cell(cut(e.statement))} | ${index.bdd.filter((b) => b.ears.includes(e.id)).map((b) => b.id).join(', ') || '—'} |`);
    md.push('');
  }
  md.push('## BDD scenarios', '', '| Scenario | Title | EARS | Paired TDD / mappings | Journey card |', '|---|---|---|---|---|');
  for (const b of scen) md.push(`| ${b.id} | ${cell(b.title)} | ${b.ears.join(', ') || '—'} | ${cell(pairs(b.id).join('; ') || '—')} | ${cardCell(b.id)} |`);
  md.push('', '## TDD cases', '', '| Case | Type | test_file | test_function | BDD |', '|---|---|---|---|---|');
  for (const c of [...index.tdd].sort((a, b) => String(a.type).localeCompare(String(b.type)) || a.id.localeCompare(b.id))) md.push(`| ${c.id} | ${c.type ?? '—'} | ${cell(c.testFile ?? '—')} | ${cell(c.testFunction ?? '—')} | ${c.bdd.join(', ') || '—'} |`);
  const gaps = [
    ...scen.filter((b) => !pairs(b.id).length).map((b) => `${b.id} has no TDD case or mapping (unpaired)`),
    ...index.ears.filter((e) => !index.bdd.some((b) => b.ears.includes(e.id)) && !index.tdd.some((c) => c.ears.includes(e.id))).map((e) => `${e.id} is cited by no scenario or case (uncovered)`),
    ...scen.filter((b) => index.tdd.some((c) => c.bdd.includes(b.id) && c.type === 'e2e') && !cardsBy.has(b.id)).map((b) => `${b.id} has an e2e case but no journey card`),
    ...(index.journeys ?? []).filter((c) => c.draft).map((c) => `${c.journey} journey card is a draft`),
  ];
  md.push('', '## Gaps', '', ...(gaps.length ? gaps.map((g) => `- ${g}`) : ['- none']), '');
  return { text: md.join('\n'), totals: `${index.ears.length} EARS, ${scen.length} scenarios, ${index.tdd.length} TDD cases, ${(index.journeys ?? []).length} cards` };
}

function skeleton(cfg, index, b) {
  const file = path.posix.join(toPosix(cfg.sdd.journeys), `${b.id}.journey.yaml`);
  const tddCase = index.tdd.find((c) => norm(c.testFile) === norm(file) && String(c.testFunction) === b.id);
  const earsBlock = b.ears.length ? b.ears.map((id) => {
    const e = index.ears.find((x) => x.id === id);
    const pat = e?.pattern ?? 'event';
    const parts = [`id: ${id}`, `pattern: ${pat}`, ...(pat === 'state' || pat === 'event' ? ['while: "<state>"'] : []), ...(pat === 'event' ? ['when: "<trigger>"'] : []), 'then: "<expect id>"'];
    return `  - { ${parts.join(', ')} }${e?.statement ? `   # ${e.statement.slice(0, 160).replace(/[\r\n]+/g, ' ')}` : ''}`;
  }).join('\n') : '  []  # the scenario cites no EARS requirement — add one in the BDD document (CHG) before finishing';
  return {
    file,
    text: fill(tpl('journey.yaml'), {
      BDD_DOC: b.file, BDD_ID: b.id, BDD_TITLE: q(b.title), TDD_ID: tddCase ? tddCase.id : 'null   # add an e2e TDD case: test_file = this card, test_function = the journey id',
      BDD_STEPS: (b.steps?.length ? b.steps : ['(no Given/When/Then text found in the BDD document)']).flatMap((s) => String(s).split('\n')).map((l) => `#   ${l}`).join('\n'),
      EARS_BLOCK: earsBlock,
    }),
  };
}

export function adapt({ dryRun = false, all = false } = {}) {
  const cfg = loadConfig();
  requireSdd(cfg, 'adapt');
  const index = indexOrThrow(cfg);
  const project = cfg.project ?? path.basename(ROOT);
  const date = new Date().toISOString().slice(0, 10);
  const created = []; const kept = [];
  const put = (rel, text, { always = false } = {}) => {
    const abs = path.resolve(ROOT, rel);
    if (existsSync(abs) && !always) { kept.push(rel); return; }
    if (existsSync(abs) && readFileSync(abs, 'utf8') === text) { kept.push(rel); return; }
    if (!dryRun) { mkdirSync(path.dirname(abs), { recursive: true }); writeFileSync(abs, text); }
    created.push(rel);
  };
  // Journey skeletons first, so the inventory and the profile describe the tree adapt leaves behind.
  const cardsBy = new Set((index.journeys ?? []).map((c) => c.journey));
  for (const b of index.bdd.filter((x) => x.kind !== 'feature')) {
    if (cardsBy.has(b.id)) continue;
    const hasE2e = index.tdd.some((c) => c.bdd.includes(b.id) && c.type === 'e2e');
    if (!all && !hasE2e) continue;
    const sk = skeleton(cfg, index, b);
    put(sk.file, sk.text);
  }
  const after = dryRun ? index : indexOrThrow(cfg);
  const inv = inventory(cfg, after);
  const vars = {
    PROJECT: project, CANON_VERSION: canonVersion(), DATE: date, AUTHOR: 'uts adapt (draft) — complete with the QA engineer',
    SEED_FILES: listDocs('docs/seed').join(', ') || 'none found in docs/seed', MODULE_FILES: listDocs('docs/modules').join(', ') || 'none found in docs/modules', INVENTORY_TOTALS: inv.totals,
  };
  put('.agents/agents/qa-engineer.md', fill(tpl('qa-engineer.md'), vars));
  put('.agents/agents/browser-subagent.md', fill(tpl('browser-subagent.md'), vars));
  put('docs/testing/TESTING_PROFILE.md', fill(tpl('TESTING_PROFILE.md'), vars));
  put('docs/testing/SDD_TEST_INVENTORY.md', inv.text, { always: true });
  for (const c of created) console.log(`${dryRun ? 'would create' : 'created'} ${c}`);
  for (const k of kept) console.log(`kept ${k}`);
  console.log(`uts adapt: ${created.length} ${dryRun ? 'to create' : 'created'}, ${kept.length} kept. Next: have the QA engineer (.agents/agents/qa-engineer.md) complete docs/testing/TESTING_PROFILE.md and finish the draft journey cards (drafts only — finished cards change through an issue); then \`task uts:check\`.`);
  return 0;
}
