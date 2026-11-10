"""W-entry gate (#1298): gates/opswait.py + hooks/block-ops-wait-without-waiting-on.sh.

Owner 10.11.2026: sessions park work in W (`ops-wait`) that waits on nobody or
on the owner. Adding `ops-wait` now needs a `Waiting-on: <third party> —
<event> — <check>` line in the same command or in a ticket comment from the
last 30 minutes; the party is never the owner, us or nobody. Fail-open on a gh
read error; bypass `# airuleset:opswait-ok <reason>` (logged).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gates import opswait  # noqa: E402
from tests._hook_state_cleanup import hermetic_hook_env  # noqa: E402

HOOK = REPO_ROOT / "hooks" / "block-ops-wait-without-waiting-on.sh"
NOW = 1_800_000_000.0
GOOD = "Waiting-on: Hetzner support — reply on ticket 4711 — read the ticket"


def _no_read(*_a):
    raise AssertionError("the gh comment read must not run")


def _run(cmd, rows=None):
    return opswait.classify_command(
        cmd, cwd="/x", now=NOW,
        comments_fn=(lambda *_a: rows) if rows is not _no_read else _no_read)


class PartyLists(unittest.TestCase):
    """Content locks: the owner / self / nobody wordings stay explicit."""

    def test_owner_lists(self):
        self.assertEqual(opswait.OWNER_NAME_STEMS, ("zbyn", "drlik"))
        for word in ("owner", "vlastnik", "majitel", "marek", "ty", "you",
                     "user", "pouzivatel"):
            self.assertIn(word, opswait.OWNER_WHOLE_WORDS)

    def test_self_gatekeeper_and_nobody_lists(self):
        for stem in ("session", "autopilot", "subagent"):
            self.assertIn(stem, opswait.SELF_PARTY_STEMS)
        self.assertIn("lane", opswait.SELF_PARTY_WORDS)
        for word in ("us", "we", "claude", "nas", "mna", "worker",
                     "supervisor", "stream"):
            self.assertIn(word, opswait.SELF_WHOLE_WORDS)
        self.assertIn("gatekeeper", opswait.GATEKEEPER_STEMS)
        self.assertIn("gk", opswait.GATEKEEPER_WORDS)
        for word in ("nobody", "nikto", "unknown", "tbd", "someone", "time",
                     "window", "observation", "third", "party"):
            self.assertIn(word, opswait.NOBODY_WHOLE_WORDS)


class Party(unittest.TestCase):
    def test_owner_wordings(self):
        for party in ("zbynek", "Zbyněk", "Zbyňkovi", "vlastníka", "owner",
                      "the owner", "Marek", "majiteľ", "ty", "you", "user",
                      "Drlík"):
            self.assertEqual(opswait.party_problem(party), "owner", party)

    def test_self_wordings(self):
        for party in ("us", "we", "nás", "mňa", "Claude", "this session",
                      "táto session", "autopilot worker", "a lane", "worker",
                      "supervisor", "our session", "another stream"):
            self.assertEqual(opswait.party_problem(party), "self", party)

    def test_gatekeeper_is_a_hand_off(self):
        for party in ("gk", "the gatekeeper", "gk box", "Gatekeeper review"):
            self.assertEqual(opswait.party_problem(party), "gatekeeper", party)

    def test_nobody_wordings(self):
        for party in ("", "nikto", "nobody", "n/a", "TBD", "-", "?", "time",
                      "observation window", "third party", "someone"):
            self.assertEqual(opswait.party_problem(party), "nobody", party)

    def test_third_parties_pass(self):
        for party in ("Hetzner support", "klient montalu", "our client",
                      "tvoj klient", "Claude Code release (Anthropic)",
                      "GitHub", "dodávateľ ESET", "Cloudflare Workers support",
                      "client site supervisor", "upstream repo owner",
                      "vlastník domény (registrátor)", "Marek Novák (montalu)",
                      "airuleset #1298 fix", "release train"):
            self.assertEqual(opswait.party_problem(party), "", party)


class Line(unittest.TestCase):
    def test_valid_line(self):
        self.assertTrue(opswait.has_valid_waiting_on(GOOD))
        self.assertTrue(opswait.has_valid_waiting_on(
            "intro\n  waiting-on: Hetzner -- reply -- read ticket 4711\n"))

    def test_markdown_and_ascii_forms(self):
        for text in ("- Waiting-on: Hetzner — reply — read ticket",
                     "**Waiting-on:** Hetzner — reply — read ticket",
                     "Waiting on: Hetzner — reply — read ticket",
                     "Waiting-on: Hetzner - reply - read ticket"):
            self.assertTrue(opswait.has_valid_waiting_on(text), text)

    def test_fenced_example_is_no_line(self):
        self.assertFalse(opswait.has_valid_waiting_on(
            "example:\n```\n%s\n```\n" % GOOD))
        self.assertTrue(opswait.has_valid_waiting_on(
            "```\nx\n```\n%s\n" % GOOD))

    def test_one_valid_wait_is_enough(self):
        both = GOOD + "\nWaiting-on: owner — power-cycle — ask him\n"
        self.assertTrue(opswait.has_valid_waiting_on(both))

    def test_shape_placeholder_owner(self):
        self.assertEqual(opswait.line_problem("Hetzner — reply"), "shape")
        self.assertEqual(opswait.line_problem(
            "<third party> — <event> — <check>"), "placeholder")
        self.assertEqual(opswait.line_problem(
            "Zbynek — power-cycle cams — ask him"), "owner")

    def test_quoted_or_midline_is_no_line(self):
        self.assertFalse(opswait.has_valid_waiting_on("> " + GOOD))
        self.assertFalse(opswait.has_valid_waiting_on("we said " + GOOD))
        self.assertFalse(opswait.has_valid_waiting_on(None))


class Classify(unittest.TestCase):
    def test_no_line_blocks(self):
        r = _run("gh issue edit 5 --add-label ops-wait", rows=[])
        self.assertEqual([(x["verdict"], x["reason"], x["ticket"]) for x in r],
                         [("BLOCK", "missing", "5")])

    def test_same_command_comment_passes_without_a_read(self):
        cmd = ("gh issue comment 5 --body '%s' && "
               "gh issue edit 5 --add-label ops-wait" % GOOD)
        r = _run(cmd, rows=_no_read)
        self.assertEqual(r[0]["verdict"], "PASS")

    def test_cat_heredoc_substitution_body(self):
        cmd = ("gh issue comment 5 --body \"$(cat <<'EOF'\n%s\nEOF\n)\" && "
               "gh issue edit 5 --add-label ops-wait" % GOOD)
        self.assertEqual(_run(cmd, rows=_no_read)[0]["verdict"], "PASS")

    def test_issue_body_never_counts(self):
        cmd = "gh issue edit 5 --body '%s' --add-label ops-wait" % GOOD
        self.assertEqual(_run(cmd, rows=[])[0]["verdict"], "BLOCK")

    def test_comment_in_another_repo_does_not_count(self):
        cmd = ("gh issue comment 5 -R a/b --body '%s' && "
               "gh issue edit 5 -R c/d --add-label ops-wait" % GOOD)
        self.assertEqual(_run(cmd, rows=[])[0]["verdict"], "BLOCK")
        cmd = cmd.replace("-R a/b ", "")
        self.assertEqual(_run(cmd, rows=_no_read)[0]["verdict"], "PASS")

    def test_heredoc_and_cat_file_bodies(self):
        cmd = ("gh issue comment 5 --body-file - <<'EOF'\n%s\nEOF\n"
               "gh issue edit 5 --add-label ops-wait" % GOOD)
        self.assertEqual(_run(cmd, rows=_no_read)[0]["verdict"], "PASS")
        cmd = ("cat > /tmp/w.md <<'EOF'\n%s\nEOF\n"
               "gh issue comment 5 -F /tmp/w.md; "
               "gh issue edit 5 --add-label ops-wait" % GOOD)
        self.assertEqual(_run(cmd, rows=_no_read)[0]["verdict"], "PASS")

    def test_comment_on_another_ticket_does_not_count(self):
        cmd = ("gh issue comment 6 --body '%s' && "
               "gh issue edit 5 --add-label ops-wait" % GOOD)
        self.assertEqual(_run(cmd, rows=[])[0]["verdict"], "BLOCK")

    def test_owner_party_in_command_blocks(self):
        cmd = ("gh issue comment 5 --body 'Waiting-on: owner — power-cycle — "
               "ask' && gh issue edit 5 --add-label ops-wait")
        r = _run(cmd, rows=_no_read)
        self.assertEqual((r[0]["verdict"], r[0]["reason"]), ("BLOCK", "owner"))

    def test_label_forms(self):
        for cmd in ("gh issue edit 5 --add-label 'bug,ops-wait'",
                    "gh issue edit 5 --add-label=ops-wait",
                    "cd /tmp && gh issue edit 5 -R o/r --add-label ops-wait",
                    "gh issue edit https://github.com/o/r/issues/5 "
                    "--add-label ops-wait",
                    "gh issue edit '#5' --add-label OPS-WAIT"):
            r = _run(cmd, rows=[])
            self.assertEqual([(x["verdict"], x["ticket"]) for x in r],
                             [("BLOCK", "5")], cmd)

    def test_several_tickets(self):
        r = _run("gh issue edit 5 6 --add-label ops-wait", rows=[])
        self.assertEqual([x["ticket"] for x in r], ["5", "6"])

    def test_not_an_add(self):
        for cmd in ("gh issue edit 5 --remove-label ops-wait",
                    "gh issue edit 5 --add-label ops-waiting",
                    "gh issue list --label ops-wait"):
            self.assertEqual(_run(cmd, rows=_no_read), [], cmd)

    def test_recent_comment_passes_old_one_blocks(self):
        fresh = [{"body": GOOD, "ts": NOW - 60}]
        old = [{"body": GOOD, "ts": NOW - opswait.FRESH_S - 60}]
        cmd = "gh issue edit 5 --add-label ops-wait"
        self.assertEqual(_run(cmd, rows=fresh)[0]["reason"], "recent-comment")
        self.assertEqual(_run(cmd, rows=old)[0]["verdict"], "BLOCK")

    def test_fail_open(self):
        cmd = "gh issue edit 5 --add-label ops-wait"
        self.assertEqual(_run(cmd, rows=None)[0]["reason"], "gh-unreadable")

        def boom(*_a):
            raise RuntimeError("gh down")
        r = opswait.classify_command(cmd, cwd="/x", now=NOW, comments_fn=boom)
        self.assertEqual(r[0]["verdict"], "PASS")
        r = _run('gh issue edit "$N" --add-label ops-wait', rows=_no_read)
        self.assertEqual((r[0]["verdict"], r[0]["reason"]),
                         ("PASS", "unresolvable-ticket"))

    def test_create_with_ops_wait_is_always_refused(self):
        for cmd in ("gh issue create -t T -b 'body' -l ops-wait",
                    "gh issue create -t T --body '%s' --label ops-wait" % GOOD):
            r = _run(cmd, rows=_no_read)
            self.assertEqual((r[0]["verdict"], r[0]["reason"]),
                             ("BLOCK", "create"), cmd)

    def test_edited_old_comment_does_not_count(self):
        old = [{"body": GOOD, "ts": NOW - 3 * 86400}]   # ts = created_at
        r = _run("gh issue edit 5 --add-label ops-wait", rows=old)
        self.assertEqual(r[0]["verdict"], "BLOCK")

    def test_message_points_owner_waits_to_u(self):
        msg = opswait.block_message([{"ticket": "5", "reason": "owner"}])
        for token in ("needs-owner-action", "needs-answer", "Waiting-on:",
                      "airuleset:opswait-ok", "#5", "OWNER"):
            self.assertIn(token, msg)


class CommentRead(unittest.TestCase):
    """`_default_comments` over a fake gh runner (the production read)."""

    def _read(self, rc, out):
        calls = []

        def runner(argv):
            calls.append(argv)
            return rc, out, "boom" if rc else ""
        rows = opswait._default_comments("5", "o/r", "/x", NOW, runner=runner)
        return rows, calls

    def test_rows_carry_created_at(self):
        out = json.dumps([{"body": GOOD, "created_at": "2027-01-15T08:00:00Z",
                           "updated_at": "2027-01-15T09:00:00Z"}])
        rows, calls = self._read(0, out)
        self.assertEqual(rows, [{"body": GOOD, "ts": opswait._parse_ts(
            "2027-01-15T08:00:00Z")}])
        self.assertIsNotNone(rows[0]["ts"])
        since = opswait._iso(NOW - opswait.FRESH_S)
        self.assertIn("repos/o/r/issues/5/comments?since=%s" % since,
                      calls[0][2])

    def test_errors_are_unknown(self):
        self.assertIsNone(self._read(1, "")[0])
        self.assertIsNone(self._read(0, "not json")[0])
        self.assertIsNone(self._read(0, "{}")[0])


class Hook(unittest.TestCase):
    def _run(self, cmd, fixture=None, broken_python=False):
        env = hermetic_hook_env(self)
        run_cwd = tempfile.mkdtemp(prefix="opswait-cwd-")
        self.addCleanup(shutil.rmtree, run_cwd, True)
        if fixture is not None:
            fx = Path(run_cwd, "fx.json")
            fx.write_text(json.dumps(fixture))
            env["AIRULESET_OPSWAIT_COMMENTS_FIXTURE"] = str(fx)
        if broken_python:
            tmpbin = tempfile.mkdtemp(prefix="nopy-")
            self.addCleanup(shutil.rmtree, tmpbin, True)
            shim = Path(tmpbin, "python3")
            shim.write_text("#!/usr/bin/env bash\nexit 42\n")
            shim.chmod(0o755)
            env["PATH"] = tmpbin + ":" + env.get("PATH", "")
        payload = json.dumps({"tool_input": {"command": cmd},
                              "cwd": run_cwd, "session_id": "test-opswait"})
        r = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True, env=env,
                           cwd=run_cwd, timeout=60)
        log = Path(env["HOME"], ".claude", "opswait-gate.log")
        return r, (log.read_text() if log.exists() else "")

    def test_blocks_without_a_line(self):
        r, log = self._run("gh issue edit 5 --add-label ops-wait",
                           fixture={"5": []})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("needs-owner-action", r.stderr)
        self.assertIn("BLOCK missing 5", log)

    def test_allows_a_recent_valid_comment(self):
        r, _log = self._run("gh issue edit 5 --add-label ops-wait",
                            fixture={"5": [{"body": GOOD, "ts": time.time()}]})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_fails_open_on_unreadable(self):
        r, log = self._run("gh issue edit 5 --add-label ops-wait",
                           fixture={"5": None})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("gh-unreadable", log)

    def test_bypass_is_logged_with_its_reason(self):
        r, log = self._run("gh issue edit 5 --add-label ops-wait  "
                           "# airuleset:opswait-ok owner said so",
                           fixture={"5": None})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("BYPASS owner_said_so 5", log)
        self.assertNotIn("gh-unreadable", log)      # no read for a bypass

    def test_a_pass_next_to_a_block_labelled_nothing(self):
        r, log = self._run("gh issue edit 5 6 --add-label ops-wait",
                           fixture={"5": [{"body": GOOD, "ts": time.time()}],
                                    "6": []})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("NOTLABELLED recent-comment 5", log)
        self.assertIn("BLOCK missing 6", log)

    def test_other_commands_exit_before_python(self):
        r, log = self._run("echo hi", broken_python=True)
        self.assertEqual((r.returncode, log), (0, ""), r.stderr)

    def test_python_malfunction_fails_closed(self):
        r, _log = self._run("gh issue edit 5 --add-label ops-wait",
                            fixture={"5": []}, broken_python=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fail-closed", r.stderr)

    def test_registered_as_a_bash_pretooluse_hook(self):
        cfg = json.loads((REPO_ROOT / "settings" / "hooks.json").read_text())
        cmds = [h.get("command", "")
                for group in cfg["hooks"]["PreToolUse"]
                if group.get("matcher") == "Bash"
                for h in group.get("hooks", [])]
        self.assertIn(
            "bash ~/devel/airuleset/hooks/block-ops-wait-without-waiting-on.sh",
            cmds)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
