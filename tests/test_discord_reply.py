"""Discord REPLY → the Claude session that asked (watchdog job 7 + notify map).

Feature: an autopilot ❓ ping is delivered to Discord; the user REPLIES to it on
their phone; the watchdog types the answer into the exact session that asked.

These tests lock the SECURITY boundary (only a known owner's explicit reply to a
❓ THIS machine sent, only into an idle pane) and the delivery mechanics (dedup,
drop-on-delivery, busy/absent-pane retry, question-map persistence + pruning).
"""

import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify
import watchdog as wd


# --------------------------------------------------------------------------- #
# notify: the outstanding-question map (message id → asking session)
# --------------------------------------------------------------------------- #
class QuestionMap(unittest.TestCase):
    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "discord-questions.json")

    def test_record_and_load(self):
        p = self._p()
        self.assertTrue(notify.record_question("111", "900", "sid-abc",
                                               "/home/x/proj", now=1000, path=p))
        q = notify.load_questions(p)
        self.assertEqual(q["111"]["session"], "sid-abc")
        self.assertEqual(q["111"]["cwd"], "/home/x/proj")
        self.assertEqual(q["111"]["channel"], "900")
        self.assertEqual(q["111"]["ts"], 1000)

    def test_missing_ids_are_rejected(self):
        p = self._p()
        self.assertFalse(notify.record_question("", "900", "sid", "/x", path=p))
        self.assertFalse(notify.record_question("111", "900", "", "/x", path=p))
        # NON-NUMERIC ids/channels refused — a Mock repr / garbage can never
        # pollute the live map (real incident, 2026-07-04)
        self.assertFalse(notify.record_question("<Mock id=1>", "900", "s", "/x", path=p))
        self.assertFalse(notify.record_question("111", "thread-z", "s", "/x", path=p))
        self.assertEqual(notify.load_questions(p), {})

    def test_drop_question(self):
        p = self._p()
        notify.record_question("111", "900", "s", "/x", now=1, path=p)
        notify.record_question("222", "900", "s2", "/y", now=2, path=p)
        self.assertTrue(notify.drop_question("111", path=p))
        q = notify.load_questions(p)
        self.assertNotIn("111", q)
        self.assertIn("222", q)
        self.assertFalse(notify.drop_question("nope", path=p))     # absent → False

    def test_stale_entries_pruned_on_write(self):
        p = self._p()
        notify.record_question("100", "900", "s", "/x", now=0, path=p)
        # a new write far in the future prunes the >24h-old entry
        notify.record_question("200", "900", "s", "/x",
                               now=notify._QUESTIONS_TTL_S + 100, path=p)
        q = notify.load_questions(p)
        self.assertNotIn("100", q)
        self.assertIn("200", q)

    def test_hard_cap_keeps_newest(self):
        p = self._p()
        for i in range(notify._QUESTIONS_MAX + 5):
            notify.record_question("5%04d" % i, "900", "s%d" % i, "/x", now=i, path=p)
        q = notify.load_questions(p)
        self.assertLessEqual(len(q), notify._QUESTIONS_MAX)
        self.assertIn("5%04d" % (notify._QUESTIONS_MAX + 4), q)     # newest kept
        self.assertNotIn("50000", q)                                   # oldest dropped

    def test_load_bad_file_is_empty(self):
        p = self._p()
        Path(p).write_text("not json")
        self.assertEqual(notify.load_questions(p), {})

    def test_known_owner_ids_from_env(self):
        env = {"DISCORD_MENTION_ZBYNEK": "773451844110385193",
               "DISCORD_MENTION_MAREK": "<@771300000000000000>",
               "DISCORD_NOTIFICATION_CHANNEL_ID": "999"}
        ids = notify.known_owner_ids(env)
        self.assertEqual(ids, {"773451844110385193", "771300000000000000"})

    def test_bot_token_accessor(self):
        self.assertEqual(notify.bot_token({"DISCORD_BOT_TOKEN": "tok"}), "tok")
        self.assertEqual(notify.bot_token({}), "")


# --------------------------------------------------------------------------- #
# watchdog: reply text cleaning + validation (the pure security core)
# --------------------------------------------------------------------------- #
class CleanReplyText(unittest.TestCase):
    def test_strips_mentions_and_collapses_whitespace(self):
        self.assertEqual(
            wd.clean_reply_text("<@123> najprv   #280\n\nzáloha"),
            "najprv #280 záloha")

    def test_strips_role_and_bang_mentions(self):
        self.assertEqual(wd.clean_reply_text("<@!1> <@&2> hej"), "hej")

    def test_empty_after_cleaning_is_empty(self):
        self.assertEqual(wd.clean_reply_text("<@123>"), "")
        self.assertEqual(wd.clean_reply_text(""), "")
        self.assertEqual(wd.clean_reply_text(None), "")

    def test_length_capped(self):
        out = wd.clean_reply_text("x" * 5000)
        self.assertEqual(len(out), wd.DISCORD_REPLY_MAX_CHARS)

    def test_newline_never_leaks(self):
        self.assertNotIn("\n", wd.clean_reply_text("a\nb\nc"))


class ParseDiscordReply(unittest.TestCase):
    OWNER = "773451844110385193"
    QMAP = {"ping1": {"session": "sid-abc", "cwd": "/home/x/restreamer",
                      "channel": "thread-z"}}

    def _msg(self, **over):
        m = {"id": "rep1", "author": {"id": self.OWNER},
             "message_reference": {"message_id": "ping1"},
             "content": "najprv 0.28.0"}
        m.update(over)
        return m

    def test_valid_reply(self):
        r = wd.parse_discord_reply(self._msg(), {self.OWNER}, self.QMAP)
        self.assertEqual(r["session"], "sid-abc")
        self.assertEqual(r["referenced"], "ping1")
        self.assertEqual(r["text"], "najprv 0.28.0")
        self.assertEqual(r["reply_id"], "rep1")

    def test_non_owner_author_rejected(self):
        # SECURITY: a stranger posting in the thread must NEVER drive a session
        self.assertIsNone(
            wd.parse_discord_reply(self._msg(author={"id": "666"}),
                                   {self.OWNER}, self.QMAP))

    def test_not_a_reply_rejected(self):
        m = self._msg()
        del m["message_reference"]
        self.assertIsNone(wd.parse_discord_reply(m, {self.OWNER}, self.QMAP))

    def test_reply_to_untracked_message_rejected(self):
        # a reply to some OTHER message (not a ❓ we sent) is ignored
        self.assertIsNone(
            wd.parse_discord_reply(
                self._msg(message_reference={"message_id": "unknown"}),
                {self.OWNER}, self.QMAP))

    def test_empty_content_rejected(self):
        self.assertIsNone(
            wd.parse_discord_reply(self._msg(content="<@1>"), {self.OWNER}, self.QMAP))

    def test_garbage_message_rejected(self):
        self.assertIsNone(wd.parse_discord_reply(None, {self.OWNER}, self.QMAP))
        self.assertIsNone(wd.parse_discord_reply("x", {self.OWNER}, self.QMAP))


# --------------------------------------------------------------------------- #
# watchdog: the delivery job (routing into the idle pane)
# --------------------------------------------------------------------------- #
IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
BUSY = ("● Validate issue\n  ⎿ running…\n"
        "✳ Baking… (2m · esc to interrupt)\n")


class DeliverDiscordReplies(unittest.TestCase):
    OWNER = "773451844110385193"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "discord-questions.json")
        # point notify's question map + env at hermetic fixtures
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER,
                    "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "777001"}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        self.sent = []            # (pane_id, argv) captured tmux calls

    def _run(self, argv, timeout=8):
        self.sent.append(argv)
        j = " ".join(argv)
        if "pane_in_mode" in j:
            return "0"
        return ""

    def _reply_msg(self, rid="rep1", ref="888001", author=None, content="najprv 0.28.0"):
        return {"id": rid, "author": {"id": author or self.OWNER},
                "message_reference": {"message_id": ref}, "content": content}

    def _fetch(self, msgs):
        return lambda ch, token: [m for m in msgs
                                  if m.get("_channel", "777001") == ch]

    def test_delivers_answer_into_idle_pane(self):
        notify.record_question("888001", "777001", "sid-abc",
                               "/home/x/restreamer", now=time.time(), path=self.qpath)
        state = {}
        panes = {"sid-abc": ("%1", IDLE)}
        logs = wd.deliver_discord_replies(
            time.time(), self._run, state, panes, dry_run=True,
            discord_fetch=self._fetch([self._reply_msg()]))
        self.assertTrue(any("reply→" in ln for ln in logs), logs)
        # dry-run: delivery is SIMULATED — the real on-disk map must be kept
        # (a dry-run diagnostic dropping the live question loses the answer);
        # the reply id is still deduped in (unsaved) state
        self.assertIn("888001", notify.load_questions(self.qpath))
        self.assertIn("rep1", state["dreply_done"])

    def test_types_the_answer_when_not_dry_run(self):
        notify.record_question("888001", "777001", "sid-abc", "/p",
                               now=time.time(), path=self.qpath)
        wd.deliver_discord_replies(
            time.time(), self._run, {}, {"sid-abc": ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([self._reply_msg(content="najprv 0.28.0")]))
        # send_continue types the literal text then Enter
        literal = [a for a in self.sent if "-l" in a]
        self.assertTrue(any("najprv 0.28.0" in a[-1] for a in literal),
                        "answer text must be typed into the pane: %r" % self.sent)
        self.assertTrue(any(a[-1] == "Enter" for a in self.sent))

    def test_busy_pane_is_not_typed_into(self):
        notify.record_question("888001", "777001", "sid-abc", "/p",
                               now=time.time(), path=self.qpath)
        state = {}
        logs = wd.deliver_discord_replies(
            time.time(), self._run, state, {"sid-abc": ("%1", BUSY)}, dry_run=False,
            discord_fetch=self._fetch([self._reply_msg()]))
        self.assertFalse(any("-l" in a for a in self.sent),
                         "must NOT inject into a running turn (#233)")
        self.assertTrue(any("busy" in ln for ln in logs), logs)
        # not delivered → question stays for the next cycle, reply not deduped
        self.assertIn("888001", notify.load_questions(self.qpath))
        self.assertNotIn("rep1", state.get("dreply_done", []))

    def test_absent_pane_retries_later(self):
        notify.record_question("888001", "777001", "sid-gone", "/p",
                               now=time.time(), path=self.qpath)
        logs = wd.deliver_discord_replies(
            time.time(), self._run, {}, {}, dry_run=False,      # no live pane
            discord_fetch=self._fetch([self._reply_msg()]))
        self.assertTrue(any("no pane" in ln for ln in logs), logs)
        self.assertIn("888001", notify.load_questions(self.qpath))   # kept

    def test_non_owner_reply_ignored(self):
        notify.record_question("888001", "777001", "sid-abc", "/p",
                               now=time.time(), path=self.qpath)
        logs = wd.deliver_discord_replies(
            time.time(), self._run, {}, {"sid-abc": ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([self._reply_msg(author="666")]))
        self.assertEqual(logs, [])                                  # nothing routed
        self.assertFalse(any("-l" in a for a in self.sent))
        self.assertIn("888001", notify.load_questions(self.qpath))

    def test_already_delivered_reply_not_reinjected(self):
        notify.record_question("888001", "777001", "sid-abc", "/p",
                               now=time.time(), path=self.qpath)
        state = {"dreply_done": ["rep1"]}                           # already handled
        logs = wd.deliver_discord_replies(
            time.time(), self._run, state, {"sid-abc": ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([self._reply_msg()]))
        self.assertFalse(any("-l" in a for a in self.sent))
        self.assertEqual(logs, [])

    def test_no_questions_is_a_noop(self):
        logs = wd.deliver_discord_replies(
            time.time(), self._run, {}, {"sid-abc": ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([self._reply_msg()]))
        self.assertEqual(logs, [])                                  # empty map → skip

    def test_no_token_is_a_noop(self):
        notify.record_question("888001", "777001", "sid-abc", "/p",
                               now=time.time(), path=self.qpath)
        self.env.pop("DISCORD_BOT_TOKEN")
        logs = wd.deliver_discord_replies(
            time.time(), self._run, {}, {"sid-abc": ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([self._reply_msg()]))
        self.assertEqual(logs, [])


class FetchChannelMessages(unittest.TestCase):
    def test_empty_args_return_empty(self):
        self.assertEqual(wd.fetch_channel_messages("", "tok"), [])
        self.assertEqual(wd.fetch_channel_messages("ch", ""), [])


if __name__ == "__main__":
    unittest.main()


class UpdateQuestion(unittest.TestCase):
    """notify.update_question — EDIT a recent ❓ ping in place (a reworded,
    still-unanswered question must converge the existing Discord card; edits
    do not push-ping — 3 pings for one reworded question, camera-box
    2026-07-05)."""

    HEAD = "<@773451844110385193> **❓ demo** — otázka"

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "discord-questions.json")
        self.env = {"DISCORD_BOT_TOKEN": "tok"}
        self.calls = []

    def _http(self, get_content=None, patch_ok=True):
        head = self.HEAD
        def http(token, method, url, payload=None):
            self.calls.append((method, url, payload))
            if method == "GET":
                return {"content": (get_content if get_content is not None
                                    else head + "\n\nstará verzia otázky?")}
            return {} if patch_ok else None
        return http

    def test_edits_recent_entry_keeps_header(self):
        notify.record_question("111", "222", "sess-a", "/p", now=1000, path=self.path)
        ok = notify.update_question("sess-a", "nová verzia otázky?", env=self.env,
                                    now=1100, path=self.path, http=self._http())
        self.assertTrue(ok)
        patch = [c for c in self.calls if c[0] == "PATCH"][0]
        self.assertEqual(patch[2]["content"],
                         self.HEAD + "\n\nnová verzia otázky?")
        self.assertEqual(patch[2]["flags"], notify.SUPPRESS_EMBEDS)
        d = notify.load_questions(self.path)
        self.assertEqual(d["111"]["ts"], 1100)      # window refreshed

    def test_old_entry_not_edited(self):
        notify.record_question("111", "222", "sess-a", "/p", now=1000, path=self.path)
        ok = notify.update_question("sess-a", "text", env=self.env,
                                    now=1000 + 16 * 60, path=self.path,
                                    http=self._http())
        self.assertFalse(ok)
        self.assertEqual(self.calls, [])

    def test_other_session_ignored(self):
        notify.record_question("111", "222", "sess-b", "/p", now=1000, path=self.path)
        ok = notify.update_question("sess-a", "text", env=self.env, now=1050,
                                    path=self.path, http=self._http())
        self.assertFalse(ok)

    def test_failed_patch_returns_false(self):
        notify.record_question("111", "222", "sess-a", "/p", now=1000, path=self.path)
        ok = notify.update_question("sess-a", "text", env=self.env, now=1050,
                                    path=self.path,
                                    http=self._http(patch_ok=False))
        self.assertFalse(ok, "failed PATCH must fall back to a fresh POST")
        self.assertEqual(notify.load_questions(self.path)["111"]["ts"], 1000)

    def test_no_token_returns_false(self):
        notify.record_question("111", "222", "sess-a", "/p", now=1000, path=self.path)
        ok = notify.update_question("sess-a", "text", env={}, now=1050,
                                    path=self.path, http=self._http())
        self.assertFalse(ok)
        self.assertEqual(self.calls, [])

    def test_non_question_message_untouched(self):
        # a mapped id whose live content lost its ❓ head (edited/foreign) is
        # left alone — never overwrite an arbitrary message
        notify.record_question("111", "222", "sess-a", "/p", now=1000, path=self.path)
        ok = notify.update_question("sess-a", "text", env=self.env, now=1050,
                                    path=self.path,
                                    http=self._http(get_content="obyčajná správa"))
        self.assertFalse(ok)
        self.assertEqual([c[0] for c in self.calls], ["GET"])


# --------------------------------------------------------------------------- #
# Reply prompt carries the QUESTION context (user ask, 2026-07-17)
# --------------------------------------------------------------------------- #
class ReplyPromptCarriesQuestion(unittest.TestCase):
    """A Discord reply may land hours/days after the ❓ was asked — a bare '1'
    typed into the session is meaningless once its context no longer holds the
    question. The prompt typed into the pane must carry WHEN the question was
    asked, its full text, and the user's answer; a legacy map entry without
    stored question text falls back to the raw reply (old behavior)."""

    OWNER = "773451844110385193"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "discord-questions.json")
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER,
                    "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "777001"}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        self.sent = []

    def _run(self, argv, timeout=8):
        self.sent.append(argv)
        return "0" if "pane_in_mode" in " ".join(argv) else ""

    QUESTION = ("<@773451844110385193> **Otázka — projekt restreamer:** "
                "ktorú verziu nasadiť?\n• 1. najprv 0.28.0\n• 2. rovno 0.29.0")

    def test_record_question_stores_single_line_text(self):
        notify.record_question("888001", "777001", "sid-abc", "/p",
                               now=1000, path=self.qpath,
                               question=self.QUESTION)
        q = notify.load_questions(self.qpath)["888001"]["question"]
        self.assertIn("ktorú verziu nasadiť?", q)
        self.assertNotIn("\n", q)                       # send-keys types ONE line
        self.assertFalse(q.startswith("<@"))            # mention prefix stripped

    def test_record_question_truncates_codepoint_safe(self):
        notify.record_question("888001", "777001", "sid-abc", "/p",
                               now=1000, path=self.qpath,
                               question="š" * 5000)
        q = notify.load_questions(self.qpath)["888001"]["question"]
        self.assertLessEqual(len(q), notify._QUESTION_TEXT_MAX)

    def test_parse_reply_carries_question_and_ts(self):
        qmap = {"ping1": {"session": "sid-abc", "cwd": "/p", "channel": "ch",
                          "ts": 1234, "question": "ktorú verziu nasadiť?"}}
        msg = {"id": "rep1", "author": {"id": self.OWNER},
               "message_reference": {"message_id": "ping1"}, "content": "1"}
        r = wd.parse_discord_reply(msg, {self.OWNER}, qmap)
        self.assertEqual(r["question"], "ktorú verziu nasadiť?")
        self.assertEqual(r["asked_ts"], 1234)

    def test_typed_prompt_wraps_reply_with_question_context(self):
        asked = time.time() - 3600
        notify.record_question("888001", "777001", "sid-abc", "/p",
                               now=asked, path=self.qpath,
                               question=self.QUESTION)
        wd.deliver_discord_replies(
            time.time(), self._run, {}, {"sid-abc": ("%1", IDLE)},
            dry_run=False,
            discord_fetch=lambda ch, tok: [
                {"id": "rep1", "author": {"id": self.OWNER},
                 "message_reference": {"message_id": "888001"},
                 "content": "1"}])
        typed = [a for a in self.sent if "-l" in a]
        self.assertTrue(typed, self.sent)
        text = typed[0][-1]
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(asked)))
        self.assertIn(when, text)                       # kedy bola položená
        self.assertIn("ktorú verziu nasadiť?", text)    # znenie otázky
        self.assertIn("odpovedal", text)                # + odpoveď užívateľa
        self.assertIn("«1»", text)
        self.assertNotIn("\n", text)                    # one line, one submit

    def test_legacy_entry_without_question_types_raw_reply(self):
        notify.record_question("888001", "777001", "sid-abc", "/p",
                               now=time.time(), path=self.qpath)
        wd.deliver_discord_replies(
            time.time(), self._run, {}, {"sid-abc": ("%1", IDLE)},
            dry_run=False,
            discord_fetch=lambda ch, tok: [
                {"id": "rep1", "author": {"id": self.OWNER},
                 "message_reference": {"message_id": "888001"},
                 "content": "najprv 0.28.0"}])
        typed = [a for a in self.sent if "-l" in a]
        self.assertIn("najprv 0.28.0", typed[0][-1])   # + re-arm tail rides along

    def test_record_question_cli_reads_question_from_stdin(self):
        # The send hook pipes the posted ❓ CONTENT via stdin — arbitrary quotes/
        # backticks never touch shell argv.
        import os
        import subprocess
        import json as _json
        import airuleset
        with TemporaryDirectory() as home:
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "notify", "--record-question", "--question-stdin",
                 "--message-id", "999",
                 "--channel", "888", "--session", "sid-x", "--cwd", "/p"],
                input=self.QUESTION, capture_output=True, text=True,
                env={**os.environ, "HOME": home})
            self.assertEqual(r.returncode, 0, r.stderr)
            d = _json.loads(Path(home, ".claude",
                                 "discord-questions.json").read_text())
            self.assertIn("ktorú verziu nasadiť?", d["999"]["question"])


class TestRecordQuestionNeverBlocksOnStdin(unittest.TestCase):
    def test_no_flag_with_open_pipe_stdin_completes(self):
        # 2026-07-19 push-gate hang: --record-question read stdin whenever it
        # was not a TTY; a caller spawning it with an inherited NEVER-CLOSING
        # pipe as stdin blocked forever in read(). Without --question-stdin
        # the command must never touch stdin.
        import os
        import subprocess
        import airuleset
        r, w = os.pipe()          # write end stays OPEN — the hang condition
        try:
            with TemporaryDirectory() as home:
                p = subprocess.run(
                    [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                     "notify", "--record-question", "--message-id", "111",
                     "--channel", "222", "--session", "sid-h", "--cwd", "/p"],
                    stdin=r, capture_output=True, text=True, timeout=15,
                    env={**os.environ, "HOME": home})
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn("recorded", p.stdout)
        finally:
            os.close(r)
            os.close(w)


class TestReplyPromptRemindsGoalRearm(unittest.TestCase):
    def test_wrapped_prompt_carries_rearm_reminder(self):
        # Montalu ping-pong break 2026-07-20: the /goal loop correctly ENDS on
        # a blocked ❓ (stop condition A), the user answers via Discord — and
        # nothing re-arms the loop, so bounce tickets rot and the gatekeeper
        # waits. The delivered reply prompt itself now carries the re-arm
        # instruction (print the continuation /goal + arm question; auto-arm
        # types it).
        r = {"question": "nechať marže skryté?", "asked_ts": 1234, "text": "1"}
        p = wd.compose_reply_prompt(r)
        self.assertIn("continuation /goal", p)
        self.assertIn("auto-arm", p)
        self.assertNotIn("\n", p)

    def test_legacy_raw_reply_also_carries_reminder(self):
        r = {"question": "", "asked_ts": 0, "text": "nechaj tak"}
        p = wd.compose_reply_prompt(r)
        self.assertIn("continuation /goal", p)


# --------------------------------------------------------------------------- #
# 2026-07-20 (#1832 incident): a DRAFT wedged in the input box blocked delivery
# FOREVER with no signal — the never-idle master loop's pane never went bare and
# the user's answer silently rotted. Two fixes locked here: wedge SELF-HEAL
# (verify after typing; corrective Enter; Enter-only retry for our own stuck
# text — never retype/duplicate) and the TICKET-FALLBACK (a reply blocked
# longer than DREPLY_TICKET_FALLBACK_S is delivered as a gh comment on the #N
# parsed from the stored question text, then ✅-reacted + dropped).
# --------------------------------------------------------------------------- #
RUNNING_DRAFT = ("✻ Waiting for 2 background agents to finish\n"
                 "──────────── ultracode ─\n"
                 "❯\xa0nech to tak\n"
                 "────────────\n"
                 "  ctx ██░░  caveman\n"
                 "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n")


class ScriptedPaneRun:
    """argv recorder whose capture-pane output follows a script (list of pane
    states returned in order; last one repeats)."""

    def __init__(self, captures):
        self.captures = list(captures)
        self.sent = []

    def __call__(self, argv, timeout=8):
        self.sent.append(argv)
        j = " ".join(argv)
        if "pane_in_mode" in j:
            return "0"
        if "capture-pane" in j:
            return self.captures.pop(0) if len(self.captures) > 1 else self.captures[0]
        return ""


class TicketFallbackDelivery(unittest.TestCase):
    OWNER = "773451844110385193"
    QTEXT = "**Otázka — odoo-erp:** Ticket #1832 je rozhodovací — nechať ako je?"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        self.gh_calls = []

    def _gh(self, cwd, num, text):
        self.gh_calls.append((cwd, num, text))
        return True

    def _fetch(self, msgs):
        return lambda ch, token: msgs

    def _reply(self, content="1"):
        return {"id": "repX", "author": {"id": self.OWNER},
                "message_reference": {"message_id": "888001"},
                "content": content}

    def _record(self):
        notify.record_question("888001", "777001", "sid-abc", "/repo/x",
                               now=time.time(), path=self.qpath,
                               question=self.QTEXT)

    def test_blocked_reply_falls_back_to_ticket_comment(self):
        self._record()
        now = time.time()
        state = {"dreply_blocked": {"repX": now - wd.DREPLY_TICKET_FALLBACK_S - 5}}
        run = ScriptedPaneRun([RUNNING_DRAFT])
        logs = wd.deliver_discord_replies(
            now, run, state, {"sid-abc": ("%1", RUNNING_DRAFT)}, dry_run=False,
            discord_fetch=self._fetch([self._reply()]), gh_comment=self._gh)
        self.assertEqual(len(self.gh_calls), 1, logs)
        cwd, num, text = self.gh_calls[0]
        self.assertEqual((cwd, num), ("/repo/x", "1832"))
        self.assertIn("«1»", text)
        # delivered-via-ticket: map dropped, reply deduped, blocked entry gone
        self.assertNotIn("888001", notify.load_questions(self.qpath))
        self.assertIn("repX", state["dreply_done"])
        self.assertNotIn("repX", state.get("dreply_blocked", {}))
        self.assertTrue(any("ticket" in ln for ln in logs), logs)

    def test_blocked_reply_before_deadline_stays_pending(self):
        self._record()
        now = time.time()
        state = {}
        run = ScriptedPaneRun([RUNNING_DRAFT])
        wd.deliver_discord_replies(
            now, run, state, {"sid-abc": ("%1", RUNNING_DRAFT)}, dry_run=False,
            discord_fetch=self._fetch([self._reply()]), gh_comment=self._gh)
        self.assertEqual(self.gh_calls, [])
        self.assertIn("888001", notify.load_questions(self.qpath))
        # first-blocked timestamp recorded for the fallback clock
        self.assertIn("repX", state.get("dreply_blocked", {}))

    def test_question_without_ticket_number_never_falls_back(self):
        notify.record_question("888001", "777001", "sid-abc", "/repo/x",
                               now=time.time(), path=self.qpath,
                               question="Otázka bez čísla tiketu — pokračovať?")
        now = time.time()
        state = {"dreply_blocked": {"repX": now - wd.DREPLY_TICKET_FALLBACK_S - 5}}
        run = ScriptedPaneRun([RUNNING_DRAFT])
        wd.deliver_discord_replies(
            now, run, state, {"sid-abc": ("%1", RUNNING_DRAFT)}, dry_run=False,
            discord_fetch=self._fetch([self._reply()]), gh_comment=self._gh)
        self.assertEqual(self.gh_calls, [])
        self.assertIn("888001", notify.load_questions(self.qpath))

    def test_absent_pane_also_reaches_ticket_fallback(self):
        # NO pane here does not mean no pane anywhere — a hosted stream's pane
        # lives in ANOTHER user's tmux (montalu, 2026-07-21), so the no-pane
        # fallback waits the LONGER DREPLY_NOPANE_FALLBACK_S to let the host
        # watchdog deliver by keystroke first; for a genuinely dead session it
        # still fires (later), never silently
        self._record()
        now = time.time()
        state = {"dreply_blocked": {"repX": now - wd.DREPLY_NOPANE_FALLBACK_S - 5}}
        run = ScriptedPaneRun([""])
        wd.deliver_discord_replies(
            now, run, state, {}, dry_run=False,
            discord_fetch=self._fetch([self._reply()]), gh_comment=self._gh)
        self.assertEqual(len(self.gh_calls), 1)


class WedgeSelfHeal(unittest.TestCase):
    OWNER = "773451844110385193"
    IDLE = "● done\n❯\xa0\n  ctx ███░  caveman\n"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        notify.record_question("888001", "777001", "sid-abc", "/repo/x",
                               now=time.time(), path=self.qpath,
                               question="Ticket #99 — pokračovať?")

    def _reply(self):
        return {"id": "repW", "author": {"id": self.OWNER},
                "message_reference": {"message_id": "888001"}, "content": "1"}

    def _wedged_pane(self, text):
        return ("──── ultracode ─\n❯\xa0" + text + "\n────\n  ctx ██░░  caveman\n")

    def test_swallowed_enter_gets_corrective_enter_then_delivers(self):
        # after typing, verify-capture still shows OUR text at ❯ (Enter was
        # swallowed) → ONE corrective Enter; second verify shows bare → delivered
        composed_tail = "auto-arm ho nalepí sám."
        run = ScriptedPaneRun([self._wedged_pane(composed_tail), self.IDLE, self.IDLE])
        state = {}
        wd.deliver_discord_replies(
            time.time(), run, state, {"sid-abc": ("%1", self.IDLE)}, dry_run=False,
            discord_fetch=lambda ch, t: [self._reply()], gh_comment=lambda *a: True)
        enters = [a for a in run.sent if a[-1] == "Enter"]
        self.assertGreaterEqual(len(enters), 2, run.sent)   # send + corrective
        self.assertIn("repW", state["dreply_done"])
        self.assertNotIn("888001", notify.load_questions(self.qpath))

    def test_still_wedged_after_retry_is_not_marked_delivered(self):
        stuck = self._wedged_pane("auto-arm ho nalepí sám.")
        run = ScriptedPaneRun([stuck, stuck, stuck])
        state = {}
        logs = wd.deliver_discord_replies(
            time.time(), run, state, {"sid-abc": ("%1", self.IDLE)}, dry_run=False,
            discord_fetch=lambda ch, t: [self._reply()], gh_comment=lambda *a: True)
        self.assertNotIn("repW", state.get("dreply_done", []))
        self.assertIn("888001", notify.load_questions(self.qpath))
        self.assertIn("repW", state.get("dreply_blocked", {}))
        self.assertTrue(any("wedge" in ln.lower() for ln in logs), logs)

    def test_own_stuck_text_is_entered_not_retyped(self):
        # a PRIOR wedged delivery left OUR composed text in the input box — the
        # next cycle must press Enter only, never type the text again (the
        # doubled-text corruption)
        stuck = self._wedged_pane("auto-arm ho nalepí sám.")
        run = ScriptedPaneRun([stuck, self.IDLE, self.IDLE])
        state = {}
        wd.deliver_discord_replies(
            time.time(), run, state, {"sid-abc": ("%1", stuck)}, dry_run=False,
            discord_fetch=lambda ch, t: [self._reply()], gh_comment=lambda *a: True)
        literal = [a for a in run.sent if "-l" in a]
        self.assertEqual(literal, [], "must NOT retype over own stuck text")
        enters = [a for a in run.sent if a[-1] == "Enter"]
        self.assertGreaterEqual(len(enters), 1)
        self.assertIn("repW", state["dreply_done"])


class ReceiptReaction(unittest.TestCase):
    """2026-07-20 (3rd user report): the ✅ reaction fired only at DELIVERY —
    a blocked reply meant no green check for minutes and the user assumed the
    answer was lost. The ✅ is a RECEIPT: it fires the moment the reply is
    MATCHED (even while delivery is pending), once per reply."""
    OWNER = "773451844110385193"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        self.react = m.patch.object(wd, "_react_ok", return_value=True)
        self.react_mock = self.react.start()
        self.addCleanup(self.react.stop)
        notify.record_question("888001", "777001", "sid-abc", "/repo/x",
                               now=time.time(), path=self.qpath,
                               question="Ticket #77 — pokračovať?")

    def _reply(self):
        return {"id": "repR", "author": {"id": self.OWNER},
                "message_reference": {"message_id": "888001"}, "content": "2"}

    def test_blocked_reply_reacts_immediately_and_once(self):
        st = {}
        run = ScriptedPaneRun([RUNNING_DRAFT])
        for i in range(2):
            wd.deliver_discord_replies(
                time.time() + i * 70, run, st,
                {"sid-abc": ("%1", RUNNING_DRAFT)}, dry_run=False,
                discord_fetch=lambda ch, t: [self._reply()],
                gh_comment=lambda *a: True)
        self.assertEqual(self.react_mock.call_count, 1,
                         "receipt ✅ fires at first MATCH, exactly once")
        # still undelivered (busy) — the receipt does not mark delivery
        self.assertIn("888001", notify.load_questions(self.qpath))

    def test_delivered_reply_reacts_exactly_once_total(self):
        idle = "● done\n❯\xa0\n  ctx ███░  caveman\n"
        run = ScriptedPaneRun([idle])
        wd.deliver_discord_replies(
            time.time(), run, {}, {"sid-abc": ("%1", idle)}, dry_run=False,
            discord_fetch=lambda ch, t: [self._reply()],
            gh_comment=lambda *a: True)
        self.assertEqual(self.react_mock.call_count, 1)


class FallbackDeadlineIsTight(unittest.TestCase):
    def test_fallback_within_three_minutes(self):
        # 10 min was too long for a phone user watching for the green check —
        # the durable ticket lane fires within 3 minutes of first blockage
        self.assertLessEqual(wd.DREPLY_TICKET_FALLBACK_S, 180)


class TicketFallbackPointer(unittest.TestCase):
    """The ticket-fallback delivers DURABLY but INVISIBLY — the user watching
    the terminal sees no prompt and assumes the answer vanished (4th report,
    2026-07-20 evening). After a ticket-fallback delivery job 7 records a
    POINTER; the moment the asking session's pane is typable it types a short
    visible prompt ('answer on ticket #N — read the comment'), exactly once."""
    OWNER = "773451844110385193"
    IDLE = "● done\n❯\xa0\n  ctx ███░  caveman\n"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        r = m.patch.object(wd, "_react_ok", return_value=True)
        r.start()
        self.addCleanup(r.stop)
        notify.record_question("888001", "777001", "sid-abc", "/repo/x",
                               now=time.time(), path=self.qpath,
                               question="Ticket #1770 — náklady?")

    def _reply(self):
        return {"id": "repP", "author": {"id": self.OWNER},
                "message_reference": {"message_id": "888001"}, "content": "3"}

    def _fallback_deliver(self, state):
        now = time.time()
        state.setdefault("dreply_blocked",
                         {"repP": now - wd.DREPLY_TICKET_FALLBACK_S - 5})
        run = ScriptedPaneRun([RUNNING_DRAFT])
        wd.deliver_discord_replies(
            now, run, state, {"sid-abc": ("%1", RUNNING_DRAFT)}, dry_run=False,
            discord_fetch=lambda ch, t: [self._reply()],
            gh_comment=lambda *a: True)
        return state

    def test_fallback_records_pointer_then_types_it_when_typable(self):
        state = self._fallback_deliver({})
        self.assertIn("sid-abc", state.get("dreply_pointer", {}))
        run = ScriptedPaneRun([self.IDLE, self.IDLE])
        wd.deliver_discord_replies(
            time.time(), run, state, {"sid-abc": ("%1", self.IDLE)},
            dry_run=False, discord_fetch=lambda ch, t: [],
            gh_comment=lambda *a: True)
        typed = [a[-1] for a in run.sent if "-l" in a]
        self.assertTrue(any("#1770" in t and "tickete" in t for t in typed),
                        typed)
        self.assertNotIn("sid-abc", state.get("dreply_pointer", {}))

    def test_pointer_not_typed_into_untypable_pane(self):
        state = self._fallback_deliver({})
        run = ScriptedPaneRun([RUNNING_DRAFT])
        wd.deliver_discord_replies(
            time.time(), run, state, {"sid-abc": ("%1", RUNNING_DRAFT)},
            dry_run=False, discord_fetch=lambda ch, t: [],
            gh_comment=lambda *a: True)
        self.assertFalse([a for a in run.sent if "-l" in a])
        self.assertIn("sid-abc", state.get("dreply_pointer", {}))


class InputDeadPing(unittest.TestCase):
    """4th wedge recurrence (2026-07-21): an ACTIVE session (transcript
    advancing) with a DEAD input box is invisible to job 10 (needs a stale
    transcript). Job 7 counts its own delivery verify-failures per session;
    >= 3 wedged cycles → ONE deduped Discord ping telling the user the input
    is dead and a restart is needed (the armed /goal survives resume)."""
    OWNER = "773451844110385193"
    IDLE = "● done\n❯\xa0\n  ctx ███░  caveman\n"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        m2 = m.patch.object(wd, "_react_ok", return_value=True)
        m2.start()
        self.addCleanup(m2.stop)
        self.pings = []
        m3 = m.patch.object(notify, "send",
                            side_effect=lambda body, **kw:
                            self.pings.append((body, kw)) or "sent")
        m3.start()
        self.addCleanup(m3.stop)
        notify.record_question("888001", "777001", "sid-abc", "/repo/x",
                               now=time.time(), path=self.qpath,
                               question="Ticket #55 — pokračovať?")

    def _reply(self):
        return {"id": "repD", "author": {"id": self.OWNER},
                "message_reference": {"message_id": "888001"}, "content": "1"}

    def _wedged_cycle(self, state):
        stuck = ("──── ultracode ─\n❯\xa0auto-arm ho nalepí sám.\n────\n"
                 "  ctx ██░░  caveman\n")
        run = ScriptedPaneRun([stuck, stuck, stuck])
        wd.deliver_discord_replies(
            time.time(), run, state, {"sid-abc": ("%1", self.IDLE)},
            dry_run=False, discord_fetch=lambda ch, t: [self._reply()],
            gh_comment=lambda *a: False)

    def test_three_wedged_cycles_ping_once(self):
        state = {}
        for _ in range(4):
            self._wedged_cycle(state)
        dead = [p for p in self.pings if "vstup" in p[0].lower()
                or "input" in p[0].lower()]
        self.assertEqual(len(dead), 1, self.pings)
        self.assertIn("dedup_key", dead[0][1])

    def test_single_wedge_does_not_ping(self):
        state = {}
        self._wedged_cycle(state)
        self.assertFalse([p for p in self.pings
                          if "vstup" in p[0].lower()])
