#!/usr/bin/env bash
# Tests for pull.sh, the consumer updater. Mirrors test_install.sh style:
# mktemp fixtures, FAIL + exit 1 assertions, PASS at the end.
#
# Model under test: project scope carries rules, the detector, GOVERNANCE.md,
# the gate (via install.sh delegation), and project-owned adaptations. Canon
# skills/agents are user-scope components — pull.sh never copies them into the
# project (a same-name project skill would be masked dead weight; a same-name
# project agent without a marker would be an undeclared collision), it points
# at the global install instead.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"   # the master .agents dir
PULL="$SRC/scripts/pull.sh"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

# Deterministic detector: an empty global root, so project copies never read
# as masked/colliding no matter what this machine has installed globally.
mkdir -p "$tmp/emptyglobal/skills" "$tmp/emptyglobal/agents"
export AGENTS_CONFIG_DIR="$tmp/emptyglobal"

# --- fresh pull into an empty git repo -------------------------------------
git init -q "$tmp/repoA"
[ -x "$PULL" ] || { echo "FAIL: pull.sh missing or not executable"; exit 1; }
bash "$PULL" "$tmp/repoA" >"$tmp/pull-test-A.log" 2>&1 \
  || { echo "FAIL: fresh pull exited nonzero"; cat "$tmp/pull-test-A.log"; exit 1; }
base="$tmp/repoA/.agents"
# canon-owned project-scope content lands
for f in GOVERNANCE.md rules/skill-overrides.md scripts/check-skill-shadowing.sh; do
  cmp -s "$SRC/$f" "$base/$f" || { echo "FAIL: missing or wrong $f"; exit 1; }
done
# the gate arrives via install.sh delegation, not the generic walk
test -f "$base/skills/verified-planning/check_plan.py" \
  || { echo "FAIL: gate not delegated"; exit 1; }
# ...but canon skills/agents are NEVER copied into project scope ...
test ! -e "$base/skills/ship-it" \
  || { echo "FAIL: canon skill copied to project (masked dead weight)"; exit 1; }
test ! -e "$base/agents/code-reviewer.md" \
  || { echo "FAIL: canon agent copied to project (undeclared collision)"; exit 1; }
# ...the run points at the global install instead
grep -q 'user-scope components' "$tmp/pull-test-A.log" \
  || { echo "FAIL: no global-install pointer"; exit 1; }
# canon-dev harness and the updater itself never travel
for f in scripts/pull.sh scripts/check-config.sh scripts/guard-bash.sh \
         scripts/check-frontmatter.py scripts/test-pull.sh; do
  test ! -e "$base/$f" || { echo "FAIL: shipped $f"; exit 1; }
done
grep -q 'detector: clean' "$tmp/pull-test-A.log" \
  || { echo "FAIL: no clean detector verdict"; exit 1; }

# --- consumer drift survives a re-run: differing files skipped, exit 1 ------
echo "# consumer tuning" >> "$base/rules/skill-overrides.md"
echo "# consumer wrapper" >> "$base/scripts/check-skill-shadowing.sh"
if bash "$PULL" "$tmp/repoA" >"$tmp/pull-test-B.log" 2>&1; then
  echo "FAIL: re-run over differing files exited 0"; exit 1
fi
grep -q 'consumer tuning' "$base/rules/skill-overrides.md" \
  || { echo "FAIL: rule file clobbered"; exit 1; }
grep -q 'consumer wrapper' "$base/scripts/check-skill-shadowing.sh" \
  || { echo "FAIL: detector copy clobbered"; exit 1; }

# --- --force restores, --backup keeps what it replaced ----------------------
bash "$PULL" --force --backup "$tmp/bak" "$tmp/repoA" >/dev/null 2>&1 \
  || { echo "FAIL: --force run failed"; exit 1; }
cmp -s "$SRC/rules/skill-overrides.md" "$base/rules/skill-overrides.md" \
  || { echo "FAIL: --force did not restore rule file"; exit 1; }
grep -q 'consumer tuning' "$tmp/bak/repoA/.agents/rules/skill-overrides.md" \
  || { echo "FAIL: backup missing replaced content"; exit 1; }

# --- adaptations are never touched ------------------------------------------
# -local skill, differing from its namesake
mkdir -p "$base/skills/ship-it-local"
printf 'adapted\n' > "$base/skills/ship-it-local/SKILL.md"
# -local skill identical to its namesake (retire candidate)
mkdir -p "$base/skills/recall-local"
cp "$SRC/skills/recall/SKILL.md" "$base/skills/recall-local/SKILL.md"
# OVERRIDES-marked agent, differing
cat > "$base/agents/tuned.md" <<'EOF'
---
name: code-reviewer
model: sonnet
---
# Tuned reviewer
> **OVERRIDES GLOBAL** — `~/.agents/agents/code-reviewer.md`
> **Why:** testing
> **Reconciled:** 2026-10-02
EOF
# OVERRIDES-marked agent: canon bytes + declaration block (drop candidate —
# pull compares minus the declaration lines, which can never match canon)
printf '%s\n' '> **OVERRIDES GLOBAL** — `~/.agents/agents/code-reviewer.md`' \
  '> **Why:** testing' '> **Reconciled:** 2026-10-02' \
  | cat "$SRC/agents/code-reviewer.md" - > "$base/agents/identical.md"
bash "$PULL" "$tmp/repoA" >"$tmp/pull-test-C.log" 2>&1 \
  || { echo "FAIL: adaptation run failed"; exit 1; }
grep -q 'adapted' "$base/skills/ship-it-local/SKILL.md" \
  || { echo "FAIL: -local skill touched"; exit 1; }
grep -q 'Tuned reviewer' "$base/agents/tuned.md" \
  || { echo "FAIL: OVERRIDES agent touched"; exit 1; }
grep -q 'kept adaptation: .*ship-it-local.*differs' "$tmp/pull-test-C.log" \
  || { echo "FAIL: -local reconcile not reported"; exit 1; }
grep -q 'recall-local.*consider retiring' "$tmp/pull-test-C.log" \
  || { echo "FAIL: identical -local not flagged"; exit 1; }
grep -q 'kept adaptation: .*tuned.md.*differs' "$tmp/pull-test-C.log" \
  || { echo "FAIL: OVERRIDES reconcile not reported"; exit 1; }
grep -q 'identical.md.*consider dropping' "$tmp/pull-test-C.log" \
  || { echo "FAIL: identical override not flagged"; exit 1; }

# --- undeclared same-name agent: flagged, untouched, exit 1 ------------------
cat > "$base/agents/sneaky.md" <<'EOF'
---
name: code-reviewer
model: sonnet
---
# Sneaky reviewer, no marker
EOF
if bash "$PULL" "$tmp/repoA" >"$tmp/pull-test-D.log" 2>&1; then
  echo "FAIL: undeclared collision exited 0"; exit 1
fi
grep -q 'Sneaky reviewer' "$base/agents/sneaky.md" \
  || { echo "FAIL: undeclared agent touched"; exit 1; }
grep -q 'DEFECT: undeclared same-name agent' "$tmp/pull-test-D.log" \
  || { echo "FAIL: undeclared collision not flagged"; exit 1; }
rm "$base/agents/sneaky.md" "$base/agents/identical.md" "$base/agents/tuned.md"
rm -rf "$base/skills/ship-it-local" "$base/skills/recall-local"

# --- project scope pull leaves project skill dirs alone ----------------------
# Same-name project skill dirs are NOT canon-owned (canon skills are
# user-scope); pull neither fills nor prunes them — the detector owns that.
mkdir -p "$base/skills/ship-it"
printf 'mine\n' > "$base/skills/ship-it/OWN.md"
printf 'mine\n' > "$base/rules/zz-local.md"
bash "$PULL" "$tmp/repoA" >"$tmp/pull-test-E.log" 2>&1 \
  || { echo "FAIL: pull with project-only files failed"; exit 1; }
grep -q 'mine' "$base/skills/ship-it/OWN.md" \
  || { echo "FAIL: project skill dir pruned"; exit 1; }
test ! -e "$base/skills/ship-it/SKILL.md" \
  || { echo "FAIL: canon skill leaked into project dir"; exit 1; }
grep -q 'mine' "$base/rules/zz-local.md" \
  || { echo "FAIL: extra rule file removed"; exit 1; }
rm -rf "$base/skills/ship-it" "$base/rules/zz-local.md"

# --- self-heal: a stale pull.sh copy in the consumer is removed --------------
# (byte-identical: only a provable copy is removed since #234)
cp "$PULL" "$base/scripts/pull.sh"
bash "$PULL" "$tmp/repoA" >/dev/null 2>&1 \
  || { echo "FAIL: self-heal run failed"; exit 1; }
test ! -e "$base/scripts/pull.sh" \
  || { echo "FAIL: stale pull.sh not removed"; exit 1; }

# --- garbage in: refuse without creating anything -----------------------------
if bash "$PULL" "$tmp/does-not-exist" >/dev/null 2>&1; then
  echo "FAIL: accepted a nonexistent dir"; exit 1
fi
mkdir -p "$tmp/notgit"
if bash "$PULL" "$tmp/notgit" >/dev/null 2>&1; then
  echo "FAIL: accepted a non-git dir"; exit 1
fi
[ -e "$tmp/notgit/.agents" ] && { echo "FAIL: created dirs in refused repo"; exit 1; }

# --- self-target: the canon checkout refuses, and pull.sh survives -----------
CANON="$(cd "$SRC/.." && pwd)"
if bash "$PULL" "$CANON" >/dev/null 2>&1; then
  echo "FAIL: accepted the canon itself as target"; exit 1
fi
test -f "$PULL" || { echo "FAIL: self-target run removed pull.sh"; exit 1; }

# --- --dry-run changes nothing -------------------------------------------------
git init -q "$tmp/repoB"
bash "$PULL" --dry-run "$tmp/repoB" >/dev/null 2>&1 \
  || { echo "FAIL: --dry-run failed"; exit 1; }
[ -e "$tmp/repoB/.agents" ] && { echo "FAIL: --dry-run created files"; exit 1; }

# --- multiple repos in one call -------------------------------------------------
git init -q "$tmp/repoC"
bash "$PULL" "$tmp/repoB" "$tmp/repoC" >/dev/null 2>&1 \
  || { echo "FAIL: multi-repo run failed"; exit 1; }
test -f "$tmp/repoB/.agents/GOVERNANCE.md" \
  || { echo "FAIL: repoB not pulled"; exit 1; }
test -f "$tmp/repoC/.agents/GOVERNANCE.md" \
  || { echo "FAIL: repoC not pulled"; exit 1; }

# --- pull-state memory --------------------------------------------------------
# Every pull records the canon SHA it pulled; the next pull reports
# canon-side deletions still present in the project (advice, never delete).
git init -q "$tmp/repoD"
bash "$PULL" "$tmp/repoD" >"$tmp/pull-test-F.log" 2>&1 \
  || { echo "FAIL: state-write run failed"; exit 1; }
test -f "$tmp/repoD/.agents/.canon-sha" \
  || { echo "FAIL: .canon-sha not written"; exit 1; }
[ "$(sed -n '1p' "$tmp/repoD/.agents/.canon-sha")" = "$(git -C "$SRC/.." rev-parse HEAD)" ] \
  || { echo "FAIL: .canon-sha does not name canon HEAD"; exit 1; }
grep -q 'no previous pull recorded' "$tmp/pull-test-F.log" \
  || { echo "FAIL: first pull missing no-state note"; exit 1; }
bash "$PULL" "$tmp/repoD" >"$tmp/pull-test-G.log" 2>&1 \
  || { echo "FAIL: second pull failed"; exit 1; }
grep -q 'canon unchanged since last pull' "$tmp/pull-test-G.log" \
  || { echo "FAIL: unchanged-canon note missing"; exit 1; }

# removal report, against a fixture canon with real deletion history
# (the live canon has never deleted a .agents file, so PULL_SRC points
# pull at the fixture — the seam exists so this path is testable)
mkdir -p "$tmp/fakecanon/.agents/rules" "$tmp/fakecanon/.agents/scripts"
printf '# fake canon\n' > "$tmp/fakecanon/.agents/GOVERNANCE.md"
printf 'doomed rule\n' > "$tmp/fakecanon/.agents/rules/doomed.md"
printf 'kept rule\n' > "$tmp/fakecanon/.agents/rules/kept.md"
cp "$SRC/scripts/check-skill-shadowing.sh" "$tmp/fakecanon/.agents/scripts/"
git -C "$tmp/fakecanon" init -q
git -C "$tmp/fakecanon" add -A
git -C "$tmp/fakecanon" -c user.email=t@t -c user.name=t commit -qm v1
git init -q "$tmp/repoE"
PULL_SRC="$tmp/fakecanon/.agents" bash "$PULL" "$tmp/repoE" >/dev/null 2>&1 \
  || { echo "FAIL: fixture pull v1 failed"; exit 1; }
test -f "$tmp/repoE/.agents/rules/doomed.md" \
  || { echo "FAIL: fixture rule not pulled"; exit 1; }
git -C "$tmp/fakecanon" rm -q .agents/rules/doomed.md
git -C "$tmp/fakecanon" -c user.email=t@t -c user.name=t commit -qm v2
PULL_SRC="$tmp/fakecanon/.agents" bash "$PULL" "$tmp/repoE" >"$tmp/pull-test-H.log" 2>&1 \
  || { echo "FAIL: fixture pull v2 failed"; exit 1; }
grep -q 'removed from canon (still present.*doomed.md' "$tmp/pull-test-H.log" \
  || { echo "FAIL: removal not reported"; cat "$tmp/pull-test-H.log"; exit 1; }
test -f "$tmp/repoE/.agents/rules/doomed.md" \
  || { echo "FAIL: removal report deleted the file"; exit 1; }
grep -q 'removed from canon.*kept.md' "$tmp/pull-test-H.log" \
  && { echo "FAIL: kept file reported removed"; exit 1; }

# unknown old SHA degrades to a note, never an error
printf '0000000000000000000000000000000000000000\n' > "$tmp/repoE/.agents/.canon-sha"
PULL_SRC="$tmp/fakecanon/.agents" bash "$PULL" "$tmp/repoE" >"$tmp/pull-test-I.log" 2>&1 \
  || { echo "FAIL: unknown-SHA run failed"; exit 1; }
grep -q 'cannot determine removals' "$tmp/pull-test-I.log" \
  || { echo "FAIL: unknown-SHA note missing"; exit 1; }

# dry-run writes no state
git init -q "$tmp/repoF"
bash "$PULL" --dry-run "$tmp/repoF" >/dev/null 2>&1 \
  || { echo "FAIL: dry-run failed"; exit 1; }
test ! -e "$tmp/repoF/.agents/.canon-sha" \
  || { echo "FAIL: dry-run wrote state"; exit 1; }

# --- --global refreshes user scope, never the real home -----------------------
# Every mutating global case sets AGENTS_CONFIG_DIR to a fake root; the only
# case that unsets it is a --dry-run asserting the printed default root.
FAKEHOME="$tmp/fakehome/.agents"
env -u AGENTS_CONFIG_DIR bash "$PULL" --global --dry-run >"$tmp/pull-test-M.log" 2>&1 \
  || { echo "FAIL: default-root dry-run failed"; exit 1; }
grep -q "user root: $HOME/.agents" "$tmp/pull-test-M.log" \
  || { echo "FAIL: default user root not $HOME/.agents"; exit 1; }
AGENTS_CONFIG_DIR="$FAKEHOME" bash "$PULL" --global >"$tmp/pull-test-J.log" 2>&1 \
  || { echo "FAIL: fresh --global failed"; cat "$tmp/pull-test-J.log"; exit 1; }
# user scope = skills + agents, full skill dirs (SKILL.md lives here)
for f in skills/ship-it/SKILL.md skills/verified-planning/check_plan.py \
         skills/verified-planning/SKILL.md agents/code-reviewer.md; do
  cmp -s "$SRC/$f" "$FAKEHOME/$f" || { echo "FAIL: global missing $f"; exit 1; }
done
# ...except the installer itself (stale-copy re-infection, repo rule mirrored)
test ! -e "$FAKEHOME/skills/verified-planning/install.sh" \
  || { echo "FAIL: --global shipped install.sh"; exit 1; }
# ...and project-scope content never leaks into the user root
for f in GOVERNANCE.md rules/skill-overrides.md scripts/check-skill-shadowing.sh \
         .canon-sha; do
  test ! -e "$FAKEHOME/$f" || { echo "FAIL: --global leaked $f"; exit 1; }
done

# drift: skip + exit 1, preserved; --force refreshes with a live warning
echo "# my tuning" >> "$FAKEHOME/skills/ship-it/SKILL.md"
if AGENTS_CONFIG_DIR="$FAKEHOME" bash "$PULL" --global >"$tmp/pull-test-K.log" 2>&1; then
  echo "FAIL: --global over differing files exited 0"; exit 1
fi
grep -q 'my tuning' "$FAKEHOME/skills/ship-it/SKILL.md" \
  || { echo "FAIL: user file clobbered"; exit 1; }
AGENTS_CONFIG_DIR="$FAKEHOME" bash "$PULL" --global --force --backup "$tmp/gbak" \
  >"$tmp/pull-test-L.log" 2>&1 \
  || { echo "FAIL: --global --force failed"; exit 1; }
cmp -s "$SRC/skills/ship-it/SKILL.md" "$FAKEHOME/skills/ship-it/SKILL.md" \
  || { echo "FAIL: --force did not refresh user file"; exit 1; }
grep -q 'my tuning' "$tmp/gbak/.agents/skills/ship-it/SKILL.md" \
  || { echo "FAIL: global backup missing replaced content"; exit 1; }
grep -q 'LIVE user config' "$tmp/pull-test-L.log" \
  || { echo "FAIL: no live-session --force warning"; exit 1; }

# user-added names (incl. -local-looking ones: no such concept here) survive
printf 'mine\n' > "$FAKEHOME/skills/zz-mine.md"
mkdir -p "$FAKEHOME/skills/ship-it-local"
printf 'mine\n' > "$FAKEHOME/skills/ship-it-local/SKILL.md"
AGENTS_CONFIG_DIR="$FAKEHOME" bash "$PULL" --global >/dev/null 2>&1 \
  || { echo "FAIL: user-added run failed"; exit 1; }
grep -q 'mine' "$FAKEHOME/skills/zz-mine.md" \
  || { echo "FAIL: user-added file removed"; exit 1; }
grep -q 'mine' "$FAKEHOME/skills/ship-it-local/SKILL.md" \
  || { echo "FAIL: -local-looking user dir touched"; exit 1; }

# self-heal: a stale global install.sh copy is removed — but only a
# byte-identical copy is provably stale (#252)
cp "$SRC/skills/verified-planning/install.sh" "$FAKEHOME/skills/verified-planning/install.sh"
AGENTS_CONFIG_DIR="$FAKEHOME" bash "$PULL" --global >/dev/null 2>&1 \
  || { echo "FAIL: global self-heal run failed"; exit 1; }
test ! -e "$FAKEHOME/skills/verified-planning/install.sh" \
  || { echo "FAIL: stale global install.sh not removed"; exit 1; }

# refusals: the user root must never resolve into the canon checkout ...
CANON="$(cd "$SRC/.." && pwd)"
if AGENTS_CONFIG_DIR="$SRC" bash "$PULL" --global >/dev/null 2>&1; then
  echo "FAIL: accepted the canon .agents as user root"; exit 1
fi
if AGENTS_CONFIG_DIR="$CANON" bash "$PULL" --global >/dev/null 2>&1; then
  echo "FAIL: accepted the canon checkout as user root"; exit 1
fi
if AGENTS_CONFIG_DIR="$CANON/.agents/skills" bash "$PULL" --global >/dev/null 2>&1; then
  echo "FAIL: accepted a dir inside the canon as user root"; exit 1
fi
# ... and --global takes no repo arguments
if AGENTS_CONFIG_DIR="$FAKEHOME" bash "$PULL" --global "$tmp/repoA" >/dev/null 2>&1; then
  echo "FAIL: --global accepted a repo argument"; exit 1
fi

# dry-run writes nothing anywhere
AGENTS_CONFIG_DIR="$tmp/fakedry" bash "$PULL" --global --dry-run >/dev/null 2>&1 \
  || { echo "FAIL: --global --dry-run failed"; exit 1; }
test ! -e "$tmp/fakedry" || { echo "FAIL: --global --dry-run created dirs"; exit 1; }

# a redirected canon source is announced loudly, never honored silently (#213)
PULL_SRC="$tmp/fakecanon/.agents" bash "$PULL" --dry-run "$tmp/repoE" >"$tmp/pull-test-P.log" 2>&1 \
  || { echo "FAIL: PULL_SRC dry-run failed"; exit 1; }
grep -q 'WARNING: using PULL_SRC=' "$tmp/pull-test-P.log" \
  || { echo "FAIL: no PULL_SRC banner"; exit 1; }
grep -q 'PULL_SRC=' "$tmp/pull-test-A.log" \
  && { echo "FAIL: banner on a normal run"; exit 1; }

# removal advice for a deleted gate names the live workflow too, not just the
# inert .agents copy (#213)
mkdir -p "$tmp/fakecanon2/.agents/skills/verified-planning" "$tmp/fakecanon2/.agents/scripts"
printf '# fake canon 2\n' > "$tmp/fakecanon2/.agents/GOVERNANCE.md"
printf 'gate: v1\n' > "$tmp/fakecanon2/.agents/skills/verified-planning/plan-gate.yml"
cp "$SRC/scripts/check-skill-shadowing.sh" "$tmp/fakecanon2/.agents/scripts/"
git -C "$tmp/fakecanon2" init -q
git -C "$tmp/fakecanon2" add -A
git -C "$tmp/fakecanon2" -c user.email=t@t -c user.name=t commit -qm v1
git init -q "$tmp/repoI"
PULL_SRC="$tmp/fakecanon2/.agents" bash "$PULL" "$tmp/repoI" >/dev/null 2>&1 \
  || { echo "FAIL: fixture2 pull v1 failed"; exit 1; }
mkdir -p "$tmp/repoI/.agents/skills/verified-planning" "$tmp/repoI/.github/workflows"
cp "$tmp/fakecanon2/.agents/skills/verified-planning/plan-gate.yml" \
  "$tmp/repoI/.agents/skills/verified-planning/plan-gate.yml"
cp "$tmp/fakecanon2/.agents/skills/verified-planning/plan-gate.yml" \
  "$tmp/repoI/.github/workflows/plan-gate.yml"
git -C "$tmp/fakecanon2" rm -q .agents/skills/verified-planning/plan-gate.yml
git -C "$tmp/fakecanon2" -c user.email=t@t -c user.name=t commit -qm v2
PULL_SRC="$tmp/fakecanon2/.agents" bash "$PULL" "$tmp/repoI" >"$tmp/pull-test-Q.log" 2>&1 \
  || { echo "FAIL: fixture2 pull v2 failed"; exit 1; }
grep -q 'live workflow still present.*\.github/workflows/plan-gate\.yml' "$tmp/pull-test-Q.log" \
  || { echo "FAIL: live workflow not named in removal advice"; cat "$tmp/pull-test-Q.log"; exit 1; }
# same mapping for the reusable name going forward (#229)
# (mkdir: the v2 `git rm` pruned the emptied fixture dirs)
mkdir -p "$tmp/fakecanon2/.agents/skills/verified-planning"
printf 'gate: reusable\n' > "$tmp/fakecanon2/.agents/skills/verified-planning/plan-gate-reusable.yml"
git -C "$tmp/fakecanon2" add -A >/dev/null 2>&1
git -C "$tmp/fakecanon2" -c user.email=t@t -c user.name=t commit -qm v3 >/dev/null 2>&1
mkdir -p "$tmp/repoI/.github/workflows"
cp "$tmp/fakecanon2/.agents/skills/verified-planning/plan-gate-reusable.yml" \
  "$tmp/repoI/.github/workflows/plan-gate-reusable.yml"
git -C "$tmp/fakecanon2" rm -q .agents/skills/verified-planning/plan-gate-reusable.yml >/dev/null 2>&1
git -C "$tmp/fakecanon2" -c user.email=t@t -c user.name=t commit -qm v4 >/dev/null 2>&1
PULL_SRC="$tmp/fakecanon2/.agents" bash "$PULL" "$tmp/repoI" >"$tmp/pull-test-Q2.log" 2>&1 \
  || { echo "FAIL: fixture2 pull v4 failed"; exit 1; }
grep -q 'live workflow still present.*\.github/workflows/plan-gate-reusable\.yml' "$tmp/pull-test-Q2.log" \
  || { echo "FAIL: live reusable workflow not named"; cat "$tmp/pull-test-Q2.log"; exit 1; }

# a declaration-only override file reconciles and the run completes (#213).
# Characterization note: the abort scenario first suspected here does not
# reproduce — `pull_into || exit 2` disables `set -e` inside the whole call
# tree, so the `|| true` in strip_decl is consistency hardening (cf.
# changed_lines), not a crash fix. This locks the degenerate behavior anyway.
git init -q "$tmp/repoG"
bash "$PULL" "$tmp/repoG" >/dev/null 2>&1 \
  || { echo "FAIL: repoG fresh pull failed"; exit 1; }
printf '%s\n' '> **OVERRIDES GLOBAL** — `~/.agents/agents/code-reviewer.md`' \
  '> **Why:** testing' '> **Reconciled:** 2026-10-02' \
  > "$tmp/repoG/.agents/agents/code-reviewer.md"
# Exit 1 is the expected detector-attention outcome (the file genuinely has
# no frontmatter); the regression is dying mid-run, which leaves neither line.
bash "$PULL" "$tmp/repoG" >"$tmp/pull-test-R.log" 2>&1 || true
grep -q 'kept adaptation: .*code-reviewer\.md' "$tmp/pull-test-R.log" \
  || { echo "FAIL: degenerate override not reconciled"; cat "$tmp/pull-test-R.log"; exit 1; }
grep -q 'pulled into' "$tmp/pull-test-R.log" \
  || { echo "FAIL: pull died on declaration-only override"; cat "$tmp/pull-test-R.log"; exit 1; }

# self-heal deletions go through --backup when one is given (#213)
git init -q "$tmp/repoH"
bash "$PULL" "$tmp/repoH" >/dev/null 2>&1 \
  || { echo "FAIL: repoH fresh pull failed"; exit 1; }
cp "$PULL" "$tmp/repoH/.agents/scripts/pull.sh"   # byte-identical: provably stale
bash "$PULL" --force --backup "$tmp/bak2" "$tmp/repoH" >/dev/null 2>&1 \
  || { echo "FAIL: backup deletion run failed"; exit 1; }
test ! -e "$tmp/repoH/.agents/scripts/pull.sh" \
  || { echo "FAIL: stale pull.sh not removed"; exit 1; }
cmp -s "$PULL" "$tmp/bak2/repoH/.agents/scripts/pull.sh" \
  || { echo "FAIL: deleted pull.sh not backed up"; exit 1; }
cp "$SRC/skills/verified-planning/install.sh" "$FAKEHOME/skills/verified-planning/install.sh"   # byte-identical: provably stale
AGENTS_CONFIG_DIR="$FAKEHOME" bash "$PULL" --global --force --backup "$tmp/gbak2" >/dev/null 2>&1 \
  || { echo "FAIL: global backup deletion run failed"; exit 1; }
test ! -e "$FAKEHOME/skills/verified-planning/install.sh" \
  || { echo "FAIL: stale install.sh not removed"; exit 1; }
test -f "$tmp/gbak2/.agents/skills/verified-planning/install.sh" \
  || { echo "FAIL: deleted install.sh not backed up"; exit 1; }

# global self-heal mirrors the repo rule: only a byte-identical installer
# copy is provably stale; a foreign file is kept and flagged (#252)
printf 'my helper\n' > "$FAKEHOME/skills/verified-planning/install.sh"
if AGENTS_CONFIG_DIR="$FAKEHOME" bash "$PULL" --global >"$tmp/pull-test-W.log" 2>&1; then
  echo "FAIL: global run with foreign install.sh exited 0"; exit 1
fi
grep -q 'my helper' "$FAKEHOME/skills/verified-planning/install.sh" \
  || { echo "FAIL: foreign global helper deleted"; exit 1; }
grep -q 'SKIP foreign file at canon-owned path' "$tmp/pull-test-W.log" \
  || { echo "FAIL: foreign global copy not flagged"; exit 1; }

# a failed copy reports failure, never success (#233)
git init -q "$tmp/repoL"
bash "$PULL" "$tmp/repoL" >/dev/null 2>&1 \
  || { echo "FAIL: repoL fresh pull failed"; exit 1; }
rm "$tmp/repoL/.agents/rules/skill-overrides.md"
chmod a-w "$tmp/repoL/.agents/rules"
if bash "$PULL" "$tmp/repoL" >"$tmp/pull-test-U.log" 2>&1; then
  chmod u+w "$tmp/repoL/.agents/rules"
  echo "FAIL: pull with failing copy exited 0"; exit 1
fi
chmod u+w "$tmp/repoL/.agents/rules"
grep -q 'pulled into' "$tmp/pull-test-U.log" \
  && { echo "FAIL: failed pull reported success"; exit 1; }

# one bad repo does not skip the rest: each is attempted, failures named,
# exit 2 at the end (#233)
git init -q "$tmp/repoM"
git init -q "$tmp/repoN"
if bash "$PULL" "$tmp/repoM" "$tmp/does-not-exist" "$tmp/repoN" >"$tmp/pull-test-V.log" 2>&1; then
  echo "FAIL: multi-repo run with bad path exited 0"; exit 1
fi
test -f "$tmp/repoM/.agents/GOVERNANCE.md" \
  || { echo "FAIL: repo before bad path not pulled"; exit 1; }
test -f "$tmp/repoN/.agents/GOVERNANCE.md" \
  || { echo "FAIL: repo after bad path not pulled"; exit 1; }
grep -q 'does-not-exist' "$tmp/pull-test-V.log" \
  || { echo "FAIL: bad repo not named"; exit 1; }

# only a byte-identical pull.sh copy is provably stale and removed; anything
# else at that canon-owned path is kept and flagged like a skip (#234)
git init -q "$tmp/repoK"
bash "$PULL" "$tmp/repoK" >/dev/null 2>&1 \
  || { echo "FAIL: repoK fresh pull failed"; exit 1; }
printf 'my helper\n' > "$tmp/repoK/.agents/scripts/pull.sh"
if bash "$PULL" "$tmp/repoK" >"$tmp/pull-test-S.log" 2>&1; then
  echo "FAIL: foreign pull.sh copy exited 0"; exit 1
fi
grep -q 'my helper' "$tmp/repoK/.agents/scripts/pull.sh" \
  || { echo "FAIL: foreign helper deleted"; exit 1; }
grep -q 'SKIP foreign file at canon-owned path' "$tmp/pull-test-S.log" \
  || { echo "FAIL: foreign copy not flagged"; exit 1; }
cp "$PULL" "$tmp/repoK/.agents/scripts/pull.sh"
bash "$PULL" "$tmp/repoK" >"$tmp/pull-test-T.log" 2>&1 \
  || { echo "FAIL: identical-copy run failed"; exit 1; }
test ! -e "$tmp/repoK/.agents/scripts/pull.sh" \
  || { echo "FAIL: identical stale copy kept"; exit 1; }

# --no-ci forwards to the gate installer: no dead .github/ tree on
# non-GitHub forges, gate files still land (#254)
git init -q "$tmp/repoT"
bash "$PULL" --no-ci "$tmp/repoT" >/dev/null 2>&1 \
  || { echo "FAIL: --no-ci pull failed"; exit 1; }
test ! -e "$tmp/repoT/.github/workflows/plan-gate-reusable.yml" \
  || { echo "FAIL: --no-ci pull installed the workflow"; exit 1; }
test -f "$tmp/repoT/.agents/skills/verified-planning/check_plan.py" \
  || { echo "FAIL: --no-ci pull skipped the gate"; exit 1; }

# a shared --backup must not collide across repos: each repo's originals
# land under their own subdirectory (#253)
git init -q "$tmp/repoO"; git init -q "$tmp/repoP"
bash "$PULL" "$tmp/repoO" "$tmp/repoP" >/dev/null 2>&1 \
  || { echo "FAIL: repoO/repoP fresh pull failed"; exit 1; }
printf 'O-original\n' > "$tmp/repoO/.agents/GOVERNANCE.md"
printf 'P-original\n' > "$tmp/repoP/.agents/GOVERNANCE.md"
bash "$PULL" --force --backup "$tmp/bakOP" "$tmp/repoO" "$tmp/repoP" >/dev/null 2>&1 \
  || { echo "FAIL: forced multi-repo backup run failed"; exit 1; }
grep -q 'O-original' "$tmp/bakOP/repoO/.agents/GOVERNANCE.md" \
  || { echo "FAIL: repoO original lost to backup collision"; exit 1; }
grep -q 'P-original' "$tmp/bakOP/repoP/.agents/GOVERNANCE.md" \
  || { echo "FAIL: repoP original lost to backup collision"; exit 1; }

# option-shaped .canon-sha text never reaches git rev parsing: it reads as
# "cannot determine removals", not a usage spray (#253)
git init -q "$tmp/repoR"
bash "$PULL" "$tmp/repoR" >/dev/null 2>&1 \
  || { echo "FAIL: repoR fresh pull failed"; exit 1; }
printf -- '--help\n' > "$tmp/repoR/.agents/.canon-sha"
bash "$PULL" "$tmp/repoR" >"$tmp/pull-test-X.log" 2>&1 \
  || { echo "FAIL: option-shaped state run failed"; exit 1; }
grep -q 'cannot determine removals' "$tmp/pull-test-X.log" \
  || { echo "FAIL: no graceful note for unrecognized state"; exit 1; }
grep -q 'usage: git' "$tmp/pull-test-X.log" \
  && { echo "FAIL: state text reached git rev parsing"; exit 1; }

# a failed state write fails the pull instead of printing success (#253)
git init -q "$tmp/repoS"
bash "$PULL" "$tmp/repoS" >/dev/null 2>&1 \
  || { echo "FAIL: repoS fresh pull failed"; exit 1; }
rm "$tmp/repoS/.agents/.canon-sha"
mkdir "$tmp/repoS/.agents/.canon-sha"
if bash "$PULL" "$tmp/repoS" >"$tmp/pull-test-Y.log" 2>&1; then
  echo "FAIL: pull with failing state write exited 0"; exit 1
fi
grep -q 'pulled into' "$tmp/pull-test-Y.log" \
  && { echo "FAIL: failed pull reported success"; exit 1; }

# with neither AGENTS_CONFIG_DIR nor HOME, the failure names the fix —
# not "HOME: unbound variable" (#253)
if env -u HOME -u AGENTS_CONFIG_DIR bash "$PULL" --global --dry-run >"$tmp/pull-test-Z.log" 2>&1; then
  echo "FAIL: rootless global run exited 0"; exit 1
fi
grep -q 'AGENTS_CONFIG_DIR or HOME' "$tmp/pull-test-Z.log" \
  || { echo "FAIL: no actionable root error"; cat "$tmp/pull-test-Z.log"; exit 1; }

# the delegation must survive an empty flags array on bash < 4.4, where an
# unguarded "${flags[@]}" under `set -u` is a fatal unbound-variable error
# (stock macOS bash is 3.2). No old bash runs here, so pin the guarded
# expansion shape; every flagless fresh pull above already covers the
# zero-flags behavior on modern bash (#251)
grep -q 'flags\[@]+"${flags\[@\]}"' "$PULL" \
  || { echo "FAIL: delegation expands a possibly-empty array unguarded"; exit 1; }

echo "PASS"
