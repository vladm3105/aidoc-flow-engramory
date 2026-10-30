// canon/lib/core.mjs — shared runtime for the unified-test-suite canon (zero dependencies).
// Vendored into consumer repos as .unified-test-suite/lib/core.mjs. Do not edit a vendored copy:
// changes belong upstream (uts check rejects local modifications).
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { execSync, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = process.cwd();
/** Directory holding this canon copy (…/.unified-test-suite in consumers, …/canon in the canon repo). */
export const CANON_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const CANON_REL = path.relative(ROOT, CANON_DIR).split(path.sep).join('/') || '.';
export const VENDOR_DIR = '.unified-test-suite';
export const CONFIG_FILE = 'unified-test-suite.config.json';
export const LOCK_FILE = 'unified-test-suite.lock.json';

export const RUNNERS = ['go', 'pytest', 'node-test', 'node-command', 'steps', 'playwright', 'command', 'sdd-lint', 'journeys'];
/** Names that would collide with canon tasks (test:<name>) or CLI words. Applies to layers, groups and gates. */
export const RESERVED_NAMES = ['layer', 'layers', 'report', 'clean', 'changed', 'gate', 'group', 'local', 'setup', 'doctor', 'stack', 'db'];
export const NAME_RE = /^[a-z][a-z0-9-]*$/;

export function readJson(file, fallback = undefined) {
  try {
    return JSON.parse(readFileSync(file, 'utf8'));
  } catch (err) {
    if (fallback !== undefined) return fallback;
    throw new Error(`cannot read JSON ${file}: ${err.message}`);
  }
}

export const sh = (cmd, opts = {}) => {
  try {
    return execSync(cmd, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], ...opts }).trim();
  } catch {
    return '';
  }
};

export function canonVersion() {
  try { return readFileSync(path.join(CANON_DIR, 'VERSION'), 'utf8').trim(); } catch { return 'unknown'; }
}

// ------------------------------------------------------------------ config
const DEFAULTS = {
  resultsDir: 'test-results',
  stack: {
    backendUrl: null, // no backend unless configured
    appUrl: null,
    healthPath: '/health',
    waitSeconds: 120,
    compose: null, // { files: [...], envFile: ".env" (local only, never CI) }
    dbReset: null, // command run before stateful layers on a MANAGED stack
  },
  layers: {},
  groups: {},
  gates: {},
  coverage: {},
  flaky: { strictFailPercent: 1 },
  bdd: { enabled: false, idPattern: 'BDD\\.\\d{2}\\.\\d{2}\\.[0-9a-z]{4}', catalogGlobs: [], requireCatalog: false, specGlobs: [], totalScenarios: null },
  selection: null,
  secrets: { envKeys: [], testOnlyEnvKeys: [], keysFile: null },
  providers: null, // {} = explicitly none; null = not declared yet (uts check TODO)
  e2e: { config: null, identityPoolEnv: 'E2E_USER_POOL', identitiesPerWorker: 1, minWorkersInCI: 1 },
  freeze: [],
  sdd: null, // aidoc-flow SDD mode; null = standalone (1.0.0-rc.2 behavior)
};
const SDD_DEFAULTS = { docsRoot: 'docs/sdd', framework: '.aidoc/framework', python: 'python3', mode: 'build', profile: '.aidoc/profile.yaml', journeys: 'tests/journeys' };

const merge = (base, over) => {
  if (over === undefined) return base;
  if (base && typeof base === 'object' && !Array.isArray(base) && over && typeof over === 'object' && !Array.isArray(over)) {
    const out = { ...base };
    for (const [k, v] of Object.entries(over)) out[k] = merge(base[k], v);
    return out;
  }
  return over;
};

let cachedConfig;
export function rawConfig() {
  return readJson(path.join(ROOT, CONFIG_FILE));
}
export function loadConfig({ required = true } = {}) {
  if (cachedConfig) return cachedConfig;
  const file = path.join(ROOT, CONFIG_FILE);
  if (!existsSync(file)) {
    if (!required) return merge(DEFAULTS, {});
    throw new Error(`${CONFIG_FILE} not found in ${ROOT} — run \`uts init\` from the unified-test-suite repo`);
  }
  const cfg = merge(DEFAULTS, rawConfig());
  for (const [name, layer] of Object.entries(cfg.layers)) {
    layer.name = name;
    layer.stateful = layer.stateful ?? Boolean(layer.needsStack);
  }
  for (const [name, gate] of Object.entries(cfg.gates)) {
    if (Array.isArray(gate)) cfg.gates[name] = { layers: gate };
    cfg.gates[name].name = name;
  }
  if (cfg.sdd) cfg.sdd = { ...SDD_DEFAULTS, ...cfg.sdd };
  cachedConfig = cfg;
  return cfg;
}

export const resultsDir = (cfg) => process.env.RESULTS_DIR || cfg.resultsDir;
export const reportDir = (cfg, layer) => `${resultsDir(cfg)}/reports/${layer}`;
export const toPosix = (p) => p.split(path.sep).join('/');

// ------------------------------------------------------------------ globs & files
const reCache = new Map();
export function globToRegExp(glob) {
  if (reCache.has(glob)) return reCache.get(glob);
  let re = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === '*') {
      if (glob[i + 1] === '*') {
        const slash = glob[i + 2] === '/';
        re += slash ? '(?:.*/)?' : '.*';
        i += slash ? 2 : 1;
      } else re += '[^/]*';
    } else if (c === '?') re += '[^/]';
    else if (c === '{') {
      const end = glob.indexOf('}', i);
      re += '(?:' + glob.slice(i + 1, end).split(',').map(escapeRe).join('|') + ')';
      i = end;
    } else re += escapeRe(c);
  }
  const compiled = new RegExp('^' + re + '$');
  reCache.set(glob, compiled);
  return compiled;
}
const escapeRe = (s) => s.replace(/[.+^${}()|[\]\\]/g, '\\$&');
export const matchesAny = (file, globs) => globs.some((g) => globToRegExp(g).test(file));

let fileUniverse;
/** All tracked + untracked non-ignored files (git), or a filesystem walk outside git. Excludes the vendored canon. */
export function repoFiles() {
  if (fileUniverse) return fileUniverse;
  const r = spawnSync('git', ['ls-files', '-co', '--exclude-standard', '-z'], { encoding: 'utf8', maxBuffer: 1 << 28 });
  if (r.status === 0) {
    fileUniverse = r.stdout.split('\0').filter((f) => f && !f.startsWith(`${VENDOR_DIR}/`) && existsSync(f));
  } else {
    const skip = new Set(['node_modules', '.git', 'vendor', 'dist', 'build', VENDOR_DIR, '.venv', 'venv', 'test-results']);
    const out = [];
    const walk = (d) => {
      for (const name of readdirSync(d)) {
        if (skip.has(name)) continue;
        const p = path.join(d, name);
        let st; try { st = statSync(p); } catch { continue; }
        if (st.isDirectory()) walk(p); else out.push(toPosix(path.relative(ROOT, p)));
      }
    };
    walk(ROOT);
    fileUniverse = out;
  }
  return fileUniverse;
}
export const findFiles = (globs) => repoFiles().filter((f) => matchesAny(f, globs)).sort();

// ------------------------------------------------------------------ fingerprint
// Content fingerprint of the working tree: HEAD + tracked diff + untracked non-ignored files.
// Excludes runner by-products that are often not gitignored and the effective results dir.
// A layer result is "fresh" only if produced at the fingerprint the summarizer sees now.
export const FINGERPRINT_EXCLUDES = ['**/__pycache__/**', '**/*.pyc', '**/.pytest_cache/**', '**/.coverage', '**/.coverage.*', '**/coverage.out', '**/playwright-report/**'];
export function fingerprint(results = process.env.RESULTS_DIR || null) {
  const inGit = spawnSync('git', ['rev-parse', '--is-inside-work-tree'], { encoding: 'utf8' });
  if (inGit.status !== 0) return 'nogit';
  // Exclude the results dir only when it lies inside the worktree (git rejects outside pathspecs).
  const relResults = results ? toPosix(path.relative(ROOT, path.resolve(ROOT, results))) : '';
  const inTree = relResults && !relResults.startsWith('..') && !path.isAbsolute(relResults);
  const excludes = [...FINGERPRINT_EXCLUDES, ...(inTree ? [`${relResults.replace(/\/$/, '')}/**`] : [])].map((g) => `:(exclude,glob)${g}`);
  // Fail closed: if git cannot compute the tree state, return a value that never matches → stale, never falsely fresh.
  const failed = () => `unknown-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const h = createHash('sha256');
  h.update(sh('git rev-parse HEAD') || 'no-head');
  const diff = spawnSync('git', ['diff', 'HEAD', '--binary', '--', '.', ...excludes], { maxBuffer: 1 << 30 });
  if (diff.status !== 0 && sh('git rev-parse HEAD')) return failed();
  h.update(diff.stdout ?? '');
  const untracked = spawnSync('git', ['ls-files', '--others', '--exclude-standard', '-z', '--', '.', ...excludes], { encoding: 'utf8', maxBuffer: 1 << 28 });
  if (untracked.status !== 0) return failed();
  for (const f of (untracked.stdout ?? '').split('\0').filter(Boolean).sort()) {
    h.update(f);
    try { h.update(readFileSync(f)); } catch { h.update('<unreadable>'); }
  }
  return h.digest('hex').slice(0, 16);
}

// ------------------------------------------------------------------ redaction
// Secrets are redacted by VALUE — and by every encoding a report may store them in (JSON-escaped,
// Go/HTML-safe JSON \u003c escapes, XML entities, URL encoding) — plus header/token patterns.
// Key names come from the project config (secrets.envKeys / testOnlyEnvKeys / keysFile). The identity
// pool is redacted as a whole AND leaf by leaf (emails, passwords, OTPs of 6+ chars).
const ALWAYS_SECRET = ['DATABASE_URL', 'GITHUB_TOKEN', 'NPM_TOKEN'];
const xmlEsc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');
const xmlEscNoApos = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const xmlTextEsc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const xmlNumEsc = (s) => xmlTextEsc(s).replace(/"/g, '&#34;').replace(/'/g, '&#39;');
const jsonEsc = (s) => JSON.stringify(s).slice(1, -1);
const jsonHtmlEsc = (s) => jsonEsc(s).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
const rfc3986 = (s) => encodeURIComponent(s).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
const formEnc = (s) => rfc3986(s).replace(/%20/g, '+');
function variants(v) {
  const out = new Set([v, jsonEsc(v), jsonHtmlEsc(v), xmlEsc(v), xmlEscNoApos(v), xmlTextEsc(v), xmlNumEsc(v), encodeURIComponent(v), rfc3986(v), formEnc(v)]);
  return [...out].filter((x) => x.length >= 6);
}
// Short all-digit values (test OTPs like 000000, PINs) are NOT redacted by value: they would corrupt
// amounts, ids and line numbers everywhere. Keep real secrets out of that shape.
const redactableValue = (v) => typeof v === 'string' && v.length >= 6 && !/^\d{1,9}$/.test(v);
// Identity-pool leaves are redacted only under secret-like keys (plus e2e.secretFields).
const SECRET_FIELD_RE = /pass|secret|token|otp|pin|key|cred|cookie|session/i;

let cachedSecrets;
function secretValues() {
  if (cachedSecrets) return cachedSecrets;
  let keys = [...ALWAYS_SECRET];
  let poolEnv = 'E2E_USER_POOL';
  let secretFields = [];
  try {
    const cfg = loadConfig({ required: false });
    keys.push(...(cfg.secrets.envKeys ?? []), ...(cfg.secrets.testOnlyEnvKeys ?? []));
    if (cfg.secrets.keysFile) {
      const spec = readJson(path.join(ROOT, cfg.secrets.keysFile), {});
      keys.push(...(spec.secret ?? []), ...(spec.testOnly ?? []));
    }
    poolEnv = cfg.e2e?.identityPoolEnv ?? poolEnv;
    secretFields = cfg.e2e?.secretFields ?? [];
  } catch { /* redaction must work even without a config */ }
  keys.push(poolEnv);
  keys = [...new Set(keys.filter(Boolean))];
  const pairs = [];
  const add = (label, value) => {
    if (!redactableValue(value)) return;
    for (const v of variants(value)) pairs.push([label, v]);
    // multi-line values (keys, certs): also each substantial line on its own
    if (value.includes('\n')) for (const line of value.split(/\r?\n/)) if (line.trim().length >= 8 && redactableValue(line.trim())) for (const v of variants(line.trim())) pairs.push([label, v]);
  };
  for (const k of keys) add(k, process.env[k]);
  // identity pool leaves
  try {
    const walk = (x, label, key = '') => {
      if (typeof x === 'string') { if (SECRET_FIELD_RE.test(key) || secretFields.includes(key)) add(label, x); }
      else if (Array.isArray(x)) x.forEach((y, i) => walk(y, `${label}[${i}]`, key));
      else if (x && typeof x === 'object') for (const [k, y] of Object.entries(x)) walk(y, `${label}.${k}`, k);
    };
    if (process.env[poolEnv]) walk(JSON.parse(process.env[poolEnv]), poolEnv);
  } catch { /* invalid pool JSON: whole-value redaction above still applies */ }
  cachedSecrets = pairs.sort((a, b) => b[1].length - a[1].length);
  return cachedSecrets;
}

const PATTERNS = [
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, '[REDACTED:PEM]'],
  [/\b(authorization|proxy-authorization|x-webhook-signature|x-admin-token|x-api-key|cookie|set-cookie)(["']?\s*[:=]\s*["']?)[^\s"',;&<\\]+(?:[ \t]+[^\s"',;&<\\]+)?/gi, '$1$2[REDACTED]'],
  [/\bBearer\s+[A-Za-z0-9._~+/-]+=*/g, 'Bearer [REDACTED]'],
  [/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g, '[REDACTED:JWT]'],
  [/\b(privy-token|privy-id-token|privy-refresh-token|session|sessionid|refresh_token|access_token)=([^;\s"&]+)/gi, '$1=[REDACTED]'],
  [/\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?):\/\/([^:\s/]+):([^@\s]+)@/gi, '$1://$2:[REDACTED]@'],
];

export function redact(text) {
  if (text == null) return text;
  let s = String(text);
  for (const [key, value] of secretValues()) if (s.includes(value)) s = s.split(value).join(`[REDACTED:${key}]`);
  for (const [re, rep] of PATTERNS) s = s.replace(re, rep);
  return s;
}

/** Redact every string inside a JSON value (serialized-JSON text redaction can corrupt escapes). */
export function redactDeep(value) {
  if (typeof value === 'string') return redact(value);
  if (Array.isArray(value)) return value.map(redactDeep);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, redactDeep(v)]));
  return value;
}

/** Redact a document by type. JSON/JSON Lines structurally; everything else as text (all encodings). */
export function redactDocument(text, filename) {
  if (filename.endsWith('.json')) {
    try { const doc = JSON.parse(text); const out = JSON.stringify(redactDeep(doc), null, 2) + '\n'; return JSON.stringify(doc, null, 2) + '\n' === out ? text : out; } catch { /* JSON Lines? */ }
    try {
      let changed = false;
      const lines = text.split('\n').map((l) => {
        if (!l.trim()) return l;
        const doc = JSON.parse(l); const red = redactDeep(doc);
        const a = JSON.stringify(doc); const b = JSON.stringify(red);
        if (a === b) return l;
        changed = true; return b;
      });
      return changed ? lines.join('\n') : text;
    } catch { /* plain text */ }
  }
  return redact(text);
}

export const stripAnsi = (s) => String(s ?? '').replace(/\u001b\[[0-9;]*[A-Za-z]/g, '');
export const shq = (s) => `'${String(s).replace(/'/g, `'\\''`)}'`;

// ------------------------------------------------------------------ steps (single parser)
/** "name :: command" — split on the FIRST "::"; blank lines and # comments ignored. */
export function parseSteps(text) {
  const steps = [];
  for (const raw of text.split('\n')) {
    if (/^\s*#/.test(raw) || !raw.includes('::')) continue;
    const i = raw.indexOf('::');
    steps.push({ name: raw.slice(0, i).trim(), run: raw.slice(i + 2).trim() });
  }
  return steps;
}

/** Steps of a `steps` layer: inline array or a list file. */
export function layerSteps(layer) {
  if (Array.isArray(layer.steps)) return layer.steps.map((s) => ({ name: s.name, run: s.run }));
  if (typeof layer.steps === 'string' && existsSync(layer.steps)) return parseSteps(readFileSync(layer.steps, 'utf8'));
  return [];
}

export function isLoopbackUrl(url) {
  try {
    const h = new URL(url).hostname.replace(/^\[|\]$/g, '');
    return h === 'localhost' || h === '::1' || /^127\./.test(h);
  } catch {
    return false;
  }
}
