import contextlib
import importlib.util
import io
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_plan.py"

# Load the gate in-process (no subprocess: faster, and avoids a security-linter
# flag for shelling out from a test).
_spec = importlib.util.spec_from_file_location("check_plan_under_test", SCRIPT)
_cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cp)


class _Result:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _cp.main([str(a) for a in args])
    return _Result(rc, buf.getvalue())


def _repo(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".git").mkdir()
    src = tmp_path / "src"
    src.mkdir()
    (src / "loop.py").write_text(
        "\n".join(f"line{n}" for n in range(1, 11)) + "\nappend_event(task_completed)\n"
    )  # line 11
    return tmp_path


LEDGER_HEADER = "## Claim ledger\n\n| # | Claim | Symbol | Citation |\n|---|---|---|---|\n"
REVIEW_OK = (
    "## Review log\n\n"
    "### Pass 1 - 2026-06-07\n- self review.\n\n"
    "### Pass 2 - 2026-06-07 - independent\n- no new findings.\n"
)
GOOD_ROW = "| 1 | completion is logged | `task_completed` | src/loop.py:11 |\n"


def _plan(repo, ledger_rows, review=REVIEW_OK, name="PLAN-001_x.md"):
    p = repo / name
    p.write_text(LEDGER_HEADER + ledger_rows + "\n" + review)
    return p


def test_resolved_citation_passes(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW)
    r = _run(plan)
    assert r.returncode == 0, r.stdout + r.stderr


def test_missing_file_fails(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, "| 1 | x | `foo` | src/nope.py:3 |\n")
    r = _run(plan)
    assert r.returncode == 1
    assert "does not exist" in r.stdout


def test_directory_citation_fails_gracefully(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, "| 1 | x | `src` | src:1 |\n")
    r = _run(plan)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "is not a file" in r.stdout
    assert "Traceback" not in r.stderr


def test_line_out_of_range_with_symbol_warns(tmp_path):
    # PLAN-051: out-of-range line + symbol present elsewhere → warn (rc 0), the symbol is authoritative.
    repo = _repo(tmp_path)
    plan = _plan(repo, "| 1 | x | `task_completed` | src/loop.py:999 |\n")
    r = _run(plan)
    assert r.returncode == 0, r.stdout
    assert "drifted" in r.stdout and "warn" in r.stdout


def test_drifted_symbol_warns(tmp_path):
    # PLAN-051: symbol present but not at the cited line → warn (rc 0), not a failure.
    repo = _repo(tmp_path)
    plan = _plan(repo, "| 1 | x | `task_completed` | src/loop.py:1 |\n")
    r = _run(plan)
    assert r.returncode == 0, r.stdout
    assert "drifted" in r.stdout and "warn" in r.stdout  # the warn line is emitted


def test_symbol_genuinely_absent_fails(tmp_path):
    # A symbol that appears nowhere in the file is a real, still-failing error.
    repo = _repo(tmp_path)
    plan = _plan(repo, "| 1 | x | `never_appears_anywhere` | src/loop.py:1 |\n")
    r = _run(plan)
    assert r.returncode == 1 and "not found in" in r.stdout


def test_no_symbol_out_of_range_fails(tmp_path):
    # No symbol to anchor on + out-of-range line → still an error (nothing to resolve against).
    repo = _repo(tmp_path)
    plan = _plan(repo, "| 1 | x |  | src/loop.py:999 |\n")
    r = _run(plan)
    assert r.returncode == 1 and "out of range" in r.stdout


def test_no_symbol_in_range_passes(tmp_path):
    # No symbol + in-range line passes unchanged (the line is the only check).
    repo = _repo(tmp_path)
    plan = _plan(repo, "| 1 | x |  | src/loop.py:5 |\n")
    r = _run(plan)
    assert r.returncode == 0, r.stdout


def test_fix_repoints_unambiguous_drift(tmp_path):
    # --fix re-points a drifted, single-occurrence citation, in place, per-row.
    repo = _repo(tmp_path)
    plan = _plan(repo, "| 1 | x | `task_completed` | src/loop.py:1 |\n")
    r = _run("--fix", plan)
    assert r.returncode == 0, r.stdout
    assert "re-pointed 1" in r.stdout
    assert "src/loop.py:11" in plan.read_text()  # 1 → 11, the real line
    r2 = _run(plan)  # now precise — no drift
    assert r2.returncode == 0 and "drifted" not in r2.stdout


def test_ambiguous_drift_warns_and_fix_leaves_it(tmp_path):
    # A symbol with multiple occurrences is ambiguous → warns, and --fix does NOT rewrite it.
    repo = _repo(tmp_path)
    (repo / "src" / "dup.py").write_text(
        "\n".join(["x"] * 4 + ["dup_sym"] + ["x"] * 10 + ["dup_sym"]) + "\n"
    )
    plan = _plan(repo, "| 1 | x | `dup_sym` | src/dup.py:1 |\n")
    before = plan.read_text()
    r = _run("--fix", plan)
    assert r.returncode == 0 and "re-pointed" not in r.stdout
    assert plan.read_text() == before  # unchanged
    r2 = _run(plan)
    assert r2.returncode == 0 and "drifted" in r2.stdout


def test_unverified_row_fails(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, "| 1 | x | `foo` | UNVERIFIED |\n")
    r = _run(plan)
    assert r.returncode == 1 and "UNVERIFIED" in r.stdout


def test_empty_ledger_fails(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, "")
    r = _run(plan)
    assert r.returncode == 1 and "no rows" in r.stdout


REVIEW_SINGLE_ENTRY = "## Review log\n\n### Pass 1 - 2026-06-07\n- only one pass.\n"
REVIEW_NO_INDEP = "## Review log\n\n### Pass 1 - 2026-06-07\n- self.\n\n### Pass 2 - 2026-06-07\n- self again, no new findings.\n"
REVIEW_NOT_GREEN = (
    "## Review log\n\n### Pass 1 - 2026-06-07\n- self.\n\n"
    "### Pass 2 - 2026-06-07 - independent\n- found a bug, unfixed.\n"
)


def test_fewer_than_two_passes_fails(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW, review=REVIEW_SINGLE_ENTRY)
    r = _run(plan)
    assert r.returncode == 1 and "at least two" in r.stdout


def test_no_independent_pass_fails(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW, review=REVIEW_NO_INDEP)
    r = _run(plan)
    assert r.returncode == 1 and "independent" in r.stdout


def test_final_pass_not_green_fails(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW, review=REVIEW_NOT_GREEN)
    r = _run(plan)
    assert r.returncode == 1 and "zero-findings" in r.stdout


REVIEW_DENIAL_IN_BODY = (
    "## Review log\n\n### Pass 1 - 2026-06-07\n- self.\n\n"
    "### Pass 2 - 2026-06-07\n- still needs independent review before ready.\n"
)
REVIEW_FALLBACK_HEADER = (
    "## Review log\n\n### Pass 1 - 2026-06-07\n- self.\n\n"
    "### Pass 2 - 2026-06-07 - fallback\n- no new findings.\n"
)
REVIEW_NON_INDEPENDENT_HEADER = (
    "## Review log\n\n### Pass 1 - 2026-06-07\n- self.\n\n"
    "### Pass 2 - 2026-06-07 - non-independent (self)\n- no new findings.\n"
)


def test_independence_denial_in_body_does_not_satisfy(tmp_path):
    # the word "independent" in pass prose is not a marked pass (#207)
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW, review=REVIEW_DENIAL_IN_BODY)
    r = _run(plan)
    assert r.returncode == 1 and "no pass marked" in r.stdout


def test_fallback_header_satisfies_independence(tmp_path):
    # SKILL.md:121 — `- fallback` marks the logged-fallback pass (#207)
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW, review=REVIEW_FALLBACK_HEADER)
    r = _run(plan)
    assert r.returncode == 0, r.stdout


def test_non_independent_header_does_not_satisfy(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW, review=REVIEW_NON_INDEPENDENT_HEADER)
    r = _run(plan)
    assert r.returncode == 1 and "no pass marked" in r.stdout


def test_green_rejects_nothing_further_with_open_findings(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(
        repo, GOOD_ROW, review=_review("nothing further to add, but 3 findings remain open")
    )
    r = _run(plan)
    assert r.returncode == 1 and "zero-findings" in r.stdout


def test_green_rejects_finds_nothing_with_open_findings(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW, review=_review("finds nothing conclusive, 2 remain"))
    r = _run(plan)
    assert r.returncode == 1 and "zero-findings" in r.stdout


def test_green_accepts_nothing_further_found(tmp_path):
    # guard against over-correction: the findings-object phrasing stays green
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW, review=_review("nothing further found"))
    r = _run(plan)
    assert r.returncode == 0, r.stdout


def test_full_valid_plan_passes(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW)
    r = _run(plan)
    assert r.returncode == 0, r.stdout


def test_non_plan_markdown_skipped(tmp_path):
    repo = _repo(tmp_path)
    p = repo / "PLAN-README.md"
    p.write_text("# just notes\n\nNo ledger, no review log here.\n")
    r = _run(p)
    assert r.returncode == 0 and "ok" in r.stdout


def test_missing_review_when_ledger_present_fails(tmp_path):
    repo = _repo(tmp_path)
    p = repo / "PLAN-002.md"
    p.write_text(LEDGER_HEADER + GOOD_ROW)
    r = _run(p)
    assert r.returncode == 1 and "Review log" in r.stdout


# --- Improvement 1: robust green-final detection ---


def _review(final_line: str) -> str:
    return f"## Review log\n\n### Pass 1 - 2026-06-07\n- self.\n\n### Pass 2 - 2026-06-07 - independent\n- {final_line}\n"


def test_green_accepts_load_bearing_phrase(tmp_path):
    # the exact false-negative we hit: "no load-bearing findings" must pass
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW, review=_review("No load-bearing findings. Ready."))
    r = _run(plan)
    assert r.returncode == 0, r.stdout


def test_green_accepts_explicit_result_marker(tmp_path):
    # an explicit Result: ready marker passes even without a "findings" phrase
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW, review=_review("**Result:** ready"))
    r = _run(plan)
    assert r.returncode == 0, r.stdout


def test_green_still_rejects_open_finding(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW, review=_review("found an unfixed bug; not ready"))
    r = _run(plan)
    assert r.returncode == 1 and "zero-findings" in r.stdout


def test_green_does_not_falsematch_no_fix_for_findings(tmp_path):
    # the broadened matcher must NOT mark a pass with open findings green
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW, review=_review("no fix for these findings yet"))
    r = _run(plan)
    assert r.returncode == 1 and "zero-findings" in r.stdout


# --- Improvement 2: cross-repo citation support (--root) ---


def _cross_repo(tmp_path):
    # parent/repo (the plan's repo, has .git) + parent/sib (a sibling repo)
    repo = _repo(tmp_path / "repo")
    sib = tmp_path / "sib"
    sib.mkdir()
    (sib / "contract.yaml").write_text("kind: contract\n")
    plan = _plan(repo, "| 1 | sibling contract | `contract` | sib/contract.yaml:1 |\n")
    return plan


def test_cross_repo_citation_fails_without_root(tmp_path):
    plan = _cross_repo(tmp_path)  # repo_root=parent/repo; repo/sib does not exist
    r = _run(plan)
    assert r.returncode == 1 and "does not exist" in r.stdout


def test_cross_repo_citation_resolves_with_root(tmp_path):
    plan = _cross_repo(tmp_path)
    r = _run("--root", tmp_path, plan)  # parent/sib/contract.yaml resolves
    assert r.returncode == 0, r.stdout + r.stderr


# --- Improvement 3: positive output + --init ---


def test_success_prints_verified_count(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW)
    r = _run(plan)
    assert r.returncode == 0
    assert "verified 1" in r.stdout and "pass" in r.stdout


def test_init_scaffolds_sections(tmp_path):
    repo = _repo(tmp_path)
    p = repo / "PLAN-007_new.md"
    p.write_text("# New plan\n\nSome body.\n")
    r = _run("--init", p)
    assert r.returncode == 0
    text = p.read_text()
    assert "## Claim ledger" in text and "## Review log" in text


def test_init_is_idempotent(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo, GOOD_ROW)  # already has both sections
    before = plan.read_text()
    r = _run("--init", plan)
    assert r.returncode == 0
    assert plan.read_text() == before  # unchanged


def test_init_adds_ledger_without_duplicating_review(tmp_path):
    # plan has a Review log (with real passes) but no ledger → add ledger only,
    # keep the single existing Review log and its content
    repo = _repo(tmp_path)
    p = repo / "PLAN-008_x.md"
    p.write_text("# Plan\n\n" + REVIEW_OK)
    r = _run("--init", p)
    assert r.returncode == 0
    text = p.read_text()
    assert "## Claim ledger" in text
    assert text.count("## Review log") == 1  # not duplicated
    assert "no new findings" in text  # author's existing pass preserved


def test_init_adds_review_when_only_ledger(tmp_path):
    repo = _repo(tmp_path)
    p = repo / "PLAN-009_x.md"
    p.write_text(LEDGER_HEADER + GOOD_ROW)  # ledger, no review log
    r = _run("--init", p)
    assert r.returncode == 0
    text = p.read_text()
    assert "## Review log" in text
    assert text.count("## Claim ledger") == 1  # not duplicated


def test_ledger_row_is_reported_by_its_hash_column_not_its_position(tmp_path):
    """A split ledger is not contiguous 1..N, and the `#` column is what authors cite.

    Reporting the position is a second numbering system for one table: a warning reading
    `ledger row 2` would name the row whose `#` column says 52, and every in-text
    "ledger row N" reference in a plan uses the `#` column.
    """
    repo = _repo(tmp_path)
    plan = _plan(
        repo,
        "| 51 | first | `task_completed` | src/loop.py:11 |\n"
        "| 52 | second | `absent_symbol` | src/loop.py:3 |\n",
    )
    r = _run(plan)
    assert r.returncode == 1
    assert "ledger row 52:" in r.stdout, r.stdout
    # POSITIVE CONTROL. Without it this passes against a gate that labels every row 52,
    # and against one that happens to print the string anywhere for any reason.
    assert "ledger row 2:" not in r.stdout, r.stdout


def test_a_table_without_a_hash_column_still_reports_by_position(tmp_path):
    """The fallback, asserted rather than assumed.

    A table declaring no `#` column has no author-visible numbering to honour, so position is
    the only label available — and that path must not regress while the other one changes.
    """
    repo = _repo(tmp_path)
    plan = repo / "PLAN-002_x.md"
    plan.write_text(
        "## Claim ledger\n\n| Claim | Symbol | Citation |\n|---|---|---|\n"
        "| only row | `absent_symbol` | src/loop.py:3 |\n\n" + REVIEW_OK
    )
    r = _run(plan)
    assert r.returncode == 1
    # ROW **1**: the header is SKIPPED (an unnumbered header followed by a
    # separator row is not a claim), so the data row reports by position 1.
    # Before the #177 fix the header gated as data and this asserted row 2;
    # the row-1 assertion now tests the position fallback directly, with no
    # header error in the way.
    assert "ledger row 1: symbol 'absent_symbol' not found" in r.stdout, r.stdout


# --- PROBE: the claim state for something that cannot be settled from source ---
#
# WHY THIS EXISTS. Before it, a ledger row had two options: a real citation, or
# UNVERIFIED (fatal). A claim whose answer is a LIVE fact — a server-side
# setting, an API behaviour, a runtime observation — fits neither, so authors
# wrote prose instead. Measured consequence: one plan answered the same
# unmeasurable question three different ways across three review passes, each
# fold retracting the last. PROBE gives that claim somewhere legitimate to sit.
#
# It is deliberately NOT a free pass, and these tests are the teeth.


PHASES = "\n## Phases\n\n### Phase A\n### Phase B\n"


def test_probe_with_command_and_blocks_passes(tmp_path):
    repo = _repo(tmp_path)
    row = (
        "| 1 | the bypass mechanism — blocks Phase B | `n/a` | PROBE: gh api repos/o/r/rulesets |\n"
    )
    r = _run(_plan(repo, row, review=REVIEW_OK + PHASES))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 probe(s)" in r.stdout, r.stdout


def test_probe_without_command_fails(tmp_path):
    # Otherwise "PROBE" is a way to dodge a citation you simply did not look up.
    repo = _repo(tmp_path)
    row = "| 1 | the bypass mechanism — blocks Phase B | `n/a` | PROBE |\n"
    r = _run(_plan(repo, row))
    assert r.returncode == 1
    assert "PROBE without a command" in r.stdout, r.stdout


def test_probe_that_does_not_say_what_it_blocks_fails(tmp_path):
    # An unmeasured claim that names no gated phase reads as merely unfinished,
    # which is how it gets folded into prose and guessed at.
    repo = _repo(tmp_path)
    row = "| 1 | the bypass mechanism is unclear | `n/a` | PROBE: gh api repos/o/r/rulesets |\n"
    r = _run(_plan(repo, row))
    assert r.returncode == 1
    assert "does not say what it BLOCKS" in r.stdout, r.stdout


def test_probe_is_not_treated_as_unverified(tmp_path):
    # The distinction is the whole point: UNVERIFIED means "you did not look";
    # PROBE means "looking at source cannot answer this".
    repo = _repo(tmp_path)
    row = "| 1 | live protection state — blocks Phase C | `n/a` | PROBE: gh api repos/o/r/branches/main/protection |\n"
    r = _run(_plan(repo, row))
    assert "UNVERIFIED" not in r.stdout, r.stdout


def test_probe_count_is_reported_so_growth_is_visible(tmp_path):
    repo = _repo(tmp_path)
    rows = (
        GOOD_ROW
        + "| 2 | a — blocks Phase A | `n/a` | PROBE: cmd one |\n"
        + "| 3 | b — blocks Phase B | `n/a` | PROBE: cmd two |\n"
    )
    r = _run(_plan(repo, rows, review=REVIEW_OK + PHASES))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "3 citation(s)" in r.stdout and "2 probe(s)" in r.stdout, r.stdout


def test_probe_blocking_a_phase_that_does_not_exist_fails(tmp_path):
    # THE AMENDMENT. A probe is admissible because it blocks a NAMED phase
    # rather than the plan — which only holds while that phase exists. Measured
    # on this rule's own first use: a scope cut deleted the named phase and left
    # the probe pointing at empty space, reading as instrumented while blocking
    # no work. Requiring a phase NAME was not enough; it must RESOLVE.
    repo = _repo(tmp_path)
    row = "| 1 | the bypass — blocks Phase Q | `n/a` | PROBE: gh api repos/o/r/rulesets |\n"
    r = _run(_plan(repo, row, review=REVIEW_OK + PHASES))
    assert r.returncode == 1
    assert "no such phase exists" in r.stdout.lower(), r.stdout


def test_probe_blocking_a_phase_id_resolves(tmp_path):
    # Phase IDs (B1, B2b) are as valid as "Phase C" and are the commoner form.
    repo = _repo(tmp_path)
    row = "| 1 | the bypass — blocks B1 | `n/a` | PROBE: gh api repos/o/r/rulesets |\n"
    body = REVIEW_OK + "\n## Phases\n\n- **B1. probe, then write**\n"
    r = _run(_plan(repo, row, review=body))
    assert r.returncode == 0, r.stdout + r.stderr


def test_probe_blocking_a_numeric_phase_that_does_not_exist_fails(tmp_path):
    # The template's own PROBE example writes "blocks Phase <N>" — the numeric
    # form — so a numeric token that resolves nowhere must fail, not skip.
    repo = _repo(tmp_path)
    row = "| 1 | latency gating — blocks Phase 9 | `n/a` | PROBE: curl localhost |\n"
    r = _run(_plan(repo, row, review=REVIEW_OK + PHASES))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no such phase exists" in r.stdout.lower(), r.stdout


def test_probe_blocking_a_numeric_phase_that_exists_passes(tmp_path):
    repo = _repo(tmp_path)
    row = "| 1 | latency gating — blocks Phase 9 | `n/a` | PROBE: curl localhost |\n"
    body = REVIEW_OK + "\n## Phases\n\n### Phase 9\n- measure.\n"
    r = _run(_plan(repo, row, review=body))
    assert r.returncode == 0, r.stdout + r.stderr


def test_probe_with_block_word_but_no_token_fails(tmp_path):
    # "blocks the migration" names no resolvable phase — failing open here
    # leaves the probe attached to nothing verifiable (#208).
    repo = _repo(tmp_path)
    row = "| 1 | migration risk — blocks the migration | `n/a` | PROBE: curl localhost |\n"
    r = _run(_plan(repo, row, review=REVIEW_OK + PHASES))
    assert r.returncode == 1
    assert "resolvable phase" in r.stdout, r.stdout


def test_probe_blocking_phase_mentioned_only_in_prose_fails(tmp_path):
    # Prose about a deleted phase is not a phase definition (#208).
    repo = _repo(tmp_path)
    row = "| 1 | the bypass — blocks Phase Q | `n/a` | PROBE: gh api repos/o/r/rulesets |\n"
    review = (
        "## Review log\n\n### Pass 1 - 2026-06-07\n- dropped Phase Q per scope cut.\n\n"
        "### Pass 2 - 2026-06-07 - independent\n- no new findings.\n"
    )
    r = _run(_plan(repo, row, review=review))
    assert r.returncode == 1
    assert "no such phase exists" in r.stdout.lower(), r.stdout


def test_probe_resolve_is_case_insensitive(tmp_path):
    # Captured case-insensitively, so it must resolve that way too (#208).
    repo = _repo(tmp_path)
    row = "| 1 | the bypass — blocks phase b | `n/a` | PROBE: gh api repos/o/r/rulesets |\n"
    r = _run(_plan(repo, row, review=REVIEW_OK + PHASES))
    assert r.returncode == 0, r.stdout + r.stderr


def test_probe_blocking_id_mentioned_unbolded_fails(tmp_path):
    # A bare ID resolves against a bold `**B…**` definition, not prose (#208).
    repo = _repo(tmp_path)
    row = "| 1 | the bypass — blocks B9 | `n/a` | PROBE: gh api repos/o/r/rulesets |\n"
    review = (
        "## Review log\n\n### Pass 1 - 2026-06-07\n- see B9 notes for context.\n\n"
        "### Pass 2 - 2026-06-07 - independent\n- no new findings.\n"
    )
    r = _run(_plan(repo, row, review=review))
    assert r.returncode == 1
    assert "no such phase exists" in r.stdout.lower(), r.stdout


UNNUMBERED_HEADER = "## Claim ledger\n\n| Claim | Symbol | Citation |\n|---|---|---|\n"


def _plan_unnumbered(repo, ledger_rows, review=REVIEW_OK, name="PLAN-002_x.md"):
    p = repo / name
    p.write_text(UNNUMBERED_HEADER + ledger_rows + "\n" + review)
    return p


def test_unnumbered_ledger_with_header_passes(tmp_path):
    # A header row is not a claim: gating it as data fails every conventional
    # unnumbered table on the literal word "Citation".
    repo = _repo(tmp_path)
    row = "| completion is logged | `task_completed` | src/loop.py:11 |\n"
    r = _run(_plan_unnumbered(repo, row))
    assert r.returncode == 0, r.stdout + r.stderr


def test_unnumbered_probe_reads_the_claim_cell(tmp_path):
    # Unnumbered rows have no # cell, so the claim is cells[0], not cells[1]:
    # a valid probe must not spuriously fail on its own symbol cell.
    repo = _repo(tmp_path)
    row = "| latency gating — blocks Phase B | `n/a` | PROBE: curl localhost |\n"
    r = _run(_plan_unnumbered(repo, row, review=REVIEW_OK + PHASES))
    assert r.returncode == 0, r.stdout + r.stderr


def test_unnumbered_probe_missing_blocks_fails(tmp_path):
    repo = _repo(tmp_path)
    row = "| latency gating is unclear | `n/a` | PROBE: curl localhost |\n"
    r = _run(_plan_unnumbered(repo, row))
    assert r.returncode == 1
    assert "does not say what it BLOCKS" in r.stdout, r.stdout


def test_mixed_numbered_and_unnumbered_tables_both_check(tmp_path):
    # The documented fallback: each table is labeled by its own numbering.
    repo = _repo(tmp_path)
    body = (
        LEDGER_HEADER
        + GOOD_ROW
        + "\nSome prose between tables.\n\n"
        + "| Claim | Symbol | Citation |\n|---|---|---|\n"
        + "| completion is logged | `task_completed` | src/loop.py:11 |\n"
    )
    p = repo / "PLAN-003_x.md"
    p.write_text(body + "\n" + REVIEW_OK)
    r = _run(p)
    assert r.returncode == 0, r.stdout + r.stderr


def test_absolute_citation_is_rejected_not_resolved(tmp_path):
    # An absolute citation would otherwise open files outside every root,
    # turning the gate into a file-existence oracle over PR-controlled text.
    repo = _repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    row = f"| 1 | outside file | `secret` | {outside}:1 |\n"
    r = _run(_plan(repo, row))
    assert r.returncode == 1
    assert "is absolute" in r.stdout, r.stdout


def test_dotdot_escape_is_rejected(tmp_path):
    # `..` that lands outside every root resolves to nothing — same oracle,
    # spelled relatively. The outside file EXISTS, so only containment (not
    # absence) can produce this error: pre-fix this test fails with rc 0.
    repo = _repo(tmp_path / "repo")
    (tmp_path / "outside.txt").write_text("secret\n")
    row = "| 1 | outside file | `secret` | ../outside.txt:1 |\n"
    r = _run(_plan(repo, row))
    assert r.returncode == 1
    assert "does not exist under any root" in r.stdout, r.stdout


def test_dotdot_contained_in_root_still_resolves(tmp_path):
    # Containment is judged by landing spot, not spelling: `sub/../x` inside
    # the root is a real citation and must keep working.
    repo = _repo(tmp_path)
    (repo / "sub").mkdir()
    row = "| 1 | completion is logged | `task_completed` | sub/../src/loop.py:11 |\n"
    r = _run(_plan(repo, row))
    assert r.returncode == 0, r.stdout + r.stderr


def test_single_cell_probe_row_errors_instead_of_crashing(tmp_path):
    # A one-cell PROBE row has no claim cell; the gate must report it, not
    # raise IndexError (which exits 1 by accident with a traceback).
    repo = _repo(tmp_path)
    plan = repo / "PLAN-002_probe.md"
    plan.write_text(
        "## Claim ledger\n\n| Citation |\n|----------|\n| PROBE: curl example.com |\n\n" + REVIEW_OK
    )
    r = _run(plan)
    assert r.returncode == 1
    assert "needs claim + citation cells" in r.stdout, r.stdout
    assert "Traceback" not in r.stdout + r.stderr


# --- Hardening batch (#213) ---


def test_position_resets_between_tables(tmp_path):
    # A split ledger labels each table by its own numbering: the unnumbered
    # table's first bad row is "ledger row 1", not a continuation count.
    repo = _repo(tmp_path)
    # NOTE: one section body, two pipe-tables — a repeated `## Claim ledger`
    # header would split sections instead of exercising multi-table labeling.
    ledger = (
        LEDGER_HEADER
        + GOOD_ROW
        + "\nA second, unnumbered table follows.\n\n"
        + "| Claim | Symbol | Citation |\n|---|---|---|\n"
        + "| bad claim | `missing_symbol_xyz` | src/loop.py:3 |\n"
    )
    p = repo / "PLAN-001_x.md"
    p.write_text(ledger + "\n" + REVIEW_OK)
    r = _run(p)
    assert r.returncode == 1
    assert "ledger row 1:" in r.stdout, r.stdout


def test_probe_command_containing_unverified_passes(tmp_path):
    # The UNVERIFIED check must not fire inside a well-formed probe (#213).
    repo = _repo(tmp_path)
    row = "| 1 | flags check — blocks Phase B | `n/a` | PROBE: check the unverified-flags endpoint |\n"
    r = _run(_plan(repo, row, review=REVIEW_OK + PHASES))
    assert r.returncode == 0, r.stdout + r.stderr


def test_empty_pipe_row_is_not_silently_skipped(tmp_path):
    # An all-empty pipe row is malformed data, not a separator (#213).
    repo = _repo(tmp_path)
    p = repo / "PLAN-001_x.md"
    p.write_text(LEDGER_HEADER + GOOD_ROW + "| | | | |\n\n" + REVIEW_OK)
    r = _run(p)
    assert r.returncode == 1
    assert "UNVERIFIED or missing citation" in r.stdout, r.stdout


def test_init_missing_snippet_exits_2(tmp_path, monkeypatch):
    # A standalone-copied gate fails as a usage error, not a traceback (#213).
    monkeypatch.setattr(_cp, "SNIPPET", tmp_path / "gone" / "PLAN-TEMPLATE.snippet.md")
    p = tmp_path / "PLAN-009.md"
    p.write_text("# notes\n")
    r = _run("--init", p)
    assert r.returncode == 2


def test_no_git_falls_back_to_plan_dir(tmp_path, monkeypatch):
    # Outside a checkout (tarball, stripped CI), citations anchor on the plan
    # directory — not the invocation directory (#213).
    d = tmp_path / "export"
    (d / "src").mkdir(parents=True)
    (d / "src" / "loop.py").write_text("x\n" * 10 + "append_event(task_completed)\n")
    plan = _plan(d, GOOD_ROW)
    monkeypatch.chdir(tmp_path)
    r = _run(plan)
    assert r.returncode == 0, r.stdout


# --- Pre-tag review fixes (#230, #231) ---


def test_missing_plan_file_exits_2_without_traceback(tmp_path, capsys):
    # A deleted PLAN (or a typo'd path) is a usage error, not a crash (#230).
    r = _run(tmp_path / "PLAN-004_old.md")
    assert r.returncode == 2
    assert "Traceback" not in r.stdout + capsys.readouterr().err


def test_probe_prefix_requires_bare_word_or_colon(tmp_path):
    # Real citations under PROBE*-named paths must not enter the probe branch.
    repo = _repo(tmp_path)
    (repo / "probes").mkdir()
    (repo / "probes" / "health.py").write_text("alive\n")
    row = "| 1 | health endpoint exists | `alive` | probes/health.py:1 |\n"
    r = _run(_plan(repo, row))
    assert r.returncode == 0, r.stdout + r.stderr


# --- Code polish batch (#240) ---


def test_duplicate_gated_header_errors(tmp_path):
    # A repeated ## Claim ledger must fail loudly: last-wins-silent would
    # skip the first copy's rows (including UNVERIFIED) without a word.
    repo = _repo(tmp_path)
    first = LEDGER_HEADER + "| 9 | hidden claim | `nope_missing` | src/loop.py:99 |\n"
    p = repo / "PLAN-001_x.md"
    p.write_text(first + "\n" + LEDGER_HEADER + GOOD_ROW + "\n" + REVIEW_OK)
    r = _run(p)
    assert r.returncode == 1
    assert "duplicate" in r.stdout.lower(), r.stdout


def test_fix_skips_duplicated_ledger(tmp_path):
    # --fix must not patch the ignored copy of a duplicated ledger: gating
    # is last-wins, rewriting is first-match. Skip loudly instead (#254).
    repo = _repo(tmp_path)
    drifted = "| 1 | x | `task_completed` | src/loop.py:1 |\n"
    p = repo / "PLAN-001_x.md"
    p.write_text(LEDGER_HEADER + drifted + "\n" + LEDGER_HEADER + drifted + "\n" + REVIEW_OK)
    before = p.read_text()
    r = _run("--fix", p)
    assert r.returncode == 1  # the duplicate error still fails
    assert "duplicate" in r.stdout.lower(), r.stdout
    assert p.read_text() == before  # unchanged — nothing patched
    assert "re-pointed" not in r.stdout
    assert "skipped" in r.stdout


def test_spaced_path_citation_resolves(tmp_path):
    # A real path with a space must resolve whole — not match a suffix.
    repo = _repo(tmp_path)
    (repo / "my dir").mkdir()
    (repo / "my dir" / "f.py").write_text("x\n" * 4 + "spaced_symbol\n")
    row = "| 1 | spaced file exists | `spaced_symbol` | my dir/f.py:5 |\n"
    r = _run(_plan(repo, row))
    assert r.returncode == 0, r.stdout + r.stderr


def test_prose_prefixed_citation_still_resolves(tmp_path):
    # Characterization: the space-inclusive match must not break prose
    # around a citation ("see src/loop.py:11") — the fallback resolves it.
    repo = _repo(tmp_path)
    row = "| 1 | completion is logged | `task_completed` | see src/loop.py:11 in review |\n"
    r = _run(_plan(repo, row))
    assert r.returncode == 0, r.stdout + r.stderr


# --- Wave 2 (PLAN-050): Windows/POSIX path hardening + opt-in v2 convergence gate ---

SHA_OK = "a" * 64
SHA_ALT = "b" * 64
SHA_ZERO = "0" * 64

TASK_HDR = (
    "## Task convergence\n\n"
    "| Task ID | SHA-256 | P1 | P2 | P3 | P4 | Total | Status |\n"
    "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |\n"
)


def _v2_plan(
    repo: Path,
    task_rows: str,
    *,
    ledger_rows: str = GOOD_ROW,
    review: str = REVIEW_OK,
    directive: str = "<!-- plan-gate: v2 -->\n",
    extra: str = "",
    name: str = "PLAN-099_v2.md",
) -> Path:
    p = repo / name
    p.write_text(directive + "\n" + LEDGER_HEADER + ledger_rows + "\n" + TASK_HDR + task_rows + "\n" + review + extra)
    return p


def test_windows_drive_relative_backtick_and_posix_root_citations_rejected(tmp_path):
    repo = _repo(tmp_path)
    for bad in (
        "C:outside.txt:1",
        "`C:outside.txt:1`",
        "`C:\\src\\loop.py:11`",
        "/outside.py:1",
        "`/outside.py:1`",
        "\\outside.py:1",
        "see /my dir/f.py:1",
        "\\\\server\\share\\secret.py:1",
        "//server/share/secret.py:1",
    ):
        plan = _plan(repo, f"| 1 | bad path | `task_completed` | {bad} |\n")
        r = _run(plan)
        assert r.returncode == 1, f"expected failure for {bad}: {r.stdout}"
        assert "is absolute" in r.stdout, f"expected 'is absolute' for {bad}: {r.stdout}"


def test_relative_backslash_and_phase_colon_prose_resolve(tmp_path):
    repo = _repo(tmp_path)
    for good in (
        "src\\loop.py:11",
        "Phase A: src/loop.py:11",
    ):
        plan = _plan(repo, f"| 1 | good path | `task_completed` | {good} |\n")
        r = _run(plan)
        assert r.returncode == 0, f"expected pass for {good}: {r.stdout + r.stderr}"


def test_v1_plan_quoting_v2_directive_in_prose_does_not_self_trigger(tmp_path):
    repo = _repo(tmp_path)
    p = repo / "PLAN-050_self.md"
    p.write_text(
        "<!-- plan-gate: v1 -->\n\n"
        "- Mentions `<!-- plan-gate: v2 -->` in prose and inside a fence:\n\n"
        "```markdown\n<!-- plan-gate: v2 -->\n## Review log\n```\n\n"
        + LEDGER_HEADER
        + GOOD_ROW
        + "\n"
        + REVIEW_OK
    )
    r = _run(p)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "health" not in r.stdout


def test_conflicting_v1_and_v2_directives_fail(tmp_path):
    repo = _repo(tmp_path)
    p = _v2_plan(
        repo,
        f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n",
        directive="<!-- plan-gate: v1 -->\n<!-- plan-gate: v2 -->\n",
    )
    r = _run(p)
    assert r.returncode == 1
    assert "conflicting standalone" in r.stdout


def test_v2_plan_without_ledger_or_review_is_not_skipped(tmp_path):
    repo = _repo(tmp_path)
    p = repo / "PLAN-099_empty_v2.md"
    p.write_text("<!-- plan-gate: v2 -->\n# Empty v2 plan\n")
    r = _run(p)
    assert r.returncode == 1
    assert "missing '## Claim ledger'" in r.stdout


def test_v2_all_locked_passes_with_health_output(tmp_path):
    repo = _repo(tmp_path)
    p = _v2_plan(
        repo,
        f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 5.0 | 18.5 | LOCKED |\n",
    )
    r = _run(p)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "health 92.5%" in r.stdout


def test_v2_cli_flag_activates_convergence_without_directive(tmp_path):
    repo = _repo(tmp_path)
    p = _v2_plan(
        repo,
        f"| T1 | `{SHA_OK}` | 4.0 | 4.0 | 4.0 | 4.0 | 16.0 | LOCKED |\n",
        directive="# Plan\n",
    )
    assert _run(p).returncode == 0  # v1 ignores convergence table
    r_v2 = _run("--v2", p)
    assert r_v2.returncode == 0, r_v2.stdout
    assert "health 80.0%" in r_v2.stdout


def test_v2_non_ready_zero_findings_still_rejects_unlocked_tasks(tmp_path):
    repo = _repo(tmp_path)
    p = _v2_plan(
        repo,
        f"| T1 | `{SHA_OK}` | 4.0 | 4.0 | 4.0 | 4.0 | 16.0 | FIX |\n",
        review=_review("**Result:** green"),
    )
    r = _run(p)
    assert r.returncode == 1
    assert "non-LOCKED" in r.stdout


def test_v2_rejects_bad_hash_zero_hash_score_threshold_duplicate_and_empty(tmp_path):
    repo = _repo(tmp_path)
    bad_cases = [
        ("", "has no rows"),
        (f"| T1 | `{SHA_ZERO}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n", "all-zero"),
        (f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n| T1 | `{SHA_ALT}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n", "duplicate Task ID"),
        (f"| T1 | `{SHA_OK}` | 4.0 | 4.0 | 4.0 | 3.5 | 15.5 | LOCKED |\n", "Total >= 16.0"),
        (f"| T1 | `{SHA_OK}` | 5.0 | 5.0 | 5.0 | 2.0 | 17.0 | LOCKED |\n", ">= 2.5"),
        (f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 19.0 | LOCKED |\n", "does not equal P1..P4 sum"),
    ]
    for rows, needle in bad_cases:
        p = _v2_plan(repo, rows)
        r = _run(p)
        assert r.returncode == 1, f"expected fail for {needle}: {r.stdout}"
        assert needle in r.stdout, f"missing {needle!r} in {r.stdout}"


def test_v2_rejects_dual_task_convergence_and_task_scores_headings(tmp_path):
    repo = _repo(tmp_path)
    extra = (
        "\n## Task scores\n\n"
        "| Task ID | SHA-256 | P1 | P2 | P3 | P4 | Total | Status |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |\n"
        f"| T2 | `{SHA_ALT}` | 1.0 | 1.0 | 1.0 | 1.0 | 4.0 | FIX |\n"
    )
    p = _v2_plan(
        repo,
        f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n",
        extra=extra,
    )
    r = _run(p)
    assert r.returncode == 1
    assert "duplicate" in r.stdout.lower()


def test_v2_ignores_fenced_headers_and_fenced_ready_marker(tmp_path):
    repo = _repo(tmp_path)
    review_fenced = (
        "## Review log\n\n"
        "### Pass 1 - 2026-06-07\n- self.\n\n"
        "### Pass 2 - 2026-06-07 - independent\n"
        "```markdown\n**Result:** ready — no further findings.\n```\n"
        "- open finding remains.\n"
    )
    p = _v2_plan(
        repo,
        f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n",
        review=review_fenced,
    )
    r = _run(p)
    assert r.returncode == 1
    assert "final Pass" in r.stdout


def test_v2_pass4_cap_escalate_and_malformed_disposition_fail(tmp_path):
    repo = _repo(tmp_path)
    review_4 = (
        "## Review log\n\n"
        "### Pass 1 - 2026-06-07\n- self.\n\n"
        "### Pass 2 - 2026-06-07 - independent\n- fix.\n\n"
        "### Pass 3 - 2026-06-07 - independent\n- fix.\n\n"
        "### Pass 4 - 2026-06-07 - independent\n- **Result:** ready — no further findings.\n"
    )
    r4 = _run(_v2_plan(repo, f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n", review=review_4))
    assert r4.returncode == 1 and "3 passes max" in r4.stdout

    review_esc = (
        "## Review log\n\n"
        "### Pass 1 - 2026-06-07\n- self.\n\n"
        "### Pass 2 - 2026-06-07 - independent\n"
        "**Disposition:** ESCALATE\n"
        "**Result:** ready — no further findings.\n"
    )
    resc = _run(_v2_plan(repo, f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n", review=review_esc))
    assert resc.returncode == 1 and "ESCALATE" in resc.stdout

    for bad_disp in ("**Disposition:** DEFER", "**Disposition:** SPLIT_TO_CHAIN (CHG-0)", "**Disposition:** SPLIT_TO_CHAIN"):
        rev_bad = (
            "## Review log\n\n### Pass 1 - 2026-06-07\n- self.\n\n"
            f"### Pass 2 - 2026-06-07 - independent\n{bad_disp}\n**Result:** ready\n"
        )
        rbad = _run(_v2_plan(repo, f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n", review=rev_bad))
        assert rbad.returncode == 1 and "invalid '**Disposition:**'" in rbad.stdout


def test_v2_split_to_chain_passes_and_stale_zeros_health(tmp_path):
    repo = _repo(tmp_path)
    review_split = (
        "## Review log\n\n"
        "### Pass 1 - 2026-06-07\n- self.\n\n"
        "### Pass 2 - 2026-06-07 - independent\n"
        "- Task T2 became STALE; splitting chain under CHG-28.\n"
        "**Disposition:** SPLIT_TO_CHAIN (CHG-28)\n"
    )
    rows = (
        f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n"
        f"| T2 | `{SHA_ALT}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | STALE |\n"
    )
    r = _run(_v2_plan(repo, rows, review=review_split))
    assert r.returncode == 0, r.stdout + r.stderr
    # T1 = 90.0%, T2 (STALE) = 0.0% => average 45.0%
    assert "health 45.0%" in r.stdout


def test_init_v1_unchanged_and_init_v2_scaffolds_convergence(tmp_path):
    repo = _repo(tmp_path)
    p1 = repo / "PLAN-010_v1.md"
    p1.write_text("# Plan v1\n")
    r1 = _run("--init", p1)
    assert r1.returncode == 0
    txt1 = p1.read_text()
    assert "## Claim ledger" in txt1 and "## Review log" in txt1
    assert "## Task convergence" not in txt1
    assert "plan-gate-v2-snippet" not in txt1

    p2 = repo / "PLAN-011_v2.md"
    p2.write_text("# Plan v2\n")
    r2 = _run("--init", "--v2", p2)
    assert r2.returncode == 0
    txt2 = p2.read_text()
    assert txt2.startswith("<!-- plan-gate: v2 -->")
    assert "## Claim ledger" in txt2 and "## Task convergence" in txt2 and "## Review log" in txt2


def test_v2_split_to_chain_accepts_zero_padded_and_rejects_zero(tmp_path):
    repo = _repo(tmp_path)
    rows = (
        f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n"
        f"| T2 | `{SHA_ALT}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | STALE |\n"
    )
    for chg_id in ("CHG-01", "CHG-09", "CHG-10"):
        rev = (
            "## Review log\n\n"
            "### Pass 1 - 2026-06-07\n- self.\n\n"
            "### Pass 2 - 2026-06-07 - independent\n"
            f"- Splitting under {chg_id}.\n"
            f"**Disposition:** SPLIT_TO_CHAIN ({chg_id})\n"
        )
        r = _run(_v2_plan(repo, rows, review=rev))
        assert r.returncode == 0, f"{chg_id}: {r.stdout} {r.stderr}"

    for bad_chg in ("CHG-0", "CHG-00"):
        rev_bad = (
            "## Review log\n\n"
            "### Pass 1 - 2026-06-07\n- self.\n\n"
            "### Pass 2 - 2026-06-07 - independent\n"
            f"**Disposition:** SPLIT_TO_CHAIN ({bad_chg})\n"
            "**Result:** ready\n"
        )
        r_bad = _run(_v2_plan(repo, f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n", review=rev_bad))
        assert r_bad.returncode == 1 and "invalid '**Disposition:**'" in r_bad.stdout


def test_v2_duplicate_task_row_does_not_skew_non_locked_attribution(tmp_path):
    repo = _repo(tmp_path)
    rows = (
        f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n"
        f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n"
        f"| T2 | `{SHA_ALT}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | DRAFT |\n"
    )
    r = _run(_v2_plan(repo, rows))
    assert r.returncode == 1
    assert "duplicate Task ID 'T1'" in r.stdout
    assert "non-LOCKED task(s) ['T2']" in r.stdout
    assert "['T1']" not in r.stdout


def test_init_v2_preserves_fenced_v1_directive(tmp_path):
    repo = _repo(tmp_path)
    fenced_sample = (
        "# Plan\n\n"
        "```markdown\n"
        "<!-- plan-gate: v1 -->\n"
        "```\n"
    )
    p = repo / "PLAN-012_fenced_v1.md"
    p.write_text(fenced_sample)
    r = _run("--init", "--v2", p)
    assert r.returncode == 0, r.stdout + r.stderr
    out = p.read_text()
    assert out.startswith("<!-- plan-gate: v2 -->")
    assert "```markdown\n<!-- plan-gate: v1 -->\n```" in out


# --- PLAN-052: Prompt-to-code promotion (D1..D4) & PROBE[link0]/PROBE[experiment] sub-typing ---


def test_v2_scorecard_validation_passes_and_rejects_bad_total_floor_or_sum(tmp_path):
    repo = _repo(tmp_path)
    good_scorecard = (
        "\n## Architecture Decision Scorecard (100-Point Model)\n\n"
        "| Pillar | Max Pts | Option A *(Selected)* | Option B | Min Bar (50%) |\n"
        "|---|:---:|:---:|:---:|:---:|\n"
        "| **1. Security** | 25 | **24** *(Good)* | 10 | 12.5 |\n"
        "| **2. Reliability** | 25 | **24** *(Good)* | 10 | 12.5 |\n"
        "| **3. Operational Excellence** | 20 | **19** *(Good)* | 10 | 10.0 |\n"
        "| **4. Developer DX** | 15 | **14** *(Good)* | 10 | 7.5 |\n"
        "| **5. Performance** | 15 | **15** *(Good)* | 10 | 7.5 |\n"
        "| **TOTAL SCORE** | **100** | **96 / 100 (`READY`)** | 50 / 100 | >= 80 |\n"
    )
    p_ok = _v2_plan(
        repo,
        f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n",
        extra=good_scorecard,
    )
    assert _run(p_ok).returncode == 0

    # Pillar floor violation (< 50% of 25 = 12.5)
    bad_floor = good_scorecard.replace("**24** *(Good)* | 10 | 12.5 |\n| **2.", "**11** *(Low)* | 10 | 12.5 |\n| **2.").replace(
        "**96 / 100", "**83 / 100"
    )
    r_floor = _run(_v2_plan(repo, f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n", extra=bad_floor))
    assert r_floor.returncode == 1 and "50% minimum floor" in r_floor.stdout

    # Sum mismatch
    bad_sum = good_scorecard.replace("**96 / 100", "**91 / 100")
    r_sum = _run(_v2_plan(repo, f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n", extra=bad_sum))
    assert r_sum.returncode == 1 and "does not equal 5-pillar sum" in r_sum.stdout

    # Total below 80.0
    bad_tot = (
        "\n## Architecture Decision Scorecard\n\n"
        "| Pillar | Max Pts | Option A *(Selected)* |\n"
        "|---|---:|---:|\n"
        "| 1. Security | 25 | 15 |\n"
        "| 2. Reliability | 25 | 15 |\n"
        "| 3. Ops | 20 | 15 |\n"
        "| 4. DX | 15 | 15 |\n"
        "| 5. Perf | 15 | 15 |\n"
        "| TOTAL SCORE | 100 | 75 / 100 |\n"
    )
    r_tot = _run(_v2_plan(repo, f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n", extra=bad_tot))
    assert r_tot.returncode == 1 and "below passing threshold 80.0" in r_tot.stdout


def test_v2_open_questions_matrix_validation_passes_and_rejects_bad_count_math_or_selection(tmp_path):
    repo = _repo(tmp_path)
    good_oq = (
        "\n## Tier-1 Open Questions\n\n"
        "| # | Option | 1. Arch & EaC | 2. Security & Blast | 3. User / Agent DX | 4. Platform Owner Ops | **Composite** |\n"
        "|---|---|:---:|:---:|:---:|:---:|:---:|\n"
        "| **1** | **(Selected) Option 1** | **5.0** | **5.0** | **4.8** | **4.8** | **4.90 / 5.00** |\n"
        "| **2** | Option 2 | 3.0 | 3.0 | 3.0 | 3.0 | **3.00 / 5.00** |\n"
        "| **3** | Option 3 | 2.0 | 2.0 | 2.0 | 2.0 | **2.00 / 5.00** |\n"
    )
    r_ok = _run(_v2_plan(repo, f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n", extra=good_oq))
    assert r_ok.returncode == 0, r_ok.stdout

    # Fewer than 3 options
    two_opts = "\n".join(good_oq.splitlines()[:-1]) + "\n"
    r_two = _run(_v2_plan(repo, f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n", extra=two_opts))
    assert r_two.returncode == 1 and "requires at least 3 options" in r_two.stdout

    # Selected option is not highest composite
    wrong_sel = good_oq.replace("(Selected) Option 1", "Option 1").replace("Option 2", "(Selected) Option 2")
    r_sel = _run(_v2_plan(repo, f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n", extra=wrong_sel))
    assert r_sel.returncode == 1 and "is less than highest composite" in r_sel.stdout


def test_internal_references_alphanumeric_labels_inline_backticks_duplicate_id_dangling_claim_and_new_at_pass(tmp_path):
    repo = _repo(tmp_path)
    # Alphanumeric row labels (3b, 4) + inline-backticked `Claim 99` must pass cleanly
    rows_alpha = (
        "| 3 | base claim | `task_completed` | src/loop.py:11 |\n"
        "| 3b | sub claim [NEW@pass2] | `task_completed` | src/loop.py:11 |\n"
        "| 4 | next claim | `task_completed` | src/loop.py:11 |\n"
    )
    prose_safe = "\nSee Claim 3, Claim 3b, and Claim 4 (ignoring inline literal `Claim 99`).\n"
    r_alpha = _run(
        _v2_plan(
            repo,
            f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n",
            ledger_rows=rows_alpha,
            extra=prose_safe,
        )
    )
    assert r_alpha.returncode == 0, r_alpha.stdout

    # Duplicate numbered row ID + dangling un-backticked Claim 77 + out-of-range NEW@pass5 fail in v2
    rows_bad = (
        "| 1 | first | `task_completed` | src/loop.py:11 |\n"
        "| 1 | duplicate [NEW@pass5] | `task_completed` | src/loop.py:11 |\n"
    )
    r_bad = _run(
        _v2_plan(
            repo,
            f"| T1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n",
            ledger_rows=rows_bad,
            extra="\nBody cites Claim 77 in prose.\n",
        )
    )
    assert r_bad.returncode == 1
    assert "duplicate row ID '1'" in r_bad.stdout
    assert "Claim 77" in r_bad.stdout
    assert "NEW@pass5 exceeds Review log pass count" in r_bad.stdout


def test_probe_subtypes_link0_vs_experiment_and_v2_task_probe_coherence(tmp_path):
    repo = _repo(tmp_path)
    # PROBE[link0] with valid read-only command passes; PROBE[experiment] with curl passes
    rows_ok = (
        GOOD_ROW
        + "| 2 | local test check — blocks Phase A | `n/a` | PROBE[link0]: pytest -q |\n"
        + "| 3 | live api check — blocks Phase B | `n/a` | PROBE[experiment]: curl https://example.com |\n"
    )
    r_ok = _run(_plan(repo, rows_ok, review=REVIEW_OK + PHASES))
    assert r_ok.returncode == 0, r_ok.stdout

    # PROBE[link0] with non-sandboxed command or shell meta fails at Stage 1
    rows_bad_link0 = "| 1 | bad link0 — blocks Phase A | `n/a` | PROBE[link0]: curl https://example.com |\n"
    r_bad_link0 = _run(_plan(repo, rows_bad_link0, review=REVIEW_OK + PHASES))
    assert r_bad_link0.returncode == 1 and "violates Link-0 read-only sandbox" in r_bad_link0.stdout

    # v2 Task convergence Status = PROBE backed by an experiment command fails cross-table check
    rows_exp = "| 1 | TASK-1 live probe — blocks Phase A | `n/a` | PROBE[experiment]: curl https://example.com |\n"
    rev_split = (
        "## Review log\n\n### Pass 1 - 2026-11-21\n- self\n\n"
        "### Pass 2 - 2026-11-21 - independent\n**Disposition:** SPLIT_TO_CHAIN (CHG-30)\n"
        + PHASES
    )
    r_cross = _run(
        _v2_plan(
            repo,
            f"| TASK-1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | PROBE |\n",
            ledger_rows=rows_exp,
            review=rev_split,
        )
    )
    assert r_cross.returncode == 1 and "cannot be backed by non-sandboxed Claim-ledger probe" in r_cross.stdout


def test_hash_tasks_cli_and_section_excluded_body_hash_enforcement(tmp_path):
    repo = _repo(tmp_path)
    body_phase = "\n## Execution\n\n### Phase 1: Do Work (`TASK-1`)\n1. Implement feature.\n"
    p = _v2_plan(
        repo,
        f"| TASK-1 | `{SHA_OK}` | 4.5 | 4.5 | 4.5 | 4.5 | 18.0 | LOCKED |\n",
        extra=body_phase,
    )
    # Wrong SHA-256 fails when TASK-1 has a body definition
    r_fail = _run(p)
    assert r_fail.returncode == 1 and "does not match body task digest" in r_fail.stdout

    # --hash-tasks --fix rewrites the table with the deterministic digest and passes
    r_fix = _run("--hash-tasks", "--fix", p)
    assert r_fix.returncode == 0, r_fix.stdout
    assert "updated 1 task SHA-256 digest(s)" in r_fix.stdout
    assert _run(p).returncode == 0

