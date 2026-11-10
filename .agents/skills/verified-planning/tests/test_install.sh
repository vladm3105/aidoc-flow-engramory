#!/usr/bin/env bash
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"   # the master skill dir
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
# Hermetic user scope: the self-heal removes a project SKILL.md only when a
# user-scope copy masks it (#213), so the default fixture provides one.
mkdir -p "$tmp/user/skills/verified-planning"
printf 'user skill\n' > "$tmp/user/skills/verified-planning/SKILL.md"
export AGENTS_CONFIG_DIR="$tmp/user"
git init -q "$tmp/repoA"
printf '.agents/\n' > "$tmp/repoA/.gitignore"   # a repo that ignores .agents/
base="$tmp/repoA/.agents/skills/verified-planning"

# seed the repo as an OLDER installer left it, so the run below exercises the
# self-heal path and not just a clean install
mkdir -p "$base"
printf 'stale skill, no circuit-breaker\n' > "$base/SKILL.md"
printf 'stale installer\n' > "$base/install.sh"

bash "$SRC/install.sh" "$tmp/repoA"

# the gate belongs in the repo — CI and the hook run from the checkout
for f in check_plan.py PLAN.md DESIGN.md PLAN-TEMPLATE.snippet.md README.md; do
  test -f "$base/$f" || { echo "FAIL: missing $f"; exit 1; }
done
test -f "$base/tests/test_check_plan.py" || { echo "FAIL: tests not copied"; exit 1; }
# the installer ships the reusable workflow; the caller is consumer-owned and
# never created here (#229)
test -f "$tmp/repoA/.github/workflows/plan-gate-reusable.yml" || { echo "FAIL: ci"; exit 1; }
test ! -e "$tmp/repoA/.github/workflows/plan-gate.yml" \
  || { echo "FAIL: installer created the consumer-owned caller"; exit 1; }
grep -q 'verified-planning skill must be committed' "$tmp/repoA/.gitignore" \
  || { echo "FAIL: gitignore not un-ignored"; exit 1; }
git -C "$tmp/repoA" check-ignore -q .agents/skills/verified-planning/check_plan.py \
  && { echo "FAIL: gate still ignored after install"; exit 1; }

# the installed workflow is pinned and hardened (#216): SHA pins, no persisted creds
wf="$tmp/repoA/.github/workflows/plan-gate-reusable.yml"
grep -Eq 'uses: actions/checkout@[0-9a-f]{40}' "$wf" \
  || { echo "FAIL: checkout floats (want SHA pin)"; exit 1; }
grep -Eq 'uses: actions/setup-python@[0-9a-f]{40}' "$wf" \
  || { echo "FAIL: setup-python floats (want SHA pin)"; exit 1; }
grep -q 'persist-credentials: false' "$wf" \
  || { echo "FAIL: checkout persists credentials"; exit 1; }

# the gate fails closed when the diff faults (#217) — and still exits 0 when
# nothing matches. Run the workflow's own run-block in the scratch repo.
awk '/^        run: \|/{flag=1;next} flag{sub(/^          /,"");print}' "$wf" > "$tmp/gate-run.sh"
grep -q 'git diff' "$tmp/gate-run.sh" || { echo "FAIL: run-block extraction broke"; exit 1; }
git -C "$tmp/repoA" -c user.email=t@t -c user.name=t commit -q --allow-empty -m seed
sed 's|\${{ github.event.pull_request.base.sha }}|bogus00000000000000000000000000000000000000|' \
  "$tmp/gate-run.sh" > "$tmp/gate-bogus.sh"
if (cd "$tmp/repoA" && bash "$tmp/gate-bogus.sh" >/dev/null 2>&1); then
  echo "FAIL: gate passed on a faulted diff"; exit 1
fi
sed "s|\${{ github.event.pull_request.base.sha }}|$(git -C "$tmp/repoA" rev-parse HEAD)|" \
  "$tmp/gate-run.sh" > "$tmp/gate-clean.sh"
(cd "$tmp/repoA" && bash "$tmp/gate-clean.sh" >/dev/null 2>&1) \
  || { echo "FAIL: gate faults on the no-match path"; exit 1; }
# a deleted PLAN file is not gated: the diff excludes deletions, so removing
# an obsolete plan reads as no-change instead of crashing the gate (#230)
git -C "$tmp/repoA" -c user.email=t@t -c user.name=t add -A >/dev/null 2>&1
printf '## Claim ledger\n\n## Review log\n' > "$tmp/repoA/PLAN-004_old.md"
git -C "$tmp/repoA" -c user.email=t@t -c user.name=t add PLAN-004_old.md >/dev/null 2>&1
git -C "$tmp/repoA" -c user.email=t@t -c user.name=t commit -qm 'add plan' >/dev/null 2>&1
git -C "$tmp/repoA" rm -q PLAN-004_old.md >/dev/null 2>&1
git -C "$tmp/repoA" -c user.email=t@t -c user.name=t commit -qm 'drop plan' >/dev/null 2>&1
sed "s|\${{ github.event.pull_request.base.sha }}|$(git -C "$tmp/repoA" rev-parse HEAD~1)|" \
  "$tmp/gate-run.sh" > "$tmp/gate-del.sh"
(cd "$tmp/repoA" && bash "$tmp/gate-del.sh" >"$tmp/gate-del.log" 2>&1) \
  || { echo "FAIL: gate faults on a deleted plan"; cat "$tmp/gate-del.log"; exit 1; }
grep -q 'no gated PLAN files changed' "$tmp/gate-del.log" \
  || { echo "FAIL: deleted plan treated as gated change"; cat "$tmp/gate-del.log"; exit 1; }
# the run block declares its shell and lists with a portable loop: a forked
# workflow retargeted off bash must fail loudly, never half-run (#238)
grep -q 'shell: bash' "$wf" \
  || { echo "FAIL: run block has no shell guard"; exit 1; }
! grep -q 'mapfile -t' "$wf" \
  || { echo "FAIL: run block still needs bash 4+ mapfile"; exit 1; }
# the loop rewrite stays covered: a real PLAN change must flow through the
# extracted block into the gate and pass
mkdir -p "$tmp/repoA/src"
printf 'x\n%.0s' 1 2 3 4 5 6 7 8 9 10 > "$tmp/repoA/src/loop.py"
printf 'append_event(task_completed)\n' >> "$tmp/repoA/src/loop.py"
printf '## Claim ledger\n\n| # | Claim | Symbol | Citation |\n|---|---|---|---|\n| 1 | completion is logged | `task_completed` | src/loop.py:11 |\n\n## Review log\n\n### Pass 1 - 2026-06-07\n- self review.\n\n### Pass 2 - 2026-06-07 - independent\n- no new findings.\n' > "$tmp/repoA/PLAN-005.md"
git -C "$tmp/repoA" -c user.email=t@t -c user.name=t add -A >/dev/null 2>&1
git -C "$tmp/repoA" -c user.email=t@t -c user.name=t commit -qm 'add gated plan' >/dev/null 2>&1
sed "s|\${{ github.event.pull_request.base.sha }}|$(git -C "$tmp/repoA" rev-parse HEAD~1)|" \
  "$tmp/gate-run.sh" > "$tmp/gate-hit.sh"
(cd "$tmp/repoA" && bash "$tmp/gate-hit.sh" >"$tmp/gate-hit.log" 2>&1) \
  || { echo "FAIL: gate faults on a real PLAN change"; cat "$tmp/gate-hit.log"; exit 1; }
grep -q 'verified 1 citation' "$tmp/gate-hit.log" \
  || { echo "FAIL: PLAN change did not reach the gate"; cat "$tmp/gate-hit.log"; exit 1; }

# the skill does NOT — the user skill masks a same-name project SKILL.md, leaving
# a duplicate that can drift while appearing authoritative. install.sh must not
# travel either: a repo carrying its own copy can re-infect itself with a stale
# copy list. Both assertions are the point of this file.
test ! -e "$base/SKILL.md" \
  || { echo "FAIL: SKILL.md installed — it is masked by the user skill"; exit 1; }
test ! -e "$base/install.sh" \
  || { echo "FAIL: install.sh installed — re-infection path"; exit 1; }

# and the README that tells the next person why the directory looks incomplete
# Literal Markdown backticks are intentional.
# shellcheck disable=SC2016
grep -q 'Do not add a `SKILL.md` here' "$base/README.md" \
  || { echo "FAIL: README missing the do-not-add warning"; exit 1; }
# the refresh line uses the $CANON convention, never the installer's own
# absolute path (which would publish username/layout on push) (#240)
grep -qF '$CANON/.agents/skills/verified-planning/install.sh' "$base/README.md" \
  || { echo 'FAIL: README refresh line not on the $CANON convention'; exit 1; }
! grep -qF "$SRC" "$base/README.md" \
  || { echo "FAIL: README embeds the installer's absolute path"; exit 1; }
grep 'Refresh this directory' "$base/README.md" | grep -qE '__SRC__|~/\.claude' \
  && { echo "FAIL: README refresh line has placeholder or hardcoded layout"; exit 1; }

# garbage in: refuse non-directories and non-git roots without creating anything
if bash "$SRC/install.sh" "$tmp/does-not-exist" >/dev/null 2>&1; then
  echo "FAIL: accepted a nonexistent dir"; exit 1
fi
mkdir -p "$tmp/notgit"
if bash "$SRC/install.sh" "$tmp/notgit" >/dev/null 2>&1; then
  echo "FAIL: accepted a non-git dir"; exit 1
fi
[ -e "$tmp/notgit/.agents" ] && { echo "FAIL: created dirs in refused repo"; exit 1; }

# consumer tweaks survive a re-run: differing files are skipped, exit 1
echo "# consumer tuning" >> "$tmp/repoA/.github/workflows/plan-gate-reusable.yml"
echo "# consumer threshold" >> "$base/check_plan.py"
if bash "$SRC/install.sh" "$tmp/repoA" >/dev/null 2>&1; then
  echo "FAIL: re-run over differing files exited 0"; exit 1
fi
grep -q 'consumer tuning' "$tmp/repoA/.github/workflows/plan-gate-reusable.yml" \
  || { echo "FAIL: workflow clobbered"; exit 1; }
grep -q 'consumer threshold' "$base/check_plan.py" \
  || { echo "FAIL: gate file clobbered"; exit 1; }

# --force restores, --backup keeps what it replaced
bash "$SRC/install.sh" --force --backup "$tmp/bak" "$tmp/repoA" >/dev/null 2>&1 \
  || { echo "FAIL: --force run failed"; exit 1; }
cmp -s "$SRC/check_plan.py" "$base/check_plan.py" \
  || { echo "FAIL: --force did not restore gate file"; exit 1; }
grep -q 'consumer threshold' "$tmp/bak/repoA/.agents/skills/verified-planning/check_plan.py" \
  || { echo "FAIL: backup missing replaced content"; exit 1; }

# --dry-run changes nothing
git init -q "$tmp/repoB"
bash "$SRC/install.sh" --dry-run "$tmp/repoB" >/dev/null 2>&1 \
  || { echo "FAIL: --dry-run failed"; exit 1; }
[ -e "$tmp/repoB/.agents" ] && { echo "FAIL: --dry-run created files"; exit 1; }
[ -e "$tmp/repoB/.github" ] && { echo "FAIL: --dry-run created files"; exit 1; }

# ignore-variant coverage: `.agents/*` hides the gate exactly like `.agents/`
git init -q "$tmp/repoC"
printf '.agents/*\n' > "$tmp/repoC/.gitignore"
bash "$SRC/install.sh" "$tmp/repoC" >/dev/null 2>&1 \
  || { echo "FAIL: install into .agents/* repo failed"; exit 1; }
git -C "$tmp/repoC" check-ignore -q .agents/skills/verified-planning/check_plan.py \
  && { echo "FAIL: gate still ignored under .agents/* variant"; exit 1; }

# pre-existing marker plus a defeating later rule: the gate stays ignored and
# the run must say so (#185) instead of printing success silently
git init -q "$tmp/repoD"
printf '.agents/\n' > "$tmp/repoD/.gitignore"
printf '\n# verified-planning skill must be committed (overrides .agents/ ignore)\n!.agents/\n' >> "$tmp/repoD/.gitignore"
printf '*\n' >> "$tmp/repoD/.gitignore"   # draconian later rule defeats everything above
bash "$SRC/install.sh" "$tmp/repoD" >"$tmp/install-D.log" 2>&1 \
  || { echo "FAIL: defeated-ignore run failed"; exit 1; }
git -C "$tmp/repoD" check-ignore -q .agents/skills/verified-planning/check_plan.py \
  || { echo "FAIL: fixture does not defeat the re-include block"; exit 1; }
grep -q 'still ignored' "$tmp/install-D.log" \
  || { echo "FAIL: no still-ignored warning"; exit 1; }

# self-heal removes the project SKILL.md only when a user-scope copy masks
# it; with no user copy the file is kept with a warning (#213)
git init -q "$tmp/repoF"
mkdir -p "$tmp/repoF/.agents/skills/verified-planning"
printf 'project skill\n' > "$tmp/repoF/.agents/skills/verified-planning/SKILL.md"
AGENTS_CONFIG_DIR="$tmp/nouser" bash "$SRC/install.sh" "$tmp/repoF" >"$tmp/install-F.log" 2>&1 \
  || { echo "FAIL: no-user-scope run failed"; exit 1; }
test -f "$tmp/repoF/.agents/skills/verified-planning/SKILL.md" \
  || { echo "FAIL: project SKILL.md deleted with no user copy to mask it"; exit 1; }
grep -q 'no user-scope copy' "$tmp/install-F.log" \
  || { echo "FAIL: no kept-with-warning note"; exit 1; }
mkdir -p "$tmp/nouser/skills/verified-planning"
printf 'user skill\n' > "$tmp/nouser/skills/verified-planning/SKILL.md"
AGENTS_CONFIG_DIR="$tmp/nouser" bash "$SRC/install.sh" "$tmp/repoF" >/dev/null 2>&1 \
  || { echo "FAIL: user-scope run failed"; exit 1; }
test ! -e "$tmp/repoF/.agents/skills/verified-planning/SKILL.md" \
  || { echo "FAIL: masked SKILL.md not removed"; exit 1; }

# the installer works from a path with sed-active characters: since #240 the
# refresh line names the $CANON convention, so `&` in the canon path has
# nothing left to corrupt — the install itself must still succeed
rm -rf "$tmp/R&D" && mkdir -p "$tmp/R&D"
cp -r "$SRC" "$tmp/R&D/skill"
git init -q "$tmp/repoG"
bash "$tmp/R&D/skill/install.sh" "$tmp/repoG" >/dev/null 2>&1 \
  || { echo "FAIL: amp-path install failed"; exit 1; }

# --no-ci skips the GitHub workflow for other forges, but still installs the
# gate itself (#214)
git init -q "$tmp/repoH"
bash "$SRC/install.sh" --no-ci "$tmp/repoH" >/dev/null 2>&1 \
  || { echo "FAIL: --no-ci run failed"; exit 1; }
test ! -e "$tmp/repoH/.github/workflows/plan-gate-reusable.yml" \
  || { echo "FAIL: --no-ci installed the workflow"; exit 1; }
test -f "$tmp/repoH/.agents/skills/verified-planning/check_plan.py" \
  || { echo "FAIL: --no-ci skipped the gate"; exit 1; }

# self-mask is no mask: when user scope IS the repo, the guard file is the
# target itself, so the project SKILL.md must be kept, not deleted (#232)
git init -q "$tmp/repoI"
mkdir -p "$tmp/repoI/.agents/skills/verified-planning"
printf 'project skill\n' > "$tmp/repoI/.agents/skills/verified-planning/SKILL.md"
AGENTS_CONFIG_DIR="$tmp/repoI/.agents" bash "$SRC/install.sh" "$tmp/repoI" >"$tmp/install-I.log" 2>&1 \
  || { echo "FAIL: self-scope run failed"; exit 1; }
test -f "$tmp/repoI/.agents/skills/verified-planning/SKILL.md" \
  || { echo "FAIL: project SKILL.md deleted under self-scope"; exit 1; }
grep -q 'user scope is the repo itself' "$tmp/install-I.log" \
  || { echo "FAIL: no self-scope warning"; exit 1; }

# an old-canon caller is never touched, and the run says how to migrate (#229)
git init -q "$tmp/repoL"
mkdir -p "$tmp/repoL/.github/workflows"
printf 'name: plan-gate\njobs:\n  check-plan:\n    steps:\n      - name: Gate changed PLAN files\n' > "$tmp/repoL/.github/workflows/plan-gate.yml"
bash "$SRC/install.sh" "$tmp/repoL" >"$tmp/install-L.log" 2>&1 \
  || { echo "FAIL: migration-notice run failed"; exit 1; }
grep -q 'Gate changed PLAN files' "$tmp/repoL/.github/workflows/plan-gate.yml" \
  || { echo "FAIL: consumer caller modified"; exit 1; }
grep -qi 'migrate' "$tmp/install-L.log" \
  || { echo "FAIL: no migration notice"; exit 1; }

# adversarial filenames survive install: the tests copy is NUL-delimited,
# so even a newline in a name cannot split it mid-copy (#240)
rm -rf "$tmp/nl" && mkdir -p "$tmp/nl"
cp -r "$SRC" "$tmp/nl/skill"
touch "$tmp/nl/skill/tests/"$'we\nird.sh'
git init -q "$tmp/repoK"
bash "$tmp/nl/skill/install.sh" "$tmp/repoK" >/dev/null 2>&1 \
  || { echo "FAIL: newline-name install failed"; exit 1; }
test -f "$tmp/repoK/.agents/skills/verified-planning/tests/"$'we\nird.sh' \
  || { echo "FAIL: newline-name test file not installed"; exit 1; }

# self-heal deletions stage into --backup like forced replacements (#234)
git init -q "$tmp/repoJ"
mkdir -p "$tmp/repoJ/.agents/skills/verified-planning"
printf 'stale skill\n' > "$tmp/repoJ/.agents/skills/verified-planning/SKILL.md"
printf 'stale installer\n' > "$tmp/repoJ/.agents/skills/verified-planning/install.sh"
bash "$SRC/install.sh" --force --backup "$tmp/ibak" "$tmp/repoJ" >/dev/null 2>&1 \
  || { echo "FAIL: backup self-heal run failed"; exit 1; }
test -f "$tmp/ibak/repoJ/.agents/skills/verified-planning/SKILL.md" \
  || { echo "FAIL: deleted SKILL.md not backed up"; exit 1; }
test -f "$tmp/ibak/repoJ/.agents/skills/verified-planning/install.sh" \
  || { echo "FAIL: deleted install.sh not backed up"; exit 1; }

# air-gapped callers can skip the toolchain download; push-triggered callers
# get a specific error instead of a generic diff failure (#254)
wf254="$tmp/repoA/.github/workflows/plan-gate-reusable.yml"
grep -q '^      setup-python:$' "$wf254" \
  || { echo "FAIL: no setup-python input"; exit 1; }
grep -q 'if: inputs.setup-python' "$wf254" \
  || { echo "FAIL: setup step not conditional"; exit 1; }
grep -q 'PR callers only' "$wf254" \
  || { echo "FAIL: no push-caller error"; exit 1; }

# a shared --backup must not collide across repos: each repo's originals
# land under their own subdirectory (#253)
git init -q "$tmp/repoM"; git init -q "$tmp/repoN"
bash "$SRC/install.sh" "$tmp/repoM" "$tmp/repoN" >/dev/null 2>&1 \
  || { echo "FAIL: repoM/repoN fresh install failed"; exit 1; }
printf 'M-original\n' >> "$tmp/repoM/.agents/skills/verified-planning/check_plan.py"
printf 'N-original\n' >> "$tmp/repoN/.agents/skills/verified-planning/check_plan.py"
bash "$SRC/install.sh" --force --backup "$tmp/ibakMN" "$tmp/repoM" "$tmp/repoN" >/dev/null 2>&1 \
  || { echo "FAIL: forced multi-repo backup run failed"; exit 1; }
grep -q 'M-original' "$tmp/ibakMN/repoM/.agents/skills/verified-planning/check_plan.py" \
  || { echo "FAIL: repoM original lost to backup collision"; exit 1; }
grep -q 'N-original' "$tmp/ibakMN/repoN/.agents/skills/verified-planning/check_plan.py" \
  || { echo "FAIL: repoN original lost to backup collision"; exit 1; }

# a failed copy reports failure, never success (#253)
git init -q "$tmp/repoO"
bash "$SRC/install.sh" "$tmp/repoO" >/dev/null 2>&1 \
  || { echo "FAIL: repoO fresh install failed"; exit 1; }
rm "$tmp/repoO/.agents/skills/verified-planning/check_plan.py"
# A dangling symlink into a missing directory: `-e` reports it absent, mkdir -p of the
# real parent succeeds, and cp then fails writing through it — even as root, where a
# chmod-based fixture would silently succeed.
ln -s "$tmp/no-such-dir/check_plan.py" "$tmp/repoO/.agents/skills/verified-planning/check_plan.py"
if bash "$SRC/install.sh" "$tmp/repoO" >"$tmp/install-O.log" 2>&1; then
  echo "FAIL: install with failing copy exited 0"; exit 1
fi
rm "$tmp/repoO/.agents/skills/verified-planning/check_plan.py"
grep -q 'installed into' "$tmp/install-O.log" \
  && { echo "FAIL: failed install reported success"; exit 1; }

# one bad repo does not skip the rest: each is attempted, failures named,
# exit 2 at the end (#253)
git init -q "$tmp/repoP"
git init -q "$tmp/repoQ"
if bash "$SRC/install.sh" "$tmp/repoP" "$tmp/does-not-exist" "$tmp/repoQ" >"$tmp/install-PQ.log" 2>&1; then
  echo "FAIL: multi-repo run with bad path exited 0"; exit 1
fi
test -f "$tmp/repoP/.agents/skills/verified-planning/check_plan.py" \
  || { echo "FAIL: repo before bad path not installed"; exit 1; }
test -f "$tmp/repoQ/.agents/skills/verified-planning/check_plan.py" \
  || { echo "FAIL: repo after bad path not installed"; exit 1; }
grep -q 'does-not-exist' "$tmp/install-PQ.log" \
  || { echo "FAIL: bad repo not named"; exit 1; }

# with neither AGENTS_CONFIG_DIR nor HOME, the failure names the fix —
# not "HOME: unbound variable" (#253)
git init -q "$tmp/repoR"
bash "$SRC/install.sh" "$tmp/repoR" >/dev/null 2>&1 \
  || { echo "FAIL: repoR fresh install failed"; exit 1; }
printf 'project skill\n' > "$tmp/repoR/.agents/skills/verified-planning/SKILL.md"
if env -u HOME -u AGENTS_CONFIG_DIR bash "$SRC/install.sh" "$tmp/repoR" >"$tmp/install-R.log" 2>&1; then
  echo "FAIL: rootless install run exited 0"; exit 1
fi
grep -q 'AGENTS_CONFIG_DIR or HOME' "$tmp/install-R.log" \
  || { echo "FAIL: no actionable root error"; cat "$tmp/install-R.log"; exit 1; }

echo "PASS"
