"""Blind-label-flip gate (#1056 L1 (d) / #1057 item 3):
gates/labeledit.py + hooks/block-blind-label-flip.sh.

RED-first. Unit: `classify_command` blocks a reduced-authority
`--remove-label prio:bounce` / `--add-label ready-for-review` when gk-watch says
`bounce-unanswered` with no commit since the verdict, and PASSES on unknown
(fail-open) / rfr-current / commit-since-verdict / a non-risky edit. Hook-level:
the real adapter blocks (exit 2) / allows (exit 0) via an injected gk-watch
fixture, degrades-to-allow on a full box, honours the bypass, and fails CLOSED
on a python malfunction.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import airuleset  # noqa: E402
from gates import labeledit  # noqa: E402
from tests._hook_state_cleanup import hermetic_hook_env  # noqa: E402

HOOK = REPO_ROOT / "hooks" / "block-blind-label-flip.sh"


def _bounce(head_ts=None, gk_ts=1000.0):
    return {"state": "bounce-unanswered",
            "gk_latest": {"id": 5699982377, "verdict": "BOUNCE",
                          "created_at": gk_ts, "sha": "abc1234", "ids": ["1", "2"]},
            "head_ts": head_ts, "age_seconds": 9 * 3600}


class ClassifyCommand(unittest.TestCase):
    def _one(self, cmd, watch):
        return labeledit.classify_command(cmd, cwd="/x",
                                          watch_fn=lambda i, c: watch)

    def test_remove_bounce_blocked_when_bounce_unanswered(self):
        r = self._one("gh issue edit 5613 --remove-label prio:bounce", _bounce())
        self.assertEqual(r[0]["verdict"], "BLOCK")
        self.assertEqual(r[0]["ticket"], "5613")

    def test_add_ready_for_review_blocked(self):
        r = self._one("gh issue edit 6890 --add-label ready-for-review", _bounce())
        self.assertEqual(r[0]["verdict"], "BLOCK")

    def test_dash_l_add_form_blocked(self):
        r = self._one("gh issue edit 6890 -l ready-for-review", _bounce())
        self.assertEqual(r[0]["verdict"], "BLOCK")

    def test_unknown_fails_open(self):
        r = self._one("gh issue edit 5613 --remove-label prio:bounce",
                      {"state": "unknown"})
        self.assertEqual(r[0]["verdict"], "PASS")
        self.assertEqual(r[0]["reason"], "unresolvable")

    def test_rfr_current_passes(self):
        r = self._one("gh issue edit 5613 --remove-label prio:bounce",
                      {"state": "rfr-current"})
        self.assertEqual(r[0]["verdict"], "PASS")

    def test_commit_since_verdict_passes(self):
        r = self._one("gh issue edit 5613 --remove-label prio:bounce",
                      _bounce(head_ts=2000.0, gk_ts=1000.0))
        self.assertEqual(r[0]["verdict"], "PASS")
        self.assertEqual(r[0]["reason"], "commit-since-verdict")

    def test_non_risky_edit_ignored(self):
        # removing ready-for-review / adding prio:bounce is NOT a blind flip
        r = self._one("gh issue edit 5613 --add-label prio:bounce", _bounce())
        self.assertEqual(r, [])

    def test_none_state_fails_open(self):
        r = labeledit.classify_command(
            "gh issue edit 5613 --remove-label prio:bounce", cwd="/x",
            watch_fn=lambda i, c: None)
        self.assertEqual(r[0]["verdict"], "PASS")


class HookLevel(unittest.TestCase):
    def _run(self, cmd, fixture=None, authority="fork-no-merge",
             broken_python=False):
        payload = json.dumps({"tool_input": {"command": cmd},
                              "session_id": "test-labeledit"})
        env = hermetic_hook_env(self)
        run_cwd = tempfile.mkdtemp(prefix="labeledit-cwd-")
        self.addCleanup(shutil.rmtree, run_cwd, True)
        if authority is not None:
            profile = airuleset.AUTHORITY_BY_USER.get("david2")  # fork-no-merge
            Path(run_cwd, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=%s -->\n" % (profile or authority),
                encoding="utf-8")
        if fixture is not None:
            fx = Path(run_cwd, "fx.json")
            fx.write_text(json.dumps(fixture))
            env["AIRULESET_GK_WATCH_FIXTURE"] = str(fx)
        if broken_python:
            tmpbin = tempfile.mkdtemp(prefix="nopy-")
            self.addCleanup(shutil.rmtree, tmpbin, True)
            for name in os.listdir("/usr/bin"):
                if name.startswith("python3"):
                    continue
                try:
                    os.symlink(os.path.join("/usr/bin", name),
                               os.path.join(tmpbin, name))
                except OSError as e:  # airuleset:script-ok best-effort test symlink
                    print("symlink skipped %s: %r" % (name, e))
            shim = Path(tmpbin, "python3")
            shim.write_text("#!/usr/bin/env bash\nexit 42\n")
            shim.chmod(0o755)
            env["PATH"] = tmpbin + ":" + env.get("PATH", "")
        return subprocess.run(["bash", str(HOOK)], input=payload,
                              capture_output=True, text=True, env=env, cwd=run_cwd)

    def test_blocks_blind_remove_of_bounce(self):
        r = self._run("gh issue edit 5613 --remove-label prio:bounce",
                      fixture={"5613": _bounce()})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("5613", r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_allows_when_gk_watch_unknown(self):
        r = self._run("gh issue edit 5613 --remove-label prio:bounce",
                      fixture={"5613": {"state": "unknown"}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_when_commit_since_verdict(self):
        r = self._run("gh issue edit 5613 --remove-label prio:bounce",
                      fixture={"5613": _bounce(head_ts=2000.0, gk_ts=1000.0)})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_full_authority_never_gated(self):
        # #1056 review R1: a marker can only LOWER authority, so a full box can't
        # be forced hermetically via a marker; a reduced ambient box would
        # legitimately BLOCK. Guard: only exercise the full-authority bypass when
        # the ambient box actually resolves to full (the fleet integrates on such
        # a box), else skip rather than false-fail.
        import tempfile as _tf
        probe = _tf.mkdtemp(prefix="labeledit-authprobe-")
        self.addCleanup(shutil.rmtree, probe, True)
        if airuleset.resolve_authority(probe) != "full":
            self.skipTest("ambient box is reduced-authority; full path unexercisable here")
        r = self._run("gh issue edit 5613 --remove-label prio:bounce",
                      fixture={"5613": _bounce()}, authority=None)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bypass_marker_allows(self):
        r = self._run("gh issue edit 5613 --remove-label prio:bounce  "
                      "# airuleset:labeledit-ok owner said so",
                      fixture={"5613": _bounce()})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_python_malfunction_fails_closed(self):
        r = self._run("gh issue edit 5613 --remove-label prio:bounce",
                      fixture={"5613": _bounce()}, broken_python=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fail-closed", r.stderr)


if __name__ == "__main__":
    unittest.main()
