"""#1059 — post-push-ci-cleanup.sh must cancel only SUPERSEDED runs of the
branch that was actually PUSHED, and only `push`/`pull_request` runs.

Two live defects the hook had (main 2db2c73d, v0.1.367):

  1. No event filter — a deliberate `workflow_dispatch` canary (odoo-erp #7430:
     run 35167652784 on an older ancestor sha) was cancelled by a fix-forward
     push, because the candidate filter kept EVERY in-flight ancestor run
     regardless of event.
  2. Branch = the session's cwd HEAD, not the pushed ref — `git -C <worktree>
     push origin <lane>` (or `git push origin HEAD:refs/autopilot-wip/…`) listed
     and cancelled runs of the cwd branch; on gk the infra session sat on
     `develop`, so 136× `CI: cancelled … on develop` killed develop runs on
     pushes that never touched develop.

These tests reuse the fake-`gh` harness style of
`tests/test_airuleset.py::TestPostPushCiCleanupHook`, but with a fake `gh`
that ALSO records which `--branch` `gh run list` was called with — so a wrong
branch is observable, not just a wrong cancel.
"""

import json
import os
import stat
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)


class TestPostPushCiCleanup1059(TestCase):
    HOOK = Path(__file__).resolve().parent.parent / "hooks" / "post-push-ci-cleanup.sh"

    # --- harness ------------------------------------------------------------

    def _root(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        bind = os.path.join(root, "bin")
        os.makedirs(bind)
        return root, bind

    def _new_repo(self, root, name, branch="dev", landed=True):
        """A git repo with two commits (OLD ancestor -> HEAD) on `branch`. When
        `landed`, refs/remotes/origin/<branch> == HEAD (a push that landed).
        Returns (repo_path, head_sha, old_sha)."""
        repo = os.path.join(root, name)
        os.makedirs(repo)

        def g(*a):
            return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True)

        g("init", "-q", "-b", branch)
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        open(os.path.join(repo, "f"), "w").write("a\n")
        g("add", "f")
        g("commit", "-qm", "a")
        old = g("rev-parse", "HEAD").stdout.strip()
        open(os.path.join(repo, "f"), "a").write("b\n")
        g("commit", "-qam", "b")
        head = g("rev-parse", "HEAD").stdout.strip()
        tip = head if landed else old
        g("update-ref", "refs/remotes/origin/%s" % branch, tip)
        return repo, head, old

    def _paths(self, root):
        return {
            "branchq": os.path.join(root, "branchq"),
            "runlist": os.path.join(root, "runlist.json"),
            "cancels": os.path.join(root, "cancels"),
            "force": os.path.join(root, "force"),
        }

    def _runlist(self, entries):
        """entries: list of (id, status, sha, event)."""
        return json.dumps([
            {"databaseId": i, "status": s, "headSha": sha, "event": ev}
            for (i, s, sha, ev) in entries
        ])

    def _write_gh(self, bind, paths, view_status="in_progress"):
        branchq = paths["branchq"]
        runlist = paths["runlist"]
        cancels = paths["cancels"]
        force = paths["force"]
        gh = os.path.join(bind, "gh")
        with open(gh, "w") as fh:
            fh.write(f'''#!/usr/bin/env bash
if [ "$1 $2" = "repo view" ]; then echo '{{"name":"x"}}'; exit 0; fi
if [ "$1 $2" = "run list" ]; then
  br=""
  shift 2
  while [ $# -gt 0 ]; do
    if [ "$1" = "--branch" ]; then br="$2"; shift 2; continue; fi
    shift
  done
  echo "$br" >> "{branchq}"
  cat "{runlist}"
  exit 0
fi
if [ "$1 $2" = "run cancel" ]; then echo "$3" >> "{cancels}"; exit 0; fi
if [ "$1 $2" = "run view" ]; then echo "{view_status}"; exit 0; fi
if [ "$1" = "api" ]; then echo "$2" >> "{force}"; exit 0; fi
exit 0
''')
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def _env(self, bind):
        env = hermetic_hook_env(self)
        env["PATH"] = bind + os.pathsep + env["PATH"]
        return env

    def _run(self, command, cwd, env):
        payload = json.dumps({"tool_input": {"command": command}})
        return subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                              capture_output=True, cwd=cwd, env=env, timeout=30)

    def _lines(self, path):
        if not os.path.exists(path):
            return []
        return [ln.strip() for ln in open(path).read().splitlines() if ln.strip()]

    # --- (a) event allowlist ------------------------------------------------

    def test_workflow_dispatch_ancestor_kept_pull_request_ancestor_cancelled(self):
        """A deliberate workflow_dispatch canary on an older ancestor sha must
        survive a landed push; a pull_request run on the SAME older sha is a
        superseded CI run and IS cancelled (the allowlist proof, #7430)."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (111, "in_progress", old, "workflow_dispatch"),  # canary — KEEP
            (222, "in_progress", old, "pull_request"),       # superseded — CANCEL
            (333, "in_progress", head, "push"),              # current run — KEEP
        ]))
        self._write_gh(bind, paths)
        r = self._run("git push origin dev", repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["cancels"]), ["222"],
                         "only the pull_request ancestor may be cancelled, never "
                         "the workflow_dispatch canary")
        self.assertIn("cancelled 1 superseded", r.stdout)
        self.assertEqual(self._lines(paths["branchq"]), ["dev"])

    def test_unknown_and_schedule_events_are_never_cancelled(self):
        """Any event outside the (push, pull_request) allowlist — schedule,
        repository_dispatch, merge_group, unknown/missing — is fail-safe kept."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (11, "in_progress", old, "schedule"),
            (12, "in_progress", old, "repository_dispatch"),
            (13, "in_progress", old, "merge_group"),
            (14, "in_progress", old, ""),          # missing/unknown event
        ]))
        self._write_gh(bind, paths)
        r = self._run("git push origin dev", repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["cancels"]), [],
                         "no non-allowlisted event may be cancelled")

    # --- (b) pushed-ref, not cwd HEAD --------------------------------------

    def test_dash_C_push_targets_pushed_lane_not_cwd_develop(self):
        """`git -C <worktree> push origin lane` while the session cwd sits on
        develop must list+cancel runs of LANE, never develop (the gk 136×
        `cancelled … on develop` incident)."""
        root, bind = self._root()
        cwd_repo, _, _ = self._new_repo(root, "cwd", branch="develop")
        lane_repo, lane_head, lane_old = self._new_repo(root, "lane", branch="lane")
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (911, "in_progress", lane_old, "push"),    # lane superseded — CANCEL
            (912, "in_progress", lane_head, "push"),   # lane current — KEEP
        ]))
        self._write_gh(bind, paths)
        r = self._run(f"git -C {lane_repo} push origin lane", cwd_repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["branchq"]), ["lane"],
                         "gh run list must be scoped to the PUSHED lane, not the "
                         "cwd branch develop")
        self.assertNotIn("develop", self._lines(paths["branchq"]))
        self.assertEqual(self._lines(paths["cancels"]), ["911"])
        self.assertIn("on lane", r.stdout)

    def test_head_colon_branch_resolves_dst_branch(self):
        """`git push origin HEAD:lane` resolves the branch from the refspec dst,
        and cancels lane's superseded run (src=HEAD -> rev-parse HEAD)."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        # simulate that the pushed dst branch `lane` landed at HEAD too
        subprocess.run(["git", "update-ref", "refs/remotes/origin/lane", head],
                       cwd=repo)
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (711, "in_progress", old, "push"),
            (712, "in_progress", head, "push"),
        ]))
        self._write_gh(bind, paths)
        r = self._run("git push origin HEAD:lane", repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["branchq"]), ["lane"])
        self.assertEqual(self._lines(paths["cancels"]), ["711"])

    # --- (c) refs/autopilot-wip is not a CI branch -------------------------

    def test_autopilot_wip_ref_push_makes_no_gh_run_call(self):
        """The durability backup `git push origin HEAD:refs/autopilot-wip/<x>`
        targets a non-heads ref — no CI branch to clean, so no gh run call and
        no cancel at all."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (111, "in_progress", old, "push"),
        ]))
        self._write_gh(bind, paths)
        r = self._run("git push origin HEAD:refs/autopilot-wip/worktree-x", repo,
                      self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["branchq"]), [],
                         "gh run list must not run for a refs/autopilot-wip push")
        self.assertEqual(self._lines(paths["cancels"]), [],
                         "no run may be cancelled for a refs/autopilot-wip push")
        self.assertEqual(r.stdout.strip(), "",
                         "no monitor instruction for a non-CI-branch push")

    def test_tag_ref_push_makes_no_gh_run_call(self):
        """A tag push (refs/tags/…) is likewise not a CI branch to supersede."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (111, "in_progress", old, "push"),
        ]))
        self._write_gh(bind, paths)
        r = self._run("git push origin HEAD:refs/tags/v1", repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["branchq"]), [])
        self.assertEqual(self._lines(paths["cancels"]), [])

    # --- (d) bare push unchanged -------------------------------------------

    def test_bare_push_cancels_ancestor_on_current_branch(self):
        """A bare `git push` (no remote, no refspec) keeps today's behaviour:
        the current branch, its superseded ancestor cancelled."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (111, "in_progress", old, "push"),
            (222, "in_progress", head, "push"),
        ]))
        self._write_gh(bind, paths)
        r = self._run("git push", repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["branchq"]), ["dev"])
        self.assertEqual(self._lines(paths["cancels"]), ["111"])

    # --- (e) odd/unparseable shapes never guess a branch -------------------

    def test_force_with_lease_option_not_misparsed_as_branch(self):
        """`--force-with-lease=main:abc` is an OPTION — its `main:abc` must never
        be taken as the pushed refspec/branch. The command reduces to a bare
        push of the current branch (dev), never a push of `main`."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        # a superseded ancestor run tagged as if on main — must NOT be queried/cancelled
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (222, "in_progress", head, "push"),   # only a current-branch HEAD run
        ]))
        self._write_gh(bind, paths)
        r = self._run("git push --force-with-lease=main:abc origin", repo,
                      self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        br = self._lines(paths["branchq"])
        self.assertNotIn("main", br,
                         f"main was extracted from --force-with-lease: {br}")
        self.assertEqual(br, ["dev"],
                         f"the option-only push resolves to the current branch: {br}")
        self.assertEqual(self._lines(paths["cancels"]), [])

    def test_delete_push_is_noop(self):
        """A delete push `git push origin :dev` has no source to supersede."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (111, "in_progress", old, "push"),
        ]))
        self._write_gh(bind, paths)
        r = self._run("git push origin :dev", repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["branchq"]), [])
        self.assertEqual(self._lines(paths["cancels"]), [])

    def test_compound_command_finds_the_git_push_segment(self):
        """A compound `git add -A && git push origin dev` still resolves the push
        segment (not the `git add`), and cancels dev's superseded ancestor."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (111, "in_progress", old, "push"),
        ]))
        self._write_gh(bind, paths)
        r = self._run("git add -A && git push origin dev", repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["branchq"]), ["dev"])
        self.assertEqual(self._lines(paths["cancels"]), ["111"])

    # --- coverage closed from the two adversarial reviews (#1059) ----------

    def test_set_upstream_option_is_skipped(self):
        """`git push -u origin dev` — the -u/--set-upstream flag is skipped, the
        branch resolves from the refspec, dev's ancestor is cancelled."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (111, "in_progress", old, "push"),
        ]))
        self._write_gh(bind, paths)
        r = self._run("git push -u origin dev", repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["branchq"]), ["dev"])
        self.assertEqual(self._lines(paths["cancels"]), ["111"])

    def test_force_plus_refspec_prefix_is_stripped(self):
        """A force refspec `+dev` resolves to branch `dev` (the leading + is
        stripped), and cancels dev's superseded ancestor."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (111, "in_progress", old, "push"),
        ]))
        self._write_gh(bind, paths)
        r = self._run("git push origin +dev", repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["branchq"]), ["dev"])
        self.assertEqual(self._lines(paths["cancels"]), ["111"])

    def test_multiple_refspecs_resolves_first_only(self):
        """`git push origin dev main` resolves the FIRST refspec (dev). A second
        refspec is not handled — fail-safe (a superseded `main` run is missed,
        never a wrong branch cancelled). The fleet pushes one ref per command."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (111, "in_progress", old, "push"),
        ]))
        self._write_gh(bind, paths)
        r = self._run("git push origin dev main", repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["branchq"]), ["dev"])
        self.assertNotIn("main", self._lines(paths["branchq"]))

    def test_non_allowlisted_event_at_head_is_not_cancelled(self):
        """A workflow_dispatch run AT the pushed HEAD sha is never a supersede
        candidate (sha == HEAD) — it must not be cancelled by the push+monitor
        path either."""
        root, bind = self._root()
        repo, head, old = self._new_repo(root, "repo", branch="dev")
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (500, "in_progress", head, "workflow_dispatch"),  # a canary AT head
            (111, "in_progress", old, "push"),                # a real superseded run
        ]))
        self._write_gh(bind, paths)
        r = self._run("git push origin dev", repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # only the superseded push ancestor cancelled; the head canary kept
        self.assertEqual(self._lines(paths["cancels"]), ["111"])
        self.assertNotIn("500", self._lines(paths["cancels"]))

    def test_dash_C_push_that_did_not_land_cancels_nothing(self):
        """`git -C <lane> push origin lane` where the lane's remote tip != HEAD
        (push failed/rejected) lists lane's runs but cancels none — the in-flight
        run may still be the live tip (PUSH_LANDED=0 path for the -C shape)."""
        root, bind = self._root()
        cwd_repo, _, _ = self._new_repo(root, "cwd", branch="develop")
        lane_repo, lane_head, lane_old = self._new_repo(root, "lane", branch="lane",
                                                        landed=False)
        paths = self._paths(root)
        open(paths["runlist"], "w").write(self._runlist([
            (911, "in_progress", lane_old, "push"),
        ]))
        self._write_gh(bind, paths)
        r = self._run(f"git -C {lane_repo} push origin lane", cwd_repo, self._env(bind))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lines(paths["branchq"]), ["lane"])
        self.assertEqual(self._lines(paths["cancels"]), [],
                         "a push that did not land must cancel nothing")
