"""#831 — RULE A (`hooks/foreign_repo_guard.py` behind
`block-foreign-airuleset-write.sh`) must block the SAME git-write shapes RULE B
(`worktree_guard.py`) blocks. Before #831, RULE A carried a NARROWER `_GIT_WRITE`
(no checkout/switch/branch/worktree/restore/clean/symbolic-ref/update-ref) and NO
env-assignment/wrapper prefix-strip or `-c <value>` skip, so a FOREIGN session's

    git -C ~/devel/airuleset checkout -b evil
    git -C ~/devel/airuleset switch other
    git -C ~/devel/airuleset branch -D main
    git -C ~/devel/airuleset symbolic-ref HEAD refs/heads/x
    env git -C ~/devel/airuleset commit -m x
    git -c user.email=x -C ~/devel/airuleset commit -m x

escaped RULE A (rc 0) while RULE B already blocked them (#817). The fix shares
the git-write classification, the wrapper strip and the newline normalization
with RULE B from ONE module (`git_write_classify.py`).

RED against the pre-#831 tree: every ForeignRuleAWidened / _Unit block case
ALLOWS (rc 0 / False). GREEN after: they BLOCK, while a genuine READ
(`branch --list`) and the already-covered commit/push behave unchanged.

Mirrors `tests/test_isolation_guard_817.py`: proven-live shapes, both the unit
helper AND the real hook.
"""

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"
HOOK = HOOKS / "block-foreign-airuleset-write.sh"

# A FOREIGN session (a restreamer project transcript) with a FOREIGN cwd, so
# RULE A is the state under test (RULE B/B2 only engage for a subagent worktree).
FOREIGN_TR = ("/home/newlevel/.claude/projects/-home-newlevel-devel-restreamer/"
              "8125adb8-0000-0000-0000-000000000000.jsonl")
FOREIGN_CWD = "/home/newlevel/devel/restreamer"
AR = "/home/newlevel/devel/airuleset"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


frg = _load("frg_831", HOOKS / "foreign_repo_guard.py")
wg = _load("wg_831", HOOKS / "worktree_guard.py")


# The six shapes #817 widened RULE B with, that RULE A used to miss. Each writes
# the airuleset checkout from a FOREIGN cwd.
NEW_WRITE_SHAPES = [
    "git -C %s checkout -b evil" % AR,
    "git -C %s switch other" % AR,
    "git -C %s branch -D main" % AR,
    "git -C %s symbolic-ref HEAD refs/heads/x" % AR,
    "env git -C %s commit -m x" % AR,
    "git -c user.email=x@y.z -C %s commit -m x" % AR,
]
# already-covered writes — must STAY blocked (no regression).
COVERED_WRITE_SHAPES = [
    "git -C %s commit -m x" % AR,
    "git -C %s push" % AR,
]
# genuine READs against the airuleset checkout — must NOT be blocked.
READ_SHAPES = [
    "git -C %s branch --list" % AR,
    "git -C %s symbolic-ref --short HEAD" % AR,
    "git -C %s stash list" % AR,
    "git -C %s worktree list" % AR,
]


class ForeignRuleAWidenedUnit(TestCase):
    """foreign_repo_guard.command_writes_airuleset — the unit under the hook."""

    def test_new_write_shapes_now_target_airuleset(self):
        for c in NEW_WRITE_SHAPES:
            self.assertTrue(
                frg.command_writes_airuleset(c, FOREIGN_CWD),
                "RULE A must detect a write to airuleset: %s" % c)

    def test_covered_write_shapes_still_detected(self):
        for c in COVERED_WRITE_SHAPES:
            self.assertTrue(frg.command_writes_airuleset(c, FOREIGN_CWD), c)

    def test_reads_are_not_flagged(self):
        for c in READ_SHAPES:
            self.assertFalse(
                frg.command_writes_airuleset(c, FOREIGN_CWD),
                "a READ against airuleset must NOT be flagged: %s" % c)

    def test_a_write_to_a_DIFFERENT_repo_is_not_flagged(self):
        # the whole point of RULE A's segment/target verification: a git write
        # on a foreign repo alongside an unrelated airuleset.py CLI call is fine.
        for c in ("git -C /home/x/other checkout -b b",
                  "env git -C /home/x/other commit -m x",
                  "python3 ~/devel/airuleset/airuleset.py autopilot-lock acquire "
                  "--repo x/y && git -C /home/x/other switch main"):
            self.assertFalse(frg.command_writes_airuleset(c, FOREIGN_CWD), c)


class RuleAParity831(TestCase):
    """RULE A and RULE B must share ONE verb set + one classifier, so they can
    never drift again (the #831 divergence)."""

    def test_both_guards_share_the_same_classifier_object(self):
        import git_write_classify as gwc
        self.assertIs(frg._classify_git_command, gwc.classify_git_command)
        self.assertIs(wg._classify_git_command, gwc.classify_git_command)

    def test_the_widened_verbs_are_in_the_shared_set(self):
        import git_write_classify as gwc
        for v in ("checkout", "switch", "branch", "worktree",
                  "restore", "clean", "symbolic-ref", "update-ref"):
            self.assertIn(v, gwc.GIT_WRITE)


class ForeignRuleAWidenedHook(TestCase):
    """The real hook, driven from a FOREIGN session — end-to-end RULE A."""

    def _run(self, cmd):
        payload = json.dumps({"tool_input": {"command": cmd},
                              "cwd": FOREIGN_CWD,
                              "transcript_path": FOREIGN_TR})
        return subprocess.run(["bash", str(HOOK)], input=payload,
                              capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin"})

    def test_new_write_shapes_are_blocked_by_the_hook(self):
        for c in NEW_WRITE_SHAPES:
            r = self._run(c)
            self.assertEqual(r.returncode, 2,
                             "expected BLOCK for: %s\nstderr=%s" % (c, r.stderr))
            self.assertIn("ticket", r.stderr.lower())

    def test_reads_are_allowed_by_the_hook(self):
        for c in READ_SHAPES:
            r = self._run(c)
            self.assertEqual(r.returncode, 0,
                             "expected ALLOW for: %s\nstderr=%s" % (c, r.stderr))


if __name__ == "__main__":
    main()
