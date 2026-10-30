// canon/lib/issues.mjs — `uts issues`: turn the fresh results of a run into GitHub issue drafts
// (CANON.md §12.7, EARS-03 "Issue drafts"). The testing side discovers and files; it never fixes.
// The canon never calls GitHub: the QA engineer files the drafts with `gh`, deduplicating by uts-key.
import { existsSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { loadConfig, resultsDir, readJson, redact, toPosix } from './core.mjs';

const key = (...parts) => createHash('sha1').update(parts.map((p) => String(p ?? '')).join('|')).digest('hex').slice(0, 12);
const oneLine = (s, n = 90) => { const t = String(s ?? '').split('\n').find((l) => l.trim()) ?? ''; return t.length > n ? `${t.slice(0, n - 1)}…` : t; };
const fence = (s) => ['```text', String(s ?? '').split('\n').slice(0, 40).join('\n'), '```'].join('\n');

export function draftIssues({ labels = [] } = {}) {
  const cfg = loadConfig();
  const R = resultsDir(cfg);
  const s = readJson(path.join(R, 'summary.json'), null);
  if (!s || !s.layers) { console.error('uts issues: no complete summary.json — run a test task first'); return 2; }
  if (s.verdict?.status === 'error') { console.error(`uts issues: the last run did not finish (${s.verdict.reasons?.[0] ?? 'error'}) — re-run before filing`); return 2; }
  const sdd = s.sdd ?? null;
  const cases = sdd?.tdd?.cases ?? [];
  const scenarios = sdd?.bdd?.scenarios ?? [];
  const reqs = sdd?.ears?.requirements ?? [];
  const trace = (pred) => {
    const tdd = cases.filter((c) => (c.tests ?? []).some(pred)).map((c) => c.id);
    const bdd = scenarios.filter((b) => (b.pairedWith ?? []).some((p) => tdd.includes(p))).map((b) => b.id);
    const ears = reqs.filter((e) => (e.cases ?? []).some((c) => tdd.includes(c)) || (e.scenarios ?? []).some((b) => bdd.includes(b))).map((e) => e.id);
    return { tdd, bdd, ears };
  };
  const journeysOf = (layer) => readJson(path.join(s.layers[layer]?.artifacts?.reportDir ?? path.join(R, 'reports', layer), 'journeys.json'), { used: [] }).used ?? [];
  const run = [
    `- Tree: \`${s.git?.sha ?? 'nogit'}\`${s.git?.dirty ? ' (dirty)' : ''} · fingerprint \`${s.git?.fingerprint ?? '—'}\` · branch \`${s.git?.branch ?? '—'}\``,
    `- Target: \`${s.target}\` · canon ${s.canonVersion} · backend ${s.env?.backendUrl ?? '—'} (managed ${s.env?.backendManaged ?? '—'}) · CI ${Boolean(s.env?.ci)}`,
  ];
  const drafts = [];
  const add = (d) => { if (!drafts.some((x) => x.key === d.key)) drafts.push(d); };
  const ids = (t) => [t.ears.length ? `- EARS: ${t.ears.join(', ')}` : null, t.bdd.length ? `- BDD: ${t.bdd.join(', ')}` : null, t.tdd.length ? `- TDD: ${t.tdd.join(', ')}` : null].filter(Boolean);

  for (const f of s.failures ?? []) {
    const L = s.layers[f.layer];
    const t = trace((x) => x.layer === f.layer && x.title === f.title);
    let label = f.kind === 'doc' ? 'sdd-drift' : 'bug'; let head = `[${f.layer}] ${f.title} fails`; let extra = [];
    if (f.kind === 'journey') {
      const u = journeysOf(f.layer).find((j) => j.journey === f.title);
      label = u?.classification === 'test' || u?.verdict === 'inconclusive' ? 'test-gap' : 'bug';
      head = `[journey] ${f.title} ${u?.verdict ?? 'failed'}${u?.classification ? ` (${u.classification})` : ''}`;
      extra = [`- Evidence record: \`${u?.record ?? '—'}\` (agent ${u?.agent?.runtime ?? '?'} / ${u?.agent?.tool ?? '?'})`, `- Card: \`${f.file}\``];
      if (!t.bdd.includes(f.title)) t.bdd.unshift(f.title);
    }
    // Stable identity for dedupe: a document finding is its code + file + message, never its line
    // (edits above it move the line); everything else is layer + file + test title.
    const ident = f.kind === 'doc' ? [String(f.title).split(' ')[0], f.file, String(f.message ?? '').replace(/^[^\n]*?:\d+:\s*/, '').split('\n')[0]] : [f.file, f.title];
    add({
      key: key('failure', f.layer, ...ident), kind: f.kind ?? 'test', layer: f.layer, labels: [label, ...labels],
      title: `${head}${f.file && f.kind !== 'journey' ? ` — ${f.file}${f.line ? `:${f.line}` : ''}` : ''}`,
      body: [
        `**${label === 'test-gap' ? 'Gap in the test basis' : label === 'sdd-drift' ? 'SDD document defect' : 'Defect'}** found by the QA engineer. Layer \`${f.layer}\` (${L?.runner ?? '?'}), kind \`${f.kind ?? 'test'}\`.`, '',
        '### Traceability', ...(ids(t).length ? ids(t) : ['- no SDD binding (standalone or unmapped test)']), '',
        '### Evidence', `- Location: \`${f.file ?? '—'}${f.line ? `:${f.line}` : ''}\``, `- Reproduce: \`${f.reproduce ?? `task test:${f.layer}`}\``, ...extra, ...(f.artifacts?.length ? [`- Artifacts: ${f.artifacts.map((a) => `\`${a}\``).join(', ')}`] : []), '',
        '### Observed', fence(f.message), '', '### Run', ...run,
      ],
    });
  }
  for (const [name, L] of Object.entries(s.layers)) {
    if (L.status !== 'infra_error' || !(L.required || L.fresh)) continue;
    // A journeys layer is infra_error because of blocked runs (drafted per journey below) or missing
    // evidence (an incomplete QA run, not a defect); only a preflight failure is a layer-level issue.
    if (L.runner === 'journeys' && L.phase !== 'preflight') continue;
    add({
      key: key('environment', name, L.phase), kind: 'environment', layer: name, labels: ['environment', ...labels],
      title: `[env] ${name} cannot run: ${oneLine(L.reason, 70)}`,
      body: [`**Environment problem** — layer \`${name}\` is \`infra_error\` (phase \`${L.phase ?? '—'}\`, exit ${L.exitCode ?? '—'}). Not a product defect; tests were not judged.`, '', '### Reason', fence(L.reason), '', `Check: \`task test:doctor\`, then \`task test:${name}\`.`, '', '### Run', ...run],
    });
  }
  for (const [name, L] of Object.entries(s.layers)) {
    if (L.runner !== 'journeys' || !L.fresh) continue;
    for (const u of journeysOf(name).filter((j) => j.verdict === 'blocked' || (j.classification === 'environment' && j.verdict !== 'passed'))) {
      add({
        key: key('environment', name, u.journey), kind: 'environment', layer: name, labels: ['environment', ...labels],
        title: `[env] journey ${u.journey} blocked`,
        body: [`**Environment problem** — the browser subagent could not reach the journey's setup state.`, '', `- Card: \`${u.file}\` · TDD: ${u.tdd ?? '—'}`, `- Evidence record: \`${u.record}\``, '', '### Run', ...run],
      });
    }
  }
  for (const f of sdd?.findings ?? []) {
    add({
      key: key('sdd', f.code, f.ref), kind: 'sdd', layer: null, labels: ['sdd-drift', ...labels],
      title: `[sdd] ${f.code} ${f.ref}: ${oneLine(f.message, 80)}`,
      body: [`**SDD drift** — \`${f.code}\` (implements ${f.implements ?? '—'}), severity \`${f.severity}\` in mode \`${sdd.mode}\`.`, '', `- Reference: \`${f.ref}\``, `- Document: \`${f.file ?? '—'}\``, '', '### Finding', fence(f.message), '', 'Fix through the owning IPLAN/CHG: rename the TDD case before the test, or correct the reference.', '', '### Run', ...run],
    });
  }

  const dir = path.join(R, 'issues');
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  const index = [];
  for (const d of drafts.sort((a, b) => a.key.localeCompare(b.key))) {
    const body = redact([...d.body, '', '---', `uts-key: ${d.key}`, 'Filed by the QA engineer (`uts issues`). The QA side discovers and files; fixes come from the coding agent under an IPLAN.', ''].join('\n'));
    writeFileSync(path.join(dir, `${d.key}.md`), body);
    index.push({ key: d.key, title: redact(d.title), labels: [...new Set(d.labels)], kind: d.kind, layer: d.layer, bodyFile: toPosix(path.join(dir, `${d.key}.md`)) });
  }
  writeFileSync(path.join(dir, 'issues.json'), JSON.stringify({ generatedFrom: toPosix(path.join(R, 'summary.json')), target: s.target, fingerprint: s.git?.fingerprint ?? null, drafts: index }, null, 2) + '\n');
  for (const i of index) console.log(`${i.key}  [${i.labels.join(',')}]  ${i.title}`);
  console.log(`uts issues: ${index.length} draft(s) → ${toPosix(dir)}/ — for each: gh issue list --state all --search "<uts-key> in:body", then create, comment or reopen (never fix).`);
  return 0;
}
