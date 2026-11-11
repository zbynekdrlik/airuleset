"""#1307 — a turn that writes an OWNER DEPENDENCY into a GitHub ticket must ask
the owner in the same turn (owner 11.11.2026: "stale problem s otazkami ktore
mi nie su polozene!!!").

The dstack#141 incident: comments recorded that the next zvukar test needed the
owner's approval / OK / hands; no `❓` was asked, no U label was set, the footer
showed nothing. `gates.ownerdep` (wired into stop-check-question-quality.sh at
its "not a question turn" branch) blocks such a turn unless the final message
asks (`❓ NEEDS YOU:` / `❓ ASKED:`), the turn adds a U label, or the message
carries `airuleset:owner-dep-ok <reason>`.

The text shapes below are the real dstack#141 comments. Fixtures only.
"""
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hook_state_cleanup import hermetic_hook_env, sweep_session_files  # noqa: E402

HOOK = REPO / "hooks" / "stop-check-question-quality.sh"


def _gate():
    return importlib.import_module("gates.ownerdep")


# --- the real dstack#141 shapes ------------------------------------------- #
C_2051 = ("10.11.2026 ~21:00Z dcontroller API (25 min): zvukar (planet 22, "
          "UltimoX2) slot 1 @1000 us: avg 530 us, peak 2020 us, 7 late. Next per "
          "tests.md ('swap the sender, not the theory'): subscribe one zvukar "
          "channel to a dev1 test device with one TX change at a time and read "
          "both channels from dcontroller over the same minutes — needs the "
          "owner's approval (zvukar's subscriptions are not ours).")
C_2057 = ("10.11. zvukar has only 2 RX channels, both in use (Solo L/R from "
          "band-asio-dante = the engineer's monitor) — no unused channel, so the "
          "approved one-unused-channel experiment is not possible without "
          "touching his monitor; not done. Facts: link_mbit 100, platform "
          "UltimoX2 4.2.9.10, latency 1000 us min.")
C_2058 = ("mikrotik 20:57Z (foh1_audio ether8, 10 s): IGMP snooping off but "
          "flood is tiny. Discriminating test needs the owner's hands: zvukar "
          "on its own CRS310 port (or a 1G link).")
C_0924 = ("So bypassing the unmanaged switch changed nothing. Next: the same "
          "comparison with an Audinate sender to zvukar on the direct port "
          "(needs the owner's OK to subscribe one zvukar channel to a tascam "
          "channel for ~10 min).")
CHECKLIST = ("## Plan\n- [ ] Reproduce without touching the receiver.\n"
             "- [ ] Verify (a dasio A/B needs the owner's approval): the receiver "
             "reads our flow near 0.4 ms, with 0 late.\n")
PAST_AB = "## A/B (owner-approved, 6.11.2026 00:19:40–00:29:44Z, dcontroller history)"
PAST_OWNER = "Owner approved (7.11.2026 …): the A/B ran on the receiver and was restored."
ROZHODNUTE = ("ROZHODNUTÉ (owner 10.11.2026, '1'): approved — subscribe ONE unused "
              "RX channel of zvukar; restore the original subscription after.")
OWNER_OK = ("Owner OK 11.11.2026 (his message: the test needs no question when "
            "there is no event). 11:54:37Z: zvukar RX 2 switched for a 9.5 min A/B.")

DONE_MSG = "Zapísané na dstack#141.\n\n✅ DONE: zvukar meranie zapísané"
ASK_MSG = ("**Otázka — projekt dstack (Dante stack):** ďalší test potrebuje "
           "prepnúť kanál zvukára.\n• áno (odporúčam) — test\n• nie — bez testu\n"
           "❓ NEEDS YOU: smiem prepnúť jeden kanál zvukára na 10 minút?")
ASKED_MSG = ("❓ ASKED: smiem prepnúť jeden kanál zvukára na 10 minút?\n\n"
             "⏳ WORKING: ďalší ticket")


class TestDependencyText(unittest.TestCase):
    def test_real_needed_forms_match(self):
        g = _gate()
        for text in (C_2051, C_2058, C_0924, CHECKLIST):
            self.assertTrue(g.owner_dependency(text), text[-80:])

    def test_other_needed_forms_match(self):
        g = _gate()
        for text in ("Blocked: waiting for the owner.",
                     "Parked, waiting on the owner's decision about the port.",
                     "The owner must approve the latency change first.",
                     "Owner approval needed before the swap.",
                     "Pending owner confirmation.",
                     "Ďalší krok: potrebujem tvoj súhlas na prepnutie.",
                     "Potrebujem súhlas, kanál nie je náš.",
                     "Test čaká na tvoj súhlas.",
                     "Čaká na ownerovo rozhodnutie o latencii.",
                     "Owner musí schváliť zmenu latencie."):
            self.assertTrue(g.owner_dependency(text), text)

    def test_past_and_settled_forms_never_match(self):
        g = _gate()
        for text in (C_2057, PAST_AB, PAST_OWNER, ROZHODNUTE, OWNER_OK,
                     "The swap was approved by the owner at 21:04Z.",
                     "Owner-approved A/B, restored after."):
            self.assertEqual(g.owner_dependency(text), "", text)

    def test_negated_forms_never_match(self):
        g = _gate()
        for text in ("This test does not need the owner's approval (approval-scope).",
                     "It no longer needs the owner's approval.",
                     "Swap the channel, never waiting for the owner.",
                     "Nepotrebujem tvoj súhlas, testujem rig.",
                     "Netreba čakať na ownera."):
            self.assertEqual(g.owner_dependency(text), "", text)

    def test_code_and_quotes_never_match(self):
        g = _gate()
        fenced = "Gate example:\n```\nneeds the owner's approval\n```\ndone."
        inline = "The gate blocks `needs the owner's approval` style lines."
        quoted = "dstack wrote:\n> needs the owner's approval\nThat was wrong."
        for text in (fenced, inline, quoted):
            self.assertEqual(g.owner_dependency(text), "", text)

    def test_long_line_is_linear(self):
        g = _gate()
        text = ("needs the " + "x" * 50_000 + " owner ") * 3
        t0 = time.monotonic()
        g.owner_dependency(text)
        self.assertLess(time.monotonic() - t0, 1.0)


class _Transcript:
    """A minimal Claude Code JSONL transcript: one prior turn, then the turn
    under test (prompt + tool calls with results)."""

    def __init__(self, testcase):
        self.dir = tempfile.mkdtemp(prefix="od1307-")
        self.path = os.path.join(self.dir, "t.jsonl")
        self.lines = []
        self.n = 0
        testcase.addCleanup(lambda: __import__("shutil").rmtree(self.dir, True))

    def prompt(self, text="pokračuj"):
        self.lines.append({"type": "user", "message": {"role": "user", "content": text}})
        return self

    def call(self, name, inp, result="", error=False):
        self.n += 1
        tid = "toolu_%d" % self.n
        self.lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
        self.lines.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result,
             "is_error": error}]}})
        return self

    def bash(self, cmd, result="", error=False):
        return self.call("Bash", {"command": cmd}, result, error)

    def feedback(self, text="Stop hook feedback: fix it"):
        self.lines.append({"type": "user", "isMeta": True,
                           "message": {"role": "user", "content": text}})
        return self

    def write(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            for line in self.lines:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return self.path


COMMENTED = "https://github.com/zbynekdrlik/dstack/issues/141#issuecomment-1"


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self.t = _Transcript(self).prompt("prior turn")
        self.t.bash("gh issue comment 141 --body %s" % json.dumps(C_2051), COMMENTED)
        self.t.prompt()

    def _eval(self, message=DONE_MSG):
        return _gate().evaluate(self.t.write(), message, self.t.dir)

    def test_previous_turn_dependency_is_not_this_turns(self):
        self.assertEqual(self._eval()[0], "allow")

    def test_inline_comment_dependency_unasked_blocks(self):
        self.t.bash("gh issue comment 141 -R zbynekdrlik/dstack --body %s"
                    % json.dumps(C_2051), COMMENTED)
        verdict, reason = self._eval()
        self.assertEqual(verdict, "block")
        self.assertIn("#141", reason)
        self.assertIn("airuleset:owner-dep-ok", reason)
        self.assertIn("needs-owner-action", reason)
        self.assertIn("approval-scope.md", reason)
        self.assertIn("polož", reason)

    def test_stop_feedback_does_not_reset_the_turn(self):
        self.t.bash("gh issue comment 141 --body %s" % json.dumps(C_0924), COMMENTED)
        self.t.feedback()
        self.assertEqual(self._eval()[0], "block")

    def test_needs_you_question_settles(self):
        self.t.bash("gh issue comment 141 --body %s" % json.dumps(C_2051), COMMENTED)
        self.assertEqual(self._eval(ASK_MSG)[0], "allow")

    def test_asked_question_settles(self):
        self.t.bash("gh issue comment 141 --body %s" % json.dumps(C_2051), COMMENTED)
        self.assertEqual(self._eval(ASKED_MSG)[0], "allow")

    def test_u_label_in_the_same_turn_settles(self):
        for label in ("needs-answer", "needs-decision", "needs-owner-action"):
            with self.subTest(label=label):
                t = _Transcript(self).prompt()
                t.bash("gh issue comment 141 --body %s && gh issue edit 141 "
                       "--add-label ops-wait,%s" % (json.dumps(C_2058), label),
                       COMMENTED)
                self.assertEqual(_gate().evaluate(t.write(), DONE_MSG, t.dir)[0],
                                 "allow")

    def test_label_via_api_settles(self):
        self.t.bash("gh issue comment 141 --body %s" % json.dumps(C_2051), COMMENTED)
        self.t.bash("gh api repos/zbynekdrlik/dstack/issues/141/labels "
                    "-f 'labels[]=needs-answer'", "[]")
        self.assertEqual(self._eval()[0], "allow")

    def test_failed_label_add_does_not_settle(self):
        self.t.bash("gh issue comment 141 --body %s" % json.dumps(C_2051), COMMENTED)
        self.t.bash("gh issue edit 141 --add-label needs-answer",
                    "HTTP 403", error=True)
        self.assertEqual(self._eval()[0], "block")

    def test_body_file_on_disk_blocks(self):
        path = os.path.join(self.t.dir, "c141.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(C_0924)
        self.t.bash("gh issue comment 141 --body-file %s" % path, COMMENTED)
        self.assertEqual(self._eval()[0], "block")

    def test_short_F_relative_body_file_blocks(self):
        with open(os.path.join(self.t.dir, "c141.md"), "w", encoding="utf-8") as fh:
            fh.write(C_0924)
        self.t.bash("cd %s && gh issue comment 141 -F c141.md" % self.t.dir, COMMENTED)
        self.assertEqual(self._eval()[0], "block")

    def test_write_tool_body_file_deleted_before_stop_blocks(self):
        path = os.path.join(self.t.dir, "gone.md")
        self.t.call("Write", {"file_path": path, "content": C_2058}, "ok")
        self.t.bash("gh issue comment 141 --body-file %s && rm %s" % (path, path),
                    COMMENTED)
        self.assertEqual(self._eval()[0], "block")

    def test_heredoc_body_blocks(self):
        self.t.bash("gh issue comment 141 --body-file - <<'EOF'\n%s\nEOF" % C_2051,
                    COMMENTED)
        self.assertEqual(self._eval()[0], "block")

    def test_cat_heredoc_body_blocks(self):
        self.t.bash('gh issue comment 141 --body "$(cat <<\'EOF\'\n%s\nEOF\n)"'
                    % C_2051, COMMENTED)
        self.assertEqual(self._eval()[0], "block")

    def test_checklist_via_issue_edit_body_file_blocks(self):
        path = os.path.join(self.t.dir, "plan.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(PAST_AB + "\n\n" + CHECKLIST)
        self.t.bash("gh issue edit 141 --body-file %s" % path, "")
        verdict, reason = self._eval()
        self.assertEqual(verdict, "block")
        self.assertIn("dasio A/B", reason)

    def test_issue_create_body_blocks(self):
        self.t.bash("gh issue create --title X --body %s" % json.dumps(C_2058),
                    "https://github.com/zbynekdrlik/dstack/issues/150")
        self.assertEqual(self._eval()[0], "block")

    def test_api_comment_body_field_blocks(self):
        self.t.bash("gh api repos/zbynekdrlik/dstack/issues/141/comments -f body=%s"
                    % json.dumps(C_0924), "{}")
        self.assertEqual(self._eval()[0], "block")

    def test_api_comment_body_at_file_blocks(self):
        path = os.path.join(self.t.dir, "b.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(C_0924)
        self.t.bash("gh api repos/zbynekdrlik/dstack/issues/141/comments "
                    "-F body=@%s" % path, "{}")
        self.assertEqual(self._eval()[0], "block")

    def test_past_forms_posted_allow(self):
        self.t.bash("gh issue comment 141 --body %s" % json.dumps(
            "\n".join((C_2057, PAST_AB, PAST_OWNER, ROZHODNUTE, OWNER_OK))), COMMENTED)
        self.assertEqual(self._eval()[0], "allow")

    def test_failed_comment_without_url_allows(self):
        self.t.bash("gh issue comment 141 --body %s" % json.dumps(C_2051),
                    "GraphQL: rate limited", error=True)
        self.assertEqual(self._eval()[0], "allow")

    def test_failed_chain_with_posted_url_still_blocks(self):
        self.t.bash("gh issue comment 141 --body %s && false" % json.dumps(C_2051),
                    COMMENTED + "\nExit code 1", error=True)
        self.assertEqual(self._eval()[0], "block")

    def test_read_only_mentions_never_count(self):
        self.t.bash("echo %s; grep -n 'owner' notes.md" % json.dumps(C_2051), "")
        self.t.bash("gh issue view 141 --comments", C_2051)
        self.assertEqual(self._eval()[0], "allow")

    def test_bypass_line_allows(self):
        self.t.bash("gh issue comment 141 --body %s" % json.dumps(C_2051), COMMENTED)
        msg = DONE_MSG.replace("✅", "airuleset:owner-dep-ok already needs-answer on #141\n\n✅")
        self.assertEqual(self._eval(msg)[0], "allow")

    def test_unreadable_transcript_fails_open(self):
        self.assertEqual(_gate().evaluate("/nonexistent/t.jsonl", DONE_MSG, "/")[0],
                         "allow")


class TestHook(unittest.TestCase):
    """Driven through the real Stop hook."""

    def _run(self, message, cmd=None):
        t = _Transcript(self).prompt()
        if cmd:
            t.bash(cmd, COMMENTED)
        sid = "od1307-" + uuid.uuid4().hex[:12]
        self.addCleanup(sweep_session_files, sid)
        env = hermetic_hook_env(self)
        payload = {"last_assistant_message": message, "session_id": sid,
                   "transcript_path": t.write(), "cwd": t.dir}
        r = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env, timeout=120)
        return r, env["HOME"]

    def test_silent_dependency_turn_is_blocked(self):
        r, _home = self._run(DONE_MSG, "gh issue comment 141 --body %s"
                             % json.dumps(C_2051))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("1307", r.stderr)
        self.assertIn("owner-dep-ok", r.stderr)

    def test_clean_turn_passes(self):
        r, _home = self._run(DONE_MSG, "gh issue comment 141 --body %s"
                             % json.dumps(C_2057))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bypass_passes_and_is_logged(self):
        msg = "airuleset:owner-dep-ok replay of the dstack shape\n\n" + DONE_MSG
        r, home = self._run(msg, "gh issue comment 141 --body %s" % json.dumps(C_2051))
        self.assertEqual(r.returncode, 0, r.stderr)
        log = Path(home) / "devel" / "airuleset" / "audits" / "owner-dep-gate.log"
        self.assertIn("BYPASS", log.read_text(encoding="utf-8"))
        self.assertIn("replay of the dstack shape", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
