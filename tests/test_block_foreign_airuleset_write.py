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
