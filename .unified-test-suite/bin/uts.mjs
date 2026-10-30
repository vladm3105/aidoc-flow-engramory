#!/usr/bin/env node
// uts — unified-test-suite CLI. Agents and humans normally call it through `task`; see `task --list`.
//
// Project lifecycle
//   uts init --target <dir> [--profile p] [--force]   (from a unified-test-suite checkout) vendor canon + templates
//                                               profiles: minimal | node | python | go | fullstack
//   uts sync --from <path|git-url> [--ref vX] [--force] [--allow-dirty]   update the vendored canon
//   uts gen                                    regenerate .unified-test-suite/generated/Taskfile.yml from config
//   uts check [--strict] [--json] [--upstream <path|git-url>]   integrity + config + adoption TODOs
//   uts version
// Running (called by the canon Taskfile)
//   uts layer <name> [--gate g]                run one layer (records meta; always exits 0)
//   uts plan (--gate g | --layers "a b") [--parallel|--serial]   print layer names
//   uts report [--target t] [--require "a b"] [--since epoch] [--strict] [--allow-empty]   THE gate
//   uts invalidate [--target t]                mark the summary "run incomplete" at the start of a run
//   uts steps <layer> <out.xml>                run a steps layer (used by `uts layer`)
//   uts fingerprint                            working-tree fingerprint used for freshness
//   uts select [--explain]                     layers for the current diff (test:changed)
//   uts layers                                 list layers, groups, gates
//   uts doctor                                 what can run here → test-results/doctor.json
//   uts backend up|down|reset                  stack lifecycle with config-derived env
//   uts setup                                  install toolchain deps derived from configured runners
//   uts bdd-check | freeze-check [--init|--update]
// SDD mode (config `sdd` block; aidoc-flow framework)
//   uts sdd-index                              index the SDD corpus → <results>/sdd/index.json
//   uts sdd-lint --layer <l> --report-dir <d> [--mode build|gate-code]   sdd_doc_lint → JUnit (sdd-lint runner)
//   uts sdd-lint --preflight                   exit 3 unless the framework checkout + PyYAML are present
// Agentic testing (CANON.md §12)
//   uts adapt [--dry-run] [--all]              scaffold .agents/agents (qa-engineer, browser-subagent), TESTING_PROFILE,
//                                              SDD_TEST_INVENTORY and journey cards — never overwriting project files
//   uts journeys plan [--ids a,b] [--env e]    JSON work order for browser subagents
//   uts journeys import --layer l --report-dir d   evidence → JUnit (runner journeys)
//   uts issues [--labels a,b]                  GitHub issue drafts from the last run (the QA engineer files them)
import { spawnSync } from 'node:child_process';
import { loadConfig, canonVersion, CANON_REL, sh } from '../lib/core.mjs';

const [, , cmd, ...rest] = process.argv;
const flag = (n) => rest.includes(n);
const opt = (n, d = null) => { const i = rest.indexOf(n); return i >= 0 && rest[i + 1] !== undefined ? rest[i + 1] : d; };
const words = (s) => (s ?? '').split(/[\s,]+/).filter(Boolean);

async function main() {
  switch (cmd) {
    case 'init': return (await import('../lib/sync.mjs')).init({ target: opt('--target'), force: flag('--force'), profile: opt('--profile', 'fullstack') });
    case 'sync': return (await import('../lib/sync.mjs')).sync({ from: opt('--from') || process.env.UTS_SOURCE, ref: opt('--ref') || null, force: flag('--force'), allowDirty: flag('--allow-dirty') });
    case 'gen': return (await import('../lib/sync.mjs')).gen();
    case 'check': return (await import('../lib/check.mjs')).check({ strict: flag('--strict'), json: flag('--json'), upstream: opt('--upstream') });
    case 'bdd-check': return (await import('../lib/check.mjs')).bddCheck();
    case 'freeze-check': return (await import('../lib/check.mjs')).freezeCheck({ mode: flag('--init') ? 'init' : flag('--update') ? 'update' : 'check' });
    case 'version': console.log(canonVersion()); return 0;
    case 'sdd-index': {
      const cfg = loadConfig();
      if (!cfg.sdd) { console.error('sdd-index: the config has no sdd block (standalone mode)'); return 2; }
      const { loadIndex } = await import('../lib/sdd.mjs');
      const { index, error, path: out } = loadIndex(cfg);
      if (error) { console.error(`sdd-index: ${error}`); return 1; }
      console.log(`sdd-index: ${index.ears.length} EARS, ${index.bdd.length} BDD, ${index.tdd.length} TDD cases, ${index.mappings.length} mappings, ${index.iplans.length} IPLAN, ${index.evals.length} EVAL, ${index.errors.length} error(s) → ${out}`);
      for (const e of index.errors) console.log(`  ✖ ${e.file}: ${e.message}`);
      return index.errors.length ? 1 : 0;
    }
    case 'adapt': return (await import('../lib/journeys.mjs')).adapt({ dryRun: flag('--dry-run'), all: flag('--all') });
    case 'journeys': {
      const j = await import('../lib/journeys.mjs');
      if (rest[0] === 'plan') return j.plan({ ids: words(opt('--ids')), env: opt('--env'), layer: opt('--layer') });
      if (rest[0] === 'import') return j.importEvidence({ layer: opt('--layer'), reportDir: opt('--report-dir') });
      console.error('usage: uts journeys plan [--ids a,b] [--env local|staging] | uts journeys import --layer <l> --report-dir <d>'); return 2;
    }
    case 'issues': return (await import('../lib/issues.mjs')).draftIssues({ labels: words(opt('--labels')) });
    case 'sdd-lint': {
      const m = await import('../lib/sdd.mjs');
      if (flag('--preflight')) return m.sddLintPreflight();
      return m.sddLint({ layer: opt('--layer'), reportDir: opt('--report-dir'), mode: opt('--mode') });
    }
    case 'layer': {
      const name = rest[0] && !rest[0].startsWith('--') ? rest[0] : null;
      if (!name) { console.error('usage: uts layer <name> [--gate g]  (task test:layer LAYER=<name>)'); return 2; }
      return (await import('../lib/run.mjs')).runLayer(name, opt('--gate', 'local'));
    }
    case 'steps': return (await import('../lib/exec.mjs')).runSteps(rest[0], rest[1]);
    case 'fingerprint': console.log((await import('../lib/core.mjs')).fingerprint(process.env.RESULTS_DIR || loadConfig({ required: false }).resultsDir)); return 0;
    case 'invalidate': {
      // Start of a run: replace any previous summary with an explicit "incomplete" one, so an aborted
      // run can never be mistaken for the previous verdict.
      const { writeIncompleteSummary } = await import('../lib/summarize.mjs');
      writeIncompleteSummary(opt('--target', 'report'));
      return 0;
    }
    case 'plan': {
      const { gatePlan } = await import('../lib/run.mjs');
      const cfg = loadConfig();
      let plan;
      if (opt('--gate')) plan = gatePlan(cfg, opt('--gate'));
      else {
        const all = words(opt('--layers'));
        for (const l of all) if (!cfg.layers[l]) throw new Error(`unknown layer "${l}"`);
        plan = { all, parallel: all.filter((l) => !cfg.layers[l].stateful), serial: all.filter((l) => cfg.layers[l].stateful) };
      }
      console.log((flag('--parallel') ? plan.parallel : flag('--serial') ? plan.serial : plan.all).join(' '));
      return 0;
    }
    case 'report': {
      const { summarize, writeCrashSummary } = await import('../lib/summarize.mjs');
      try {
        return summarize({ target: opt('--target', 'report'), require: words(opt('--require')), since: Number(opt('--since')) || null, strict: flag('--strict'), allowEmpty: flag('--allow-empty') });
      } catch (err) { writeCrashSummary(err); console.error(err.stack); return 2; }
    }
    case 'select': { const { selectLayers } = await import('../lib/run.mjs'); console.log(selectLayers({ explain: flag('--explain') }).layers.join(' ')); return 0; }
    case 'layers': {
      const cfg = loadConfig();
      console.log('Layers:');
      for (const [n, L] of Object.entries(cfg.layers)) console.log(`  ${n.padEnd(18)} ${L.runner.padEnd(13)} ${L.stateful ? 'sequential' : 'parallel  '} ${L.needsStack ? 'needs stack' : ''}${L.description ? ` — ${L.description}` : ''}`);
      if (Object.keys(cfg.groups ?? {}).length) { console.log('Groups:'); for (const [n, g] of Object.entries(cfg.groups)) console.log(`  ${n.padEnd(18)} ${g.join(', ')}`); }
      console.log('Gates:');
      for (const [n, g] of Object.entries(cfg.gates)) console.log(`  ${n.padEnd(18)} ${g.layers.join(', ')}${g.strict ? ' [strict]' : ''}${g.budgetSeconds ? ` [budget ${g.budgetSeconds}s]` : ''}`);
      return 0;
    }
    case 'doctor': return (await import('../lib/run.mjs')).doctor();
    case 'backend': {
      const { stackEnv } = await import('../lib/run.mjs');
      const r = spawnSync('bash', [`${CANON_REL}/scripts/backend.sh`, rest[0] ?? 'up'], { stdio: 'inherit', env: { ...process.env, ...stackEnv(loadConfig()) } });
      return r.status ?? 1;
    }
    case 'setup': {
      const cfg = loadConfig();
      const runners = new Set(Object.values(cfg.layers).map((l) => l.runner));
      const cmds = cfg.setup ?? [
        ...(sh('ls package-lock.json') ? ['npm ci'] : sh('ls package.json') ? ['npm install'] : []),
        ...(runners.has('playwright') ? [`npx playwright install ${process.env.CI ? '--with-deps ' : ''}chromium`] : []),
      ];
      for (const c of cmds) {
        console.log(`uts setup: ${c}`);
        const r = spawnSync('bash', ['-o', 'pipefail', '-c', c], { stdio: 'inherit' });
        if (r.status !== 0) return r.status ?? 1;
      }
      return 0;
    }
    default:
      console.error(`uts ${canonVersion()} — unknown command "${cmd ?? ''}". See the header of ${CANON_REL}/bin/uts.mjs or \`task --list\`.`);
      return 2;
  }
}

main().then((code) => process.exit(code ?? 0), (err) => { console.error(`uts: ${err.message}`); process.exit(2); });
