"""hooks/block-foreign-airuleset-write.sh — only the airuleset SESSION writes
the airuleset repo.

2026-07-23 incident: the user mistyped an airuleset complaint into the
RESTREAMER session (wrong tmux window) — and that session went ahead, fixed
watchdog code in ~/devel/airuleset, committed and DEPLOYED via airuleset.py
push, with the airuleset stream learning about it only afterwards ("nepaci sa
mi to"). The wanted behavior: a foreign project session files a ticket in the
airuleset repo (or tells the user they typed into the wrong window) — it never
commits/pushes there itself.

The hook blocks WRITE git ops + airuleset.py push/install targeting any
*/devel/airuleset checkout unless the SESSION ITSELF is an airuleset session
(payload transcript_path encodes the launch dir; CLAUDE_PROJECT_DIR accepted
as a secondary signal). Read ops, the sanctioned airuleset.py CLI surface
(notify/share/tickets-status/fable-gate/…) and gh issue traffic stay open.
"""

import json
import os
import subprocess
from pathlib import Path
from unittest import TestCase, main

HOOK = (Path(__file__).resolve().parent.parent / "hooks"
        / "block-foreign-airuleset-write.sh")

AR_TR = ("/home/newlevel/.claude/projects/-home-newlevel-devel-airuleset/"
         "2d02a127-0000-0000-0000-000000000000.jsonl")
RS_TR = ("/home/newlevel/.claude/projects/-home-newlevel-devel-restreamer/"
         "8125adb8-0000-0000-0000-000000000000.jsonl")
AR = "/home/newlevel/devel/airuleset"
RS = "/home/newlevel/devel/restreamer"


def run(cmd, cwd=RS, transcript=RS_TR, env_extra=None):
    payload = json.dumps({"tool_input": {"command": cmd}, "cwd": cwd,
                          "transcript_path": transcript})
    env = {"PATH": "/usr/bin:/bin"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["bash", str(HOOK)], input=payload,
                          capture_output=True, text=True, env=env)


class ForeignSessionBlocked(TestCase):
    def assertBlocked(self, cmd, **kw):
        r = run(cmd, **kw)
        self.assertEqual(r.returncode, 2,
                         f"expected BLOCK for: {cmd}\nstderr={r.stderr}")
        self.assertIn("ticket", r.stderr.lower())     # redirect instruction

    def assertAllowed(self, cmd, **kw):
        r = run(cmd, **kw)
        self.assertEqual(r.returncode, 0,
                         f"expected ALLOW for: {cmd}\nstderr={r.stderr}")

    # --- the incident shapes ------------------------------------------------
    def test_commit_in_airuleset_cwd_from_foreign_session(self):
        self.assertBlocked("git add -A && git commit -m 'fix watchdog'",
                           cwd=AR)

    def test_git_dash_c_push_from_foreign_cwd(self):
        self.assertBlocked("git -C /home/newlevel/devel/airuleset push")

    def test_airuleset_py_push_from_foreign_session(self):
        self.assertBlocked("python3 ~/devel/airuleset/airuleset.py push")

    def test_airuleset_py_install_from_foreign_session(self):
        self.assertBlocked("python3 /home/newlevel/devel/airuleset/airuleset.py install")

    def test_cd_then_commit(self):
        self.assertBlocked("cd ~/devel/airuleset && git commit -am wip")

    def test_subdev_checkout_also_guarded(self):
        self.assertBlocked("git -C /home/montalu/devel/airuleset pull")

    # --- what must STAY OPEN ------------------------------------------------
    def test_sanctioned_cli_surface_allowed(self):
        for c in ("python3 ~/devel/airuleset/airuleset.py notify --run-card --repo x/y --issue 1",
                  "python3 ~/devel/airuleset/airuleset.py share /tmp/f.png",
                  "python3 ~/devel/airuleset/airuleset.py tickets-status --refresh --cwd /x",
                  "python3 ~/devel/airuleset/airuleset.py fable-gate",
                  "python3 ~/devel/airuleset/airuleset.py authority"):
            self.assertAllowed(c)

    def test_gh_issue_traffic_allowed(self):
        self.assertAllowed("gh issue create -R zbynekdrlik/airuleset -t T -F b.md")
        self.assertAllowed("gh issue comment 26 -R zbynekdrlik/airuleset -F c.md")

    def test_read_ops_allowed(self):
        self.assertAllowed("git -C ~/devel/airuleset log --oneline -5")
        self.assertAllowed("git status", cwd=AR)
        self.assertAllowed("git -C ~/devel/airuleset fetch origin")

    def test_non_airuleset_writes_untouched(self):
        self.assertAllowed("git add -A && git commit -m x && git push", cwd=RS)

    # --- the airuleset session itself is free -------------------------------
    def test_airuleset_session_commits_freely(self):
        r = run("git add -A && git commit -m x", cwd=AR, transcript=AR_TR)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run("python3 airuleset.py push", cwd=AR, transcript=AR_TR)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_claude_project_dir_env_also_identifies(self):
        r = run("git commit -am x", cwd=AR, transcript="",
                env_extra={"CLAUDE_PROJECT_DIR": AR})
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- escape hatches ------------------------------------------------------
    def test_marker_bypass(self):
        r = run("git -C ~/devel/airuleset push  # airuleset:foreign-ok emergency",
                cwd=RS)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_identity_fields_fails_open(self):
        payload = json.dumps({"tool_input": {"command": "git commit -am x"}})
        r = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(r.returncode, 0, r.stderr)


class ForeignBypassLogPerUser492(TestCase):
    """#492 sibling: block-foreign-airuleset-write.sh wrote its bypass log to
    the FIXED /tmp/airuleset-foreign-write-bypass.log — the same shared-name
    cross-user collision + `Permission denied` stderr leak as the main guard.
    Fix: per-user (`-<uid>`) path, and a brace-group redirect so it can never
    leak even when the per-uid file is itself unwritable."""

    UID = os.getuid()
    PER_USER = Path("/tmp/airuleset-foreign-write-bypass-%d.log" % UID)
    FIXED = Path("/tmp/airuleset-foreign-write-bypass.log")

    def _make_unwritable(self, p):
        if os.geteuid() == 0:
            self.skipTest("root bypasses file permission bits; cannot reproduce EACCES")
        saved = (p.read_bytes(), p.stat().st_mode) if p.exists() else None

        def restore():
            try:
                if p.exists():
                    os.chmod(p, 0o644)
                    p.unlink()
                if saved is not None:
                    p.write_bytes(saved[0])
                    os.chmod(p, saved[1])
            except OSError:
                # airuleset:script-ok best-effort test-cleanup restore of a
                # shared /tmp telemetry file; must not mask the test result.
                pass
        self.addCleanup(restore)
        p.write_text("")
        os.chmod(p, 0o444)

    def test_bypass_no_leak_and_logged_to_per_user_path(self):
        # RED on old code (writes to the fixed 0444 path -> leaks); GREEN
        # after fix (per-uid path, never touches the foreign-owned file).
        self._make_unwritable(self.FIXED)
        tag = "issue492probe" + os.urandom(3).hex()
        r = run("git -C ~/devel/airuleset push  # airuleset:foreign-ok %s" % tag,
                cwd=RS)
        self.assertEqual(r.returncode, 0, r.stderr)         # bypass honored
        self.assertNotIn("Permission denied", r.stderr, r.stderr)
        self.assertTrue(self.PER_USER.exists(),
                        "per-user foreign bypass log missing: %s" % self.PER_USER)
        self.assertIn(tag, self.PER_USER.read_text())


class AutopilotLockComposite790(TestCase):
    """#790: a composite Bash command that combines a legitimate
    `airuleset.py autopilot-lock acquire/release --repo <foreign-repo>` call
    with git write ops targeting that SAME foreign repo (never airuleset)
    false-blocked, because the old RULE A logic set TARGETS=1 from the
    substring "devel/airuleset" appearing in the PATH to airuleset.py itself,
    then matched a git write verb ANYWHERE in the whole composite string with
    no check that THAT git invocation's own target was airuleset. autopilot-
    lock is called routinely right alongside the integration git steps of a
    foreign-repo release cycle, so this pattern recurs every cycle.

    These reproduce the exact incident shapes from a foreign (non-airuleset)
    session and must be ALLOWED — no segment in them writes */devel/airuleset.
    """

    FOREIGN_CWD = "/home/montalu1/devel/odoo/odoo-slovnormal"
    GK_CWD = "/home/gatekeeper/devel/odoo/odoo-erp"

    def assertAllowed(self, cmd, **kw):
        r = run(cmd, **kw)
        self.assertEqual(r.returncode, 0,
                         f"expected ALLOW for: {cmd}\nstderr={r.stderr}")

    def test_lock_acquire_then_cd_then_fetch_merge_foreign_repo(self):
        # montalu1 incident 1 (2026-08-31)
        self.assertAllowed(
            "python3 ~/devel/airuleset/airuleset.py autopilot-lock acquire "
            "--repo /home/montalu1/devel/odoo/odoo-slovnormal && "
            "cd /tmp/wt-5646 && "
            "git fetch origin develop && "
            "git merge origin/develop",
            cwd=self.FOREIGN_CWD)

    def test_lock_release_then_dash_c_worktree_remove_then_push_foreign_repo(self):
        # montalu1 incident 2 (2026-08-31)
        self.assertAllowed(
            "python3 ~/devel/airuleset/airuleset.py autopilot-lock release "
            "--repo /home/montalu1/devel/odoo/odoo-slovnormal ; "
            "git -C /home/montalu1/devel/odoo/odoo-slovnormal worktree remove "
            "/tmp/wt-5646 ; "
            "git push origin :refs/autopilot-wip/worktree-foo",
            cwd=self.FOREIGN_CWD)

    def test_lock_acquire_then_push_foreign_repo(self):
        # gk odoo-erp incident (2026-09-01, integration cycle #5687 -> PR #5699)
        self.assertAllowed(
            "python3 ~/devel/airuleset/airuleset.py autopilot-lock acquire "
            "--repo /home/gatekeeper/devel/odoo/odoo-erp && "
            "git push -u origin gatekeeper/5687-something",
            cwd=self.GK_CWD)

    def test_push_delete_autopilot_wip_ref_then_lock_release_foreign_repo(self):
        # gk odoo-erp incident, second shape from the same integration cycle
        self.assertAllowed(
            "git push origin --delete refs/autopilot-wip/worktree-foo ; "
            "python3 ~/devel/airuleset/airuleset.py autopilot-lock release "
            "--repo /home/gatekeeper/devel/odoo/odoo-erp",
            cwd=self.GK_CWD)

    # --- protection must STILL hold: a REAL airuleset write in the same
    # composite as an autopilot-lock call is STILL blocked -----------------
    def test_lock_call_plus_genuine_airuleset_write_still_blocked(self):
        r = run(
            "python3 ~/devel/airuleset/airuleset.py autopilot-lock acquire "
            "--repo /home/montalu1/devel/odoo/odoo-slovnormal && "
            "cd ~/devel/airuleset && git commit -am 'sneaky'",
            cwd=self.FOREIGN_CWD)
        self.assertEqual(r.returncode, 2,
                         f"expected BLOCK for genuine airuleset write: {r.stderr}")

    def test_lock_call_plus_dash_c_airuleset_push_still_blocked(self):
        r = run(
            "python3 ~/devel/airuleset/airuleset.py autopilot-lock release "
            "--repo /home/montalu1/devel/odoo/odoo-slovnormal ; "
            "git -C /home/newlevel/devel/airuleset push",
            cwd=self.FOREIGN_CWD)
        self.assertEqual(r.returncode, 2,
                         f"expected BLOCK for genuine airuleset write: {r.stderr}")

    def test_newline_separated_genuine_write_still_blocked(self):
        # review finding on #790: shlex(posix=True) treats a literal newline
        # as plain whitespace, so a multi-line composite silently merged into
        # ONE token stream and a newline-separated airuleset write went
        # UNDETECTED. Multi-line Bash commands are a common shape, so this
        # was a genuine protection regression, not an exotic residual.
        r = run("cd ~/devel/airuleset\ngit add -A\ngit commit -m fix",
                cwd=self.FOREIGN_CWD)
        self.assertEqual(r.returncode, 2,
                         f"expected BLOCK for newline-separated write: {r.stderr}")

    def test_newline_separated_dash_c_push_still_blocked(self):
        r = run("echo syncing\ngit -C /home/newlevel/devel/airuleset push",
                cwd=self.FOREIGN_CWD)
        self.assertEqual(r.returncode, 2,
                         f"expected BLOCK for newline-separated write: {r.stderr}")

    # --- new over-block class the fix must NOT introduce --------------------
    def test_airuleset_py_push_mentioned_as_plain_argument_not_blocked(self):
        # review finding on #790: `airuleset.py push` appearing merely as an
        # unquoted ARGUMENT of some other command (not as the invoked
        # program) must NOT block — the cousin of the original whole-string
        # TARGETS bug this fix set out to remove.
        self.assertAllowed("echo airuleset.py push", cwd=self.FOREIGN_CWD)
        self.assertAllowed("echo Run airuleset.py push on dev1",
                           cwd=self.FOREIGN_CWD)


class HookWired(TestCase):
    def test_registered_in_settings_fragment(self):
        cfg = json.loads((Path(__file__).resolve().parent.parent / "settings"
                          / "hooks.json").read_text())
        cmds = [h.get("command", "") for blk in cfg["hooks"]["PreToolUse"]
                if blk.get("matcher") == "Bash" for h in blk.get("hooks", [])]
        self.assertTrue(any("block-foreign-airuleset-write.sh" in c
                            for c in cmds), cmds)


if __name__ == "__main__":
    main()
