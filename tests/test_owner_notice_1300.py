"""#1300 — an unattended tool's owner notice reaches the footer `U`.

`notify --body <text> --owner-notice <slug> [--cwd <project>]`: when the
owner policy (`notify.OWNER_PING_ALLOW`, #1137) suppresses the send, the
notice is recorded as a #716 Discord-less entry (`suppressed:notice:<slug>`)
in the account's ❓ map, scoped to the project's git root, so the footer
`U N` counts it and `core-quals --waiting` lists it.
`notify --clear-owner-notice <slug>` drops it.

Every case runs the REAL CLI in a subprocess under an isolated HOME, so the
live `~/.claude` is never touched and no Discord request is made (no token).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import statusbar  # noqa: E402


class OwnerNoticeCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.proj = self.home / "devel" / "toolproj"
        self.other = self.home / "devel" / "otherproj"
        for p in (self.proj, self.other):
            (p / ".git").mkdir(parents=True)
        (self.proj / "src").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _env(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("DISCORD_", "AIRULESET_", "TMUX"))}
        env["HOME"] = str(self.home)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def run_notify(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(REPO / "airuleset.py"), "notify", *args],
            cwd=str(cwd or self.home), env=self._env(), capture_output=True,
            text=True, timeout=60)

    def qmap(self):
        path = self.home / ".claude" / "discord-questions.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def count(self, cwd):
        return statusbar.question_ping_count(str(cwd), home=str(self.home))

    def send(self, body, slug="restart-needed", owner="zbynek", *extra):
        # `=` form: a slug like `-lead` must reach the slug check, not argparse
        return self.run_notify("--body", body, "--owner-name", owner,
                               "--dedup-key", "tool-check#1",
                               "--owner-notice=" + slug,
                               "--cwd", str(self.proj), *extra)

    def test_suppressed_notice_lands_in_project_u(self):
        r = self.send("Reštartuj Claude na dev2: zmenila sa konfigurácia MCP.")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("suppressed", r.stdout)
        self.assertEqual(self.count(self.proj), 1)
        entry = self.qmap()["suppressed:notice:restart-needed"]
        self.assertIn("Reštartuj Claude na dev2", entry["block"])
        self.assertEqual(entry["cwd"], str(self.proj))
        self.assertEqual(entry["channel"], "")

    def test_notice_is_scoped_to_its_project(self):
        self.send("Box dev2 je za stabilnou verziou.")
        self.assertEqual(self.count(self.other), 0)

    def test_subdir_cwd_resolves_to_git_root(self):
        r = self.run_notify("--body", "Box bez admin práv.", "--owner-name",
                            "zbynek", "--owner-notice", "no-admin", "--cwd",
                            str(self.proj / "src"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.qmap()["suppressed:notice:no-admin"]["cwd"],
                         str(self.proj))

    def test_same_slug_overwrites_one_item(self):
        self.send("prvý text")
        self.send("novší text")
        self.assertEqual(self.count(self.proj), 1)
        self.assertIn("novší text",
                      self.qmap()["suppressed:notice:restart-needed"]["block"])

    def test_clear_removes_the_notice(self):
        self.send("Reštartuj Claude.")
        r = self.run_notify("--clear-owner-notice", "restart-needed",
                            "--cwd", str(self.proj))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.count(self.proj), 0)
        self.assertNotIn("suppressed:notice:restart-needed", self.qmap())

    def test_clear_of_another_projects_slug_keeps_it(self):
        self.send("Reštartuj Claude.")
        r = self.run_notify("--clear-owner-notice", "restart-needed",
                            "--cwd", str(self.other))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.count(self.proj), 1)

    def test_delivered_owner_records_nothing(self):
        r = self.send("Notice pre davida.", "restart-needed", "david")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("suppressed", r.stdout)
        self.assertEqual(self.qmap(), {})

    def test_dry_run_records_nothing(self):
        r = self.send("Náhľad.", "restart-needed", "zbynek", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.qmap(), {})

    def test_home_cwd_without_git_root_is_refused_before_sending(self):
        r = self.run_notify("--body", "x", "--owner-name", "zbynek",
                            "--owner-notice", "restart-needed",
                            "--cwd", str(self.home))
        self.assertEqual(r.returncode, 1)
        self.assertIn("--owner-notice", r.stderr)
        self.assertEqual(self.qmap(), {})
        log = self.home / ".claude" / "notify-delivery.log"
        self.assertFalse(log.exists() and "suppressed" in log.read_text())

    def test_bad_slug_is_refused(self):
        for slug in ("Bad Slug", "-lead", "a/b", "x" * 65):
            r = self.send("x", slug)
            self.assertEqual(r.returncode, 1, slug)
        self.assertEqual(self.qmap(), {})

    def test_owner_notice_without_body_is_refused(self):
        r = self.run_notify("--owner-notice", "restart-needed",
                            "--cwd", str(self.proj))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.qmap(), {})

    def test_answered_question_prune_keeps_the_notice(self):
        self.send("Reštartuj Claude.")
        code = ("import sys, time; sys.path.insert(0, %r);"
                "from watchdog.questions import prune_answered_questions as p;"
                "print(p(time.time() + 3600, projects_dir=%r))"
                % (str(REPO), str(self.home / ".claude" / "projects")))
        r = subprocess.run([sys.executable, "-c", code], env=self._env(),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.count(self.proj), 1)


if __name__ == "__main__":
    unittest.main()
