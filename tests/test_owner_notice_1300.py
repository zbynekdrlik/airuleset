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

    def notice(self, slug, proj=None):
        """The map entry of notice `slug` for `proj` (key shape agnostic)."""
        proj = str(proj or self.proj)
        hits = [v for v in self.qmap().values()
                if str(v.get("session", "")).startswith("notice:")
                and str(v.get("session")).endswith(":" + slug)
                and v.get("cwd") == proj]
        return hits[0] if len(hits) == 1 else None

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
        entry = self.notice("restart-needed")
        self.assertIn("Reštartuj Claude na dev2", entry["block"])
        self.assertEqual(entry["cwd"], str(self.proj))
        self.assertEqual(entry["channel"], "")

    def test_notice_is_scoped_to_its_project(self):
        self.send("Box dev2 je za stabilnou verziou.")
        self.assertEqual(self.count(self.proj), 1)
        self.assertEqual(self.count(self.other), 0)

    def test_subdir_cwd_resolves_to_git_root(self):
        r = self.run_notify("--body", "Box bez admin práv.", "--owner-name",
                            "zbynek", "--owner-notice", "no-admin", "--cwd",
                            str(self.proj / "src"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.notice("no-admin"))

    def test_same_slug_overwrites_one_item(self):
        self.send("prvý text")
        self.send("novší text")
        self.assertEqual(self.count(self.proj), 1)
        self.assertIn("novší text", self.notice("restart-needed")["block"])

    def test_clear_removes_the_notice(self):
        self.send("Reštartuj Claude.")
        r = self.run_notify("--clear-owner-notice", "restart-needed",
                            "--cwd", str(self.proj))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.count(self.proj), 0)
        self.assertIsNone(self.notice("restart-needed"))

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


    # --- review round 1 (#1300) ------------------------------------------

    def test_ticket_ref_in_body_still_counts(self):
        for slug, body in (("ref", "Pozri remoteos-mcp #12, treba reštart."),
                           ("tracked", "Reštart. Sledované na #12")):
            self.send(body, slug)
        self.assertEqual(self.count(self.proj), 2)
        cover = statusbar.question_attribution.QuestionCover({12}, rows={12})
        self.assertEqual(statusbar.question_ping_count(
            str(self.proj), home=str(self.home), cover=cover), 2)

    def test_symlinked_cwd_matches_the_session_path(self):
        real = Path(self._tmp.name) / "real"
        (real / "lnproj" / ".git").mkdir(parents=True)
        link = self.home / "work"
        link.symlink_to(real)
        r = self.run_notify("--body", "x", "--owner-name", "zbynek",
                            "--owner-notice", "n1", "--cwd",
                            str(link / "lnproj"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.count(link / "lnproj"), 1)

    def test_question_block_never_returns_a_notice(self):
        import cli_question_block
        self.send("Reštartuj Claude.")
        self.assertIsNone(cli_question_block.newest(self.qmap(), str(self.proj)))
        mine = {"session": "s-1", "cwd": str(self.proj), "asked": 1, "ts": 1,
                "block": "moja otázka"}
        entries = dict(self.qmap(), **{"1": mine})
        self.assertIs(cli_question_block.newest(entries, str(self.proj)), mine)

    def test_same_slug_in_two_projects_keeps_both(self):
        self.send("A potrebuje reštart.")
        r = self.run_notify("--body", "B potrebuje reštart.", "--owner-name",
                            "zbynek", "--owner-notice=restart-needed",
                            "--cwd", str(self.other))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.count(self.proj), 1)
        self.assertEqual(self.count(self.other), 1)
        self.assertIn("A potrebuje", self.notice("restart-needed")["block"])

    def test_dry_run_clear_keeps_the_notice(self):
        self.send("Reštartuj Claude.")
        r = self.run_notify("--clear-owner-notice", "restart-needed",
                            "--cwd", str(self.proj), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.count(self.proj), 1)

    def test_failed_record_exits_nonzero(self):
        (self.home / ".claude" / "discord-questions.json").mkdir(parents=True)
        r = self.send("Reštartuj Claude.")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("NOT recorded", r.stderr)

    def test_record_and_clear_leave_a_delivery_log_line(self):
        self.send("Reštartuj Claude.")
        self.run_notify("--clear-owner-notice", "restart-needed",
                        "--cwd", str(self.proj))
        log = (self.home / ".claude" / "notify-delivery.log").read_text()
        self.assertIn("owner-notice-recorded", log)
        self.assertIn("owner-notice-cleared", log)


if __name__ == "__main__":
    unittest.main()
