"""hooks/block-foreign-airuleset-write.sh — RULE B: a worktree-isolated
SUBAGENT must write only inside its OWN worktree, never the shared main checkout.

Live incident (#496, worker #433 step 12, 2026-08-15): a worker dispatched with
`isolation: "worktree"` (worktree correctly created) still Edited the MAIN
checkout via absolute paths (`watchdog/__init__.py` −247 lines + a new
`watchdog/wedge.py`), which blocked the serial-integration merge of another lane.
Write/Edit never `cd`, so the harness's own cwd-based worktree guard did not catch
the absolute-path write at all — this hook is the airuleset-level backstop.

Detection (live-verified from a real worktree-subagent payload): `.agent_id`
present ⇒ subagent; `.cwd` is the STABLE worktree session cwd
(`<main>/.claude/worktrees/<name>`) and never tracks an in-Bash `cd`.

Every case is hermetic — a fake repo tree is built under a temp dir, so nothing
depends on the box's real checkout layout.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

HOOK = (Path(__file__).resolve().parent.parent / "hooks"
        / "block-foreign-airuleset-write.sh")

# An airuleset-session transcript (so RULE A's own allow-all short-circuit is the
# state under test for the worktree cases — RULE B must fire BEFORE it).
AR_TR = ("/home/newlevel/.claude/projects/-home-newlevel-devel-airuleset/"
         "2d02a127-0000-0000-0000-000000000000.jsonl")


class WorktreeGuardBase(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wtguard-")
        self.main = os.path.join(self.tmp, "fakerepo")
        self.wt = os.path.join(self.main, ".claude", "worktrees", "agent-abc123")
        os.makedirs(os.path.join(self.main, "watchdog"), exist_ok=True)
        os.makedirs(os.path.join(self.wt, "watchdog"), exist_ok=True)
        # a sibling worktree (a DIFFERENT worker's checkout)
        self.sibling = os.path.join(self.main, ".claude", "worktrees",
                                    "agent-sibling")
        os.makedirs(self.sibling, exist_ok=True)

    def run_hook(self, payload, env_extra=None):
        env = {"PATH": "/usr/bin:/bin"}
        if env_extra:
            env.update(env_extra)
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)

    def write_payload(self, tool, path, cwd=None, agent_id="abc123",
                      transcript=AR_TR):
        ti = {"file_path": path}
        if tool == "NotebookEdit":
            ti = {"notebook_path": path}
        p = {"tool_name": tool, "tool_input": ti,
             "cwd": cwd if cwd is not None else self.wt,
             "transcript_path": transcript}
        if agent_id is not None:
            p["agent_id"] = agent_id
        return p

    def bash_payload(self, cmd, cwd=None, agent_id="abc123", transcript=AR_TR):
        p = {"tool_name": "Bash", "tool_input": {"command": cmd},
             "cwd": cwd if cwd is not None else self.wt,
             "transcript_path": transcript}
        if agent_id is not None:
            p["agent_id"] = agent_id
        return p

    def assertBlocked(self, payload, **kw):
        r = self.run_hook(payload, **kw)
        self.assertEqual(r.returncode, 2,
                         f"expected BLOCK\npayload={payload}\nstderr={r.stderr}")

    def assertAllowed(self, payload, **kw):
        r = self.run_hook(payload, **kw)
        self.assertEqual(r.returncode, 0,
                         f"expected ALLOW\npayload={payload}\nstderr={r.stderr}")


class WorktreeEscapeBlocked(WorktreeGuardBase):
    # --- the incident shape: absolute-path Write/Edit into the main checkout --
    def test_write_new_file_into_main(self):
        p = self.write_payload("Write",
                               os.path.join(self.main, "watchdog", "wedge.py"))
        self.assertBlocked(p)

    def test_edit_existing_main_file(self):
        p = self.write_payload("Edit",
                               os.path.join(self.main, "watchdog", "__init__.py"))
        self.assertBlocked(p)

    def test_notebookedit_into_main(self):
        p = self.write_payload("NotebookEdit",
                               os.path.join(self.main, "analysis.ipynb"))
        self.assertBlocked(p)

    def test_write_message_names_the_worktree(self):
        p = self.write_payload("Write",
                               os.path.join(self.main, "watchdog", "wedge.py"))
        r = self.run_hook(p)
        self.assertEqual(r.returncode, 2)
        self.assertIn(self.wt, r.stderr)   # tells the worker WHERE to write

    # --- relative path climbing out of the worktree with ../ ------------------
    def test_relative_dotdot_escape(self):
        # cwd is the worktree; a relative ../../../watchdog/x.py climbs to main
        p = self.write_payload("Write", "../../../watchdog/climb.py")
        self.assertBlocked(p)

    # --- a write into a SIBLING worker's worktree is also forbidden -----------
    def test_write_into_sibling_worktree(self):
        p = self.write_payload("Write", os.path.join(self.sibling, "x.py"))
        self.assertBlocked(p)

    # --- Bash mutation shapes named in the ticket -----------------------------
    def test_bash_cd_main_then_commit(self):
        p = self.bash_payload(f"cd {self.main} && git commit -am fix")
        self.assertBlocked(p)

    def test_bash_cd_main_then_apply(self):
        p = self.bash_payload(f"cd {self.main} && git apply /tmp/p.patch")
        self.assertBlocked(p)

    def test_bash_git_dash_C_main_checkout(self):
        p = self.bash_payload(f"git -C {self.main} checkout -- watchdog")
        self.assertBlocked(p)

    def test_bash_redirect_into_main(self):
        p = self.bash_payload(f"echo hi > {self.main}/watchdog/wedge.py")
        self.assertBlocked(p)

    def test_bash_sed_i_on_main(self):
        p = self.bash_payload(f"sed -i s/a/b/ {self.main}/watchdog/__init__.py")
        self.assertBlocked(p)


class WorktreeGuardAllows(WorktreeGuardBase):
    # --- the worker's OWN worktree is fully writable --------------------------
    def test_write_into_own_worktree_absolute(self):
        p = self.write_payload("Write", os.path.join(self.wt, "watchdog", "x.py"))
        self.assertAllowed(p)

    def test_write_into_own_worktree_relative(self):
        p = self.write_payload("Write", "watchdog/x.py")
        self.assertAllowed(p)

    def test_edit_own_worktree_file(self):
        p = self.write_payload("Edit", os.path.join(self.wt, "watchdog", "y.py"))
        self.assertAllowed(p)

    # --- writes entirely outside the repo (scratch / ~/.claude / /tmp) --------
    def test_write_to_tmp(self):
        p = self.write_payload("Write", "/tmp/scratch-body.md")
        self.assertAllowed(p)

    # --- Bash that only READS the main checkout, or commits its OWN branch -----
    def test_bash_bare_commit_runs_in_worktree(self):
        # cwd resets to the worktree each call; a bare commit is the OWN branch
        self.assertAllowed(self.bash_payload("git commit -am wip"))

    def test_bash_read_main_file(self):
        self.assertAllowed(self.bash_payload(f"cat {self.main}/watchdog/__init__.py"))

    def test_bash_copy_from_main_into_worktree(self):
        # cp FROM main INTO the worktree is a READ of main — must not block
        self.assertAllowed(self.bash_payload(f"cp {self.main}/watchdog/x.py ./"))

    def test_bash_grep_main(self):
        self.assertAllowed(self.bash_payload(f"grep -rn foo {self.main}/watchdog"))

    def test_bash_git_log_origin_main(self):
        # "origin/main" is a REF, not the main checkout PATH
        self.assertAllowed(self.bash_payload("git log origin/main..HEAD --oneline"))

    def test_bash_git_C_own_worktree(self):
        self.assertAllowed(self.bash_payload(f"git -C {self.wt} commit -am wip"))

    def test_bash_gh_issue_comment_names_main_in_quoted_body(self):
        # a quoted body that mentions the main path is inert payload, not a write
        cmd = f'gh issue comment 496 --body "touched {self.main}/watchdog/x"'
        self.assertAllowed(self.bash_payload(cmd))

    # --- the override escape hatch --------------------------------------------
    def test_override_env_allows(self):
        p = self.write_payload("Write",
                               os.path.join(self.main, "watchdog", "wedge.py"))
        self.assertAllowed(p, env_extra={"AIRULESET_ALLOW_WORKTREE_ESCAPE": "1"})

    def test_bash_bypass_marker_allows(self):
        cmd = (f"cd {self.main} && git commit -am fix  # airuleset:worktree-ok "
               "supervisor rescue")
        self.assertAllowed(self.bash_payload(cmd))


class NoFalsePositiveOnLegitContexts(WorktreeGuardBase):
    # --- MAIN session (no agent_id) is the supervisor's own integration work --
    def test_main_session_write_into_checkout_allowed(self):
        p = self.write_payload(
            "Write", os.path.join(self.main, "watchdog", "x.py"),
            agent_id=None)
        self.assertAllowed(p)

    def test_main_session_git_commit_in_checkout_allowed(self):
        # a main session with no agent_id, transcript is airuleset's own session
        p = self.bash_payload(f"cd {self.main} && git commit -am integrate",
                              agent_id=None)
        self.assertAllowed(p)

    # --- a SUBAGENT with NO worktree cwd is the serial-fallback dispatch -------
    def test_serial_fallback_subagent_on_shared_tree_allowed(self):
        # agent_id present, but cwd is the plain checkout (no worktree) — the
        # documented serial fallback legitimately works the shared tree.
        p = self.write_payload(
            "Write", os.path.join(self.main, "watchdog", "x.py"),
            cwd=self.main)
        self.assertAllowed(p)

    def test_serial_fallback_subagent_bash_commit_allowed(self):
        p = self.bash_payload(f"cd {self.main} && git commit -am wip",
                              cwd=self.main)
        self.assertAllowed(p)


class RuleAStillWorks(WorktreeGuardBase):
    """RULE B must not regress RULE A (foreign-session airuleset write)."""

    RS_TR = ("/home/newlevel/.claude/projects/-home-newlevel-devel-restreamer/"
             "8125adb8-0000-0000-0000-000000000000.jsonl")

    def test_foreign_session_commit_into_airuleset_blocked(self):
        # no agent_id (a foreign MAIN session), transcript = restreamer
        p = {"tool_name": "Bash",
             "tool_input": {"command": "git -C /home/newlevel/devel/airuleset push"},
             "cwd": "/home/newlevel/devel/restreamer",
             "transcript_path": self.RS_TR}
        r = self.run_hook(p)
        self.assertEqual(r.returncode, 2, f"stderr={r.stderr}")
        self.assertIn("ticket", r.stderr.lower())

    def test_airuleset_session_own_bash_allowed(self):
        # airuleset session, no agent_id, a normal git commit in its own repo
        p = {"tool_name": "Bash",
             "tool_input": {"command": "git commit -am work"},
             "cwd": "/home/newlevel/devel/airuleset",
             "transcript_path": AR_TR}
        self.assertAllowed(p)


class BashHardeningR1(WorktreeGuardBase):
    """Round-1 adversarial-review hardening (#496): multi-option git, plain
    copy-family write utilities with a main DESTINATION, and the path-boundary
    fix that stops a sibling `<main>-backup` from being read as the main path."""

    # --- newly BLOCKED: multi-option git targeting main ----------------------
    def test_bash_git_multi_option_gitdir_worktree(self):
        p = self.bash_payload(
            f"git --git-dir={self.main}/.git --work-tree={self.main} add -A")
        self.assertBlocked(p)

    # --- newly BLOCKED: plain copy-family write utilities INTO main -----------
    def test_bash_cp_into_main(self):
        self.assertBlocked(self.bash_payload(
            f"cp ./x.py {self.main}/watchdog/x.py"))

    def test_bash_mv_into_main(self):
        self.assertBlocked(self.bash_payload(
            f"mv ./x.py {self.main}/watchdog/x.py"))

    def test_bash_install_into_main(self):
        self.assertBlocked(self.bash_payload(
            f"install -m644 ./x.py {self.main}/watchdog/x.py"))

    def test_bash_dd_into_main(self):
        self.assertBlocked(self.bash_payload(
            f"dd if=./x.py of={self.main}/watchdog/x.py"))

    def test_bash_cp_main_to_main(self):
        self.assertBlocked(self.bash_payload(
            f"cp {self.main}/a.py {self.main}/b.py"))

    # --- STILL ALLOWED: copy FROM main is a read (dest is the worktree) -------
    def test_bash_cp_from_main_trailing_dot(self):
        self.assertAllowed(self.bash_payload(f"cp {self.main}/watchdog/x.py ."))

    def test_bash_cp_from_main_into_relative_dir(self):
        self.assertAllowed(self.bash_payload(f"cp {self.main}/watchdog/x.py ./here/"))

    def test_bash_rsync_from_main(self):
        self.assertAllowed(self.bash_payload(f"rsync -a {self.main}/watchdog/ ./local/"))

    # --- STILL ALLOWED: a sibling dir sharing the main path as a prefix -------
    def test_bash_redirect_into_sibling_backup_allowed(self):
        self.assertAllowed(self.bash_payload(f"echo hi > {self.main}-backup/x.py"))

    def test_bash_git_C_sibling_backup_allowed(self):
        self.assertAllowed(self.bash_payload(f"git -C {self.main}-backup commit -am x"))

    # --- STILL ALLOWED: read-only git via -C main ----------------------------
    def test_bash_git_C_main_log_allowed(self):
        self.assertAllowed(self.bash_payload(f"git -C {self.main} log --oneline"))

    def test_bash_git_C_main_status_allowed(self):
        self.assertAllowed(self.bash_payload(f"git -C {self.main} status"))

    # --- STILL ALLOWED: own-worktree git via -C, and bypass consistency ------
    def test_bash_git_C_own_worktree_add_allowed(self):
        self.assertAllowed(self.bash_payload(f"git -C {self.wt} add -A"))

    def test_bash_marker_inside_quoted_body_still_blocks(self):
        # the marker only counts OUTSIDE quotes now (like RULE A's foreign-ok):
        # a marker WRITTEN INTO a main file must NOT disable the guard.
        p = self.bash_payload(
            f'echo "airuleset:worktree-ok" > {self.main}/watchdog/x.py')
        self.assertBlocked(p)


if __name__ == "__main__":
    main()
