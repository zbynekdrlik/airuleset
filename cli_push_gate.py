"""airuleset push-GATE section — extracted from cli_remote.py (#630).

`cli_remote.py` crossed the ~1000-line `architecture-first.md` soft cap after
#629 added the mid-run tree-mutation detector; this leaf carries the whole
pre-push GATE section VERBATIM out of it — the two cohesive clusters that
`cmd_push` runs BEFORE it pushes:

- the #548 TMPDIR-litter guard (`PUSH_TMPDIR_LITTER_CAP`,
  `_effective_push_tmpdir_cap`, `_check_push_tmpdir_litter`), and
- the #629 mid-run tracked-tree-mutation detector
  (`TREE_MOVED_CHANGED_FILES_SHOWN`, `_tracked_tree_fingerprint`,
  `_diff_tracked_tree_fingerprints`, `_fp_unavailable_reason`,
  `_render_tree_moved_report`, `_classify_push_gate_outcome`).

`cli_remote.py` re-exports every name from here at the old definition site
(`from cli_push_gate import (...)`), so `cmd_push`'s bare-name calls and the
tests that go through `cli_remote._X` (`tests/test_tmp_litter_548.py`,
`tests/test_gate_tree_moved_629.py`) keep resolving unchanged — the same
verbatim-move + facade-re-export pattern the #433 L-cluster CLI leaves use.

Deliberately SELF-CONTAINED: stdlib only, and only `os` at module level
(`hashlib` / `subprocess` are imported LOCALLY inside `_tracked_tree_fingerprint`,
verbatim — the never-top-level-import-subprocess idiom cli_remote.py itself
follows). NO `import airuleset`: this whole section is pure — it takes the repo
dir as an argument and shells out to git, with zero coupling to the resident
airuleset facade.
"""

import os


# #548 point 3 — push-gate TMPDIR litter guard (anti-recidivism). After the full
# suite runs inside its own per-run TMPDIR (redirected via `test_env["TMPDIR"]`
# below, then removed), count the entries it left behind: the per-run dir is
# unique + private to THIS push's subprocess, so its leftover count IS the
# suite's raw-mkdtemp leak with zero interference from the shared box's other
# workers. Beyond a calibrated cap that is a mkdtemp-without-cleanup regression
# (the exact class that reached 465k on dev1) — fail the push LOUD. Env-tunable;
# calibrated ABOVE the measured full-suite baseline so the current suite passes
# and only a gross regression trips it.
PUSH_TMPDIR_LITTER_CAP = 6000       # env AIRULESET_PUSH_TMPDIR_LITTER_CAP


def _effective_push_tmpdir_cap():
    """The cap actually in force — `AIRULESET_PUSH_TMPDIR_LITTER_CAP` override,
    else the `PUSH_TMPDIR_LITTER_CAP` default. Single source so the guard and
    its failure MESSAGE never disagree (#548 review A-MINOR-1)."""
    try:
        return int(os.environ.get("AIRULESET_PUSH_TMPDIR_LITTER_CAP",
                                  PUSH_TMPDIR_LITTER_CAP))
    except (TypeError, ValueError):
        return PUSH_TMPDIR_LITTER_CAP


def _check_push_tmpdir_litter(run_dir, cap=None):
    """Return `(ok, count)` — `ok=False` when the test suite left more than
    `cap` top-level entries in its per-run TMPDIR (a leak regression, #548).
    Never raises: a missing/unreadable dir counts 0 and passes."""
    if cap is None:
        cap = _effective_push_tmpdir_cap()
    try:
        count = len(os.listdir(str(run_dir)))
    except OSError:
        return (True, 0)
    return (count <= cap, count)


# --- #629: push-gate mid-run tree-mutation detection ------------------------- #
# The gate runs the whole suite (`unittest discover`) against the SHARED main
# checkout. Four wiring tests assert on `inspect.getsource(airuleset.<fn>)`,
# which is correct ONLY while `airuleset.py` on disk stays byte-identical to the
# version the running process imported. When a concurrent integration merges the
# NEXT lane into the shared checkout WHILE the gate's suite is still running, the
# file changes size mid-run, `getsource` slices the NEW source at the OLD
# `co_firstlineno`, and those tests fail with a wrong-non-empty-content signature
# that reads EXACTLY like a code regression (a full diagnostic cycle lost).
#
# A before/after content fingerprint of every TRACKED file, taken immediately
# around the suite subprocess, lets the gate REPORT that its own tree moved
# instead of letting it masquerade as a test failure -- never a retry, never
# swallowing a genuine failure (if a real failure and a mid-run mutation
# coincide, both stay visible). Only TRACKED files are hashed, so `.claude/*`
# (hence the gitignored `.claude/worktrees/` a concurrent worker builds in) can
# never false-trigger -- only a mutation to the main checkout's tracked files (a
# supervisor merge/checkout) does. Detection is FAIL-OPEN: any inability to take
# a fingerprint disables it (it never blocks the gate merely because it could not
# run, and never suppresses a genuine test result).
TREE_MOVED_CHANGED_FILES_SHOWN = 20     # cap the report's changed-file list


def _tracked_tree_fingerprint(repo_dir):
    """Snapshot of every git-tracked file's working-tree content, for #629.
    Returns `{"head": <sha or None>, "files": {relpath: sha256hex} or None,
    "error": <str or None>}`. NEVER raises: on any git/read failure `files` is
    None and `error` is set, which disables detection (fail-open)."""
    import hashlib
    import subprocess
    repo = str(repo_dir)
    try:
        ls = subprocess.run(
            ["git", "-C", repo, "ls-files", "-z"],
            capture_output=True, timeout=120)
        if ls.returncode != 0:
            return {"head": None, "files": None,
                    "error": "git ls-files rc=%s: %s" % (
                        ls.returncode,
                        ls.stderr.decode("utf-8", "replace").strip())}
        rels = [p for p in ls.stdout.split(b"\0") if p]
        files = {}
        for rel in rels:
            relpath = rel.decode("utf-8", "surrogateescape")
            try:
                with open(os.path.join(repo, relpath), "rb") as fh:
                    files[relpath] = hashlib.sha256(fh.read()).hexdigest()
            except OSError as e:
                # A tracked file unreadable AT this instant is itself evidence
                # the tree is moving -- a distinct sentinel so the before/after
                # diff flags it, never a crash.
                files[relpath] = "<unreadable:%s>" % type(e).__name__
        head = None
        hr = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30)
        if hr.returncode == 0:
            head = hr.stdout.strip() or None
        return {"head": head, "files": files, "error": None}
    except Exception as e:  # noqa: BLE001 -- fail-open by contract (never raises)
        return {"head": None, "files": None,
                "error": "%s: %s" % (type(e).__name__, e)}


def _diff_tracked_tree_fingerprints(before, after):
    """`(moved, changed_paths, available)` for two `_tracked_tree_fingerprint`
    snapshots. `available` is False when EITHER snapshot could not be taken (git
    unavailable) -- then detection is OFF (`moved` False, `changed_paths` []).
    Otherwise `moved` is True iff the tracked-content sets differ, and
    `changed_paths` names every added / removed / changed relpath.

    Accepted residual: a change-then-exact-revert entirely within the
    before/after window evades this (start == end). That is not a real
    integration pattern -- a merge ADVANCES the tree, it never restores
    byte-identical prior content -- and the suite still read a transient
    inconsistent tree; a before/after diff was chosen over continuous
    monitoring (a new concurrent heuristic layer) as the simplest thing that
    reliably catches the case that actually happens."""
    fb = (before or {}).get("files")
    fa = (after or {}).get("files")
    if fb is None or fa is None:
        return (False, [], False)
    changed = sorted(k for k in set(fb) | set(fa) if fb.get(k) != fa.get(k))
    return (bool(changed), changed, True)


def _fp_unavailable_reason(before, after):
    """First snapshot error string, for logging WHY detection was skipped."""
    for fp in (before, after):
        if fp and fp.get("error"):
            return fp["error"]
    return "tracked-tree fingerprint unavailable"


def _render_tree_moved_report(before, after, changed_paths, test_returncode):
    """The unambiguous VOID report (#629). A reader must immediately understand
    the tree moved mid-run and that the suite result is not trustworthy -- never
    mistake it for a regression."""
    head_b = (before or {}).get("head") or "<unknown>"
    head_a = (after or {}).get("head") or "<unknown>"
    shown = changed_paths[:TREE_MOVED_CHANGED_FILES_SHOWN]
    lines = [
        "",
        "=" * 78,
        "PUSH GATE VOID — tracked files changed on disk during the test run (#629)",
        "=" * 78,
        "A concurrent integration mutated the shared checkout while this gate's",
        "test suite was running, so the suite read a mix of the old and new tree.",
        "Its result is NOT trustworthy — this is NOT (necessarily) a code regression.",
        "",
        "  HEAD before run:  %s" % head_b,
        "  HEAD after run:   %s" % head_a,
        "  Test suite exit code: %s  (VOID — see above)" % test_returncode,
        "  Tracked files that changed mid-run (%d):" % len(changed_paths),
    ]
    lines += ["    - %s" % p for p in shown]
    if len(changed_paths) > len(shown):
        lines.append("    ... and %d more" % (len(changed_paths) - len(shown)))
    lines += [
        "",
        "Do NOT read the test failures above as a regression. Re-run",
        "`airuleset.py push` on a SETTLED tree (serialize the concurrent",
        "integration first). Refusing to push.",
        "=" * 78,
    ]
    return "\n".join(lines)


def _classify_push_gate_outcome(test_returncode, fp_before, fp_after):
    """Single decision function for the push gate's suite outcome (#629).
    Returns `(ok, reason, message)` with `reason` in
    {"tree-moved", "tests-failed", "clean"}. Precedence: a mid-run tree mutation
    VOIDS the run and is reported FIRST (so it never masquerades as a regression),
    then a genuine test failure. The TMPDIR litter guard stays its OWN separate
    branch in `cmd_push` (checked only after a clean verdict here), so tree-moved
    beats it too. Detection being UNAVAILABLE never blocks a clean run and never
    swallows a real failure -- it only adds a note that a mid-run mutation could
    not be ruled out."""
    moved, changed, available = _diff_tracked_tree_fingerprints(fp_before, fp_after)
    if moved:
        return (False, "tree-moved",
                _render_tree_moved_report(fp_before, fp_after, changed, test_returncode))
    skip_note = ""
    if not available:
        skip_note = ("\n  [tree-move detection skipped: %s — a mid-run mutation "
                     "could not be ruled out]"
                     % _fp_unavailable_reason(fp_before, fp_after))
    if test_returncode != 0:
        return (False, "tests-failed",
                "  TESTS FAILED — refusing to push untested code." + skip_note)
    return (True, "clean", "  Tests passed." + skip_note)
