"""#817 — an isolation-FAILED dispatched worker (its `isolation:"worktree"`
silently did not apply, so it runs in the SHARED airuleset main checkout) must
NOT be able to mutate the shared checkout's HEAD/branches. Incident: such a
worker ran `git checkout -b <branch>` in the shared tree during the supervisor's
`git merge --no-ff` integration, HEAD moved off `main`, the merge landed on the
worker's branch, and the worker later deleted that branch → the merge commit was
LOST.

Root cause: `hooks/block-foreign-airuleset-write.sh` RULE B (#496) only engages
on a `*/.claude/worktrees/*` cwd; an isolation-failed worker's cwd is the shared
checkout (NOT a worktree), so the whole guard was skipped. This adds RULE B2 (a
sibling branch keyed on the git-op's RESOLVED target being the shared airuleset
checkout, `REPO_ROOT` = the hook's own checkout), and widens the shared
`_GIT_WRITE` set with `switch`/`branch`/`worktree` (RULE B missed them too).

Every hook case is hermetic: a fake airuleset checkout is built under a temp dir
with COPIES of the hook + its helper, so `REPO_ROOT` (dirname-dirname of the
running hook) resolves to that fake checkout — the same identity the real
installed hook uses. Nothing depends on the box's real layout.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"
HOOK_SRC = HOOKS / "block-foreign-airuleset-write.sh"
GUARD_SRC = HOOKS / "worktree_guard.py"
FOREIGN_SRC = HOOKS / "foreign_repo_guard.py"
CLASSIFY_SRC = HOOKS / "git_write_classify.py"

# An airuleset-session transcript so RULE A's own allow-all short-circuit is the
# state under test for the ALLOW cases (RULE B2 runs BEFORE RULE A).
AR_TR = ("/home/newlevel/.claude/projects/-home-newlevel-devel-airuleset/"
         "2d02a127-0000-0000-0000-000000000000.jsonl")


# --------------------------------------------------------------------------- #
# worktree_guard.py loaded directly for unit-level teeth on the new API.
# --------------------------------------------------------------------------- #
def _load_guard():
    spec = importlib.util.spec_from_file_location("wt_guard_817", GUARD_SRC)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["wt_guard_817"] = mod
    spec.loader.exec_module(mod)
    return mod


wg = _load_guard()


class HookB2Base(TestCase):
    """Runs a COPY of the hook whose REPO_ROOT is a hermetic fake checkout."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="iso817-")
        self.checkout = os.path.join(self.tmp, "fake-airuleset")
        os.makedirs(os.path.join(self.checkout, "hooks"), exist_ok=True)
        os.makedirs(os.path.join(self.checkout, "watchdog"), exist_ok=True)
        # a real worktree of the fake checkout — a `cd` here is legitimate.
        self.wt = os.path.join(self.checkout, ".claude", "worktrees", "agent-x")
        os.makedirs(self.wt, exist_ok=True)
        # #831: worktree_guard.py + foreign_repo_guard.py now import the shared
        # git-write classifier, so the hermetic checkout must carry it too, or
        # `import git_write_classify` fails and the python helper crashes (the
        # hook then fails-open and every BLOCK case would silently ALLOW).
        for src, name in ((HOOK_SRC, "block-foreign-airuleset-write.sh"),
                          (GUARD_SRC, "worktree_guard.py"),
                          (FOREIGN_SRC, "foreign_repo_guard.py"),
                          (CLASSIFY_SRC, "git_write_classify.py")):
            shutil.copy(src, os.path.join(self.checkout, "hooks", name))
        self.hook = os.path.join(self.checkout, "hooks",
                                 "block-foreign-airuleset-write.sh")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_hook(self, payload, env_extra=None):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "HOME": os.environ.get("HOME", "/tmp")}
        if env_extra:
            env.update(env_extra)
        return subprocess.run(["bash", self.hook], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)

    def bash(self, cmd, cwd=None, agent_id="w817", transcript=AR_TR,
             agent_type="autopilot-worker"):
        p = {"tool_name": "Bash", "tool_input": {"command": cmd},
             "cwd": cwd if cwd is not None else self.checkout,
             "transcript_path": transcript}
        if agent_id is not None:
            p["agent_id"] = agent_id
        if agent_type is not None:
            p["agent_type"] = agent_type
        return p

    def write(self, tool, path, cwd=None, agent_id="w817", transcript=AR_TR,
              agent_type="autopilot-worker"):
        ti = {"notebook_path": path} if tool == "NotebookEdit" else {"file_path": path}
        p = {"tool_name": tool, "tool_input": ti,
             "cwd": cwd if cwd is not None else self.checkout,
             "transcript_path": transcript}
        if agent_id is not None:
            p["agent_id"] = agent_id
        if agent_type is not None:
            p["agent_type"] = agent_type
        return p

    def assertBlocked(self, payload, **kw):
        r = self.run_hook(payload, **kw)
        self.assertEqual(r.returncode, 2,
                         f"expected BLOCK\npayload={payload}\nstderr={r.stderr}")

    def assertAllowed(self, payload, **kw):
        r = self.run_hook(payload, **kw)
        self.assertEqual(r.returncode, 0,
                         f"expected ALLOW\npayload={payload}\nstderr={r.stderr}")


class TestRuleB2BlocksSharedCheckout(HookB2Base):
    def test_checkout_dash_b(self):
        self.assertBlocked(self.bash("git checkout -b worktree-issue-802-clean"))

    def test_switch(self):
        self.assertBlocked(self.bash("git switch main"))

    def test_merge(self):
        self.assertBlocked(self.bash("git merge --no-ff worktree-agent-issue-813"))

    def test_reset_hard(self):
        self.assertBlocked(self.bash("git reset --hard origin/main"))

    def test_branch_delete(self):
        self.assertBlocked(self.bash("git branch -D worktree-issue-802-clean"))

    def test_commit(self):
        self.assertBlocked(self.bash("git commit -am contaminate"))

    def test_push(self):
        self.assertBlocked(self.bash("git push origin HEAD:main"))

    def test_worktree_remove(self):
        self.assertBlocked(self.bash("git worktree remove .claude/worktrees/agent-y"))

    def test_cherry_pick(self):
        self.assertBlocked(self.bash("git cherry-pick abc123"))

    def test_subdir_cwd_still_blocks(self):
        # hole (i): cwd is a SUBDIR of the shared checkout, not the root.
        self.assertBlocked(self.bash("git checkout -b x",
                                     cwd=os.path.join(self.checkout, "watchdog")))

    def test_dash_C_from_foreign_cwd_blocks(self):
        # hole (ii): cwd is NEITHER tree; -C targets the shared checkout.
        self.assertBlocked(self.bash("git -C %s checkout -b x" % self.checkout,
                                     cwd="/tmp"))

    def test_write_into_shared_checkout(self):
        self.assertBlocked(self.write("Write",
                                      os.path.join(self.checkout, "watchdog", "x.py")))

    def test_edit_into_shared_checkout(self):
        self.assertBlocked(self.write("Edit",
                                      os.path.join(self.checkout, "airuleset.py")))

    def test_relative_write_resolves_under_shared_checkout(self):
        self.assertBlocked(self.write("Write", "body.md"))


class TestRuleB2Allows(HookB2Base):
    def test_read_status(self):
        self.assertAllowed(self.bash("git status --porcelain"))

    def test_read_branch_list(self):
        self.assertAllowed(self.bash("git branch --list"))

    def test_read_log(self):
        self.assertAllowed(self.bash("git log --oneline -5"))

    def test_read_diff(self):
        self.assertAllowed(self.bash("git diff origin/main"))

    def test_supervisor_no_agent_id(self):
        # the MAIN session's own integration merge — no agent_id, never B2.
        self.assertAllowed(self.bash("git merge --no-ff worktree-agent-issue-813",
                                     agent_id=None, agent_type=None))

    def test_non_autopilot_worker_subagent_allowed(self):
        # a general-purpose / SDD / cavecrew / fork subagent doing shared-tree
        # git work is NOT the incident class — B2 is scoped to autopilot-worker.
        self.assertAllowed(self.bash("git checkout -b x", agent_type="general-purpose"))

    def test_override_env(self):
        self.assertAllowed(self.bash("git checkout -b x"),
                           env_extra={"AIRULESET_ALLOW_WORKTREE_ESCAPE": "1"})

    def test_bypass_marker(self):
        self.assertAllowed(self.bash(
            "git checkout -b x  # airuleset:worktree-ok serial fallback"))

    def test_bare_git_from_foreign_cwd(self):
        # cwd=/tmp, bare git -> targets /tmp's repo, not the shared checkout.
        self.assertAllowed(self.bash("git checkout -b x", cwd="/tmp"))

    def test_write_to_scratch_outside_checkout(self):
        self.assertAllowed(self.write("Write", "/tmp/iso817-scratch/body.md"))

    def test_symbolic_ref_read_allowed(self):
        # the worker's own self-check / the supervisor's Step-4 assert (a READ).
        self.assertAllowed(self.bash("git symbolic-ref --short HEAD"))

    def test_stash_list_allowed(self):
        self.assertAllowed(self.bash("git stash list"))

    def test_worktree_list_allowed(self):
        self.assertAllowed(self.bash("git worktree list"))


class TestRuleB2ReviewHardening(HookB2Base):
    """#817 fresh-context adversarial review — proven-live bypasses, now closed."""

    def test_multiline_bypass(self):
        self.assertBlocked(self.bash("git status\ngit checkout -b evil"))

    def test_env_prefix_bypass(self):
        self.assertBlocked(self.bash("FOO=1 git checkout -b evil"))

    def test_env_prefix_of_the_override_var_still_blocks(self):
        # the per-command env prefix must NOT blind the analyzer into an
        # unlogged allow — the override is a STANDING export only.
        self.assertBlocked(self.bash("AIRULESET_ALLOW_WORKTREE_ESCAPE=1 git checkout -b evil"))

    def test_env_wrapper_bypass(self):
        self.assertBlocked(self.bash("env git checkout -b evil"))

    def test_command_wrapper_bypass(self):
        self.assertBlocked(self.bash("command git checkout -b evil"))

    def test_dash_c_value_bypass(self):
        self.assertBlocked(self.bash("git -c user.email=x commit -am evil"))

    def test_bare_stash_blocks(self):
        self.assertBlocked(self.bash("git stash"))

    def test_symbolic_ref_write_blocks(self):
        self.assertBlocked(self.bash("git symbolic-ref HEAD refs/heads/hijack"))

    def test_update_ref_blocks(self):
        self.assertBlocked(self.bash("git update-ref refs/heads/main HEAD~3"))

    def test_rm_shared_file_blocks(self):
        self.assertBlocked(self.bash("rm -rf watchdog"))

    def test_rm_the_guard_itself_blocks(self):
        self.assertBlocked(self.bash("rm hooks/worktree_guard.py"))

    def test_worktrees_parent_dir_hijack_blocks(self):
        # git run at CHECKOUT/.claude/worktrees walks UP to the shared HEAD.
        self.assertBlocked(self.bash(
            "git -C %s checkout -b hijack" % os.path.join(self.checkout, ".claude", "worktrees")))

    def test_cd_worktrees_parent_then_checkout_blocks(self):
        self.assertBlocked(self.bash("cd .claude/worktrees && git checkout -b hijack"))

    def test_write_into_worktrees_dir_blocks(self):
        # an isolation-failed worker owns no worktree; a write into any worktree
        # (a sibling's) is corruption.
        self.assertBlocked(self.write("Write", os.path.join(self.wt, "y.py")))

    def test_cd_into_a_worktree_then_checkout_blocks(self):
        self.assertBlocked(self.bash(
            "cd .claude/worktrees/agent-x && git checkout -b y"))


class TestRuleBStillCoversWorktreeEscape(HookB2Base):
    """RULE B (cwd IS a worktree) must now ALSO catch `git -C <main> switch`
    and `git -C <main> branch -D` — the same op-enumeration gap #817 widens."""

    def test_worktree_switch_dash_C_main(self):
        self.assertBlocked(self.bash("git -C %s switch main" % self.checkout,
                                     cwd=self.wt))

    def test_worktree_branch_delete_dash_C_main(self):
        self.assertBlocked(self.bash("git -C %s branch -D z" % self.checkout,
                                     cwd=self.wt))

    def test_worktree_branch_list_dash_C_main_allowed(self):
        self.assertAllowed(self.bash("git -C %s branch --list" % self.checkout,
                                     cwd=self.wt))

    def test_worktree_own_checkout_allowed(self):
        # a worker legitimately working its OWN worktree branch.
        self.assertAllowed(self.bash("git checkout -b feature", cwd=self.wt))


class TestGuardUnit(TestCase):
    """Direct teeth on worktree_guard.mutates_shared_checkout + the widened set."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="iso817u-")
        self.co = os.path.join(self.tmp, "co")
        os.makedirs(os.path.join(self.co, ".claude", "worktrees", "agent-x"))
        os.makedirs(os.path.join(self.co, "sub"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def M(self, cmd, worker_cwd=None):
        return wg.mutates_shared_checkout(cmd, self.co,
                                          worker_cwd if worker_cwd else self.co)

    def test_branch_op_blocks(self):
        for c in ("git checkout -b x", "git switch main", "git merge x",
                  "git reset --hard x", "git rebase x", "git cherry-pick x",
                  "git commit -am x", "git push origin HEAD:main",
                  "git branch -D x", "git branch newone",
                  "git worktree remove foo"):
            self.assertTrue(self.M(c), c)

    def test_reads_allowed(self):
        for c in ("git status", "git log", "git branch --list", "git branch -a",
                  "git branch --contains HEAD", "git diff", "git worktree list",
                  "git stash list", "git tag --list",
                  "git symbolic-ref --short HEAD", "git symbolic-ref HEAD"):
            self.assertFalse(self.M(c), c)

    def test_new_bypass_shapes_block(self):
        # #817 review: newline / env-prefix / wrapper / -c value / bare stash /
        # plumbing / rm — all proven-live bypasses, now closed.
        for c in ("git status\ngit checkout -b evil",
                  "FOO=1 git checkout -b evil",
                  "AIRULESET_ALLOW_WORKTREE_ESCAPE=1 git checkout -b evil",
                  "env git checkout -b evil",
                  "command git checkout -b evil",
                  "nohup git checkout -b evil",
                  "timeout 300 git checkout -b evil",
                  "git -c user.email=x commit -am evil",
                  "git stash",
                  "git symbolic-ref HEAD refs/heads/hijack",
                  "git update-ref refs/heads/main HEAD~3",
                  "rm -rf watchdog",
                  "rm hooks/worktree_guard.py"):
            self.assertTrue(self.M(c), c)

    def test_worktree_dir_and_siblings_block(self):
        # exempt_wt=False: an isolation-failed worker owns no worktree.
        wtp = os.path.join(self.co, ".claude", "worktrees")
        sib = os.path.join(wtp, "agent-x")
        self.assertTrue(self.M("git -C %s checkout -b hijack" % wtp))   # parent dir
        self.assertTrue(self.M("cd .claude/worktrees && git checkout -b hijack"))
        self.assertTrue(self.M("cd .claude/worktrees/agent-x && git checkout -b y"))
        self.assertTrue(self.M("git -C %s reset --hard HEAD~5" % sib))  # sibling
        self.assertTrue(self.M("git worktree remove .claude/worktrees/agent-x"))

    def test_dash_C_checkout_from_elsewhere_blocks(self):
        self.assertTrue(self.M("git -C %s checkout -b y" % self.co,
                               worker_cwd="/tmp"))

    def test_subdir_cwd_blocks(self):
        self.assertTrue(self.M("git checkout -b y",
                               worker_cwd=os.path.join(self.co, "sub")))

    def test_bare_git_elsewhere_allowed(self):
        self.assertFalse(self.M("git checkout -b y", worker_cwd="/tmp"))

    def test_widened_write_set(self):
        for v in ("switch", "branch", "worktree", "symbolic-ref", "update-ref"):
            self.assertIn(v, wg._GIT_WRITE)


class TestWorkerSelfCheckDoc(TestCase):
    """Part (a): the worker contract carries a hard first-step ISOLATION FAILED
    self-check (agents/autopilot-worker.md IS the worker's system prompt)."""

    def setUp(self):
        self.md = (REPO / "agents" / "autopilot-worker.md").read_text(encoding="utf-8")

    def test_isolation_failed_marker(self):
        self.assertIn("ISOLATION FAILED", self.md)

    def test_self_check_commands(self):
        self.assertIn("git rev-parse --show-toplevel", self.md)
        self.assertIn("git symbolic-ref --short HEAD", self.md)

    def test_self_check_is_an_abort_on_isolation_failure(self):
        # tokens UNIQUE to the #817 self-check bullet (0 pre-fix occurrences),
        # so a removal of the bullet fails these — not vacuous whole-file hits.
        self.assertIn("SILENTLY DID NOT APPLY", self.md)
        self.assertIn(".claude/worktrees/", self.md)


class TestSupervisorPreMergeAssertDoc(TestCase):
    """Part (c): SKILL.md Step 4 asserts HEAD is the integration target BEFORE
    each --no-ff merge (a worker could have hijacked the shared HEAD)."""

    def setUp(self):
        self.md = (REPO / "skills" / "autopilot" / "SKILL.md").read_text(encoding="utf-8")

    def test_symbolic_ref_assert_present(self):
        self.assertIn("git symbolic-ref --short HEAD", self.md)

    def test_mentions_817_context(self):
        self.assertIn("#817", self.md)


if __name__ == "__main__":
    main()
