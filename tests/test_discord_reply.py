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
        self.assertTrue(notify.record_question("111", "chan", "sid-abc",
                                               "/home/x/proj", now=1000, path=p))
        q = notify.load_questions(p)
        self.assertEqual(q["111"]["session"], "sid-abc")
        self.assertEqual(q["111"]["cwd"], "/home/x/proj")
        self.assertEqual(q["111"]["channel"], "chan")
        self.assertEqual(q["111"]["ts"], 1000)

    def test_missing_ids_are_rejected(self):
        p = self._p()
        self.assertFalse(notify.record_question("", "c", "sid", "/x", path=p))
        self.assertFalse(notify.record_question("111", "c", "", "/x", path=p))
        self.assertEqual(notify.load_questions(p), {})

    def test_drop_question(self):
        p = self._p()
        notify.record_question("111", "c", "s", "/x", now=1, path=p)
        notify.record_question("222", "c", "s2", "/y", now=2, path=p)
        self.assertTrue(notify.drop_question("111", path=p))
        q = notify.load_questions(p)
        self.assertNotIn("111", q)
        self.assertIn("222", q)
        self.assertFalse(notify.drop_question("nope", path=p))     # absent → False

    def test_stale_entries_pruned_on_write(self):
        p = self._p()
        notify.record_question("old", "c", "s", "/x", now=0, path=p)
        # a new write far in the future prunes the >24h-old entry
        notify.record_question("new", "c", "s", "/x",
                               now=notify._QUESTIONS_TTL_S + 100, path=p)
        q = notify.load_questions(p)
        self.assertNotIn("old", q)
        self.assertIn("new", q)

    def test_hard_cap_keeps_newest(self):
        p = self._p()
        for i in range(notify._QUESTIONS_MAX + 5):
            notify.record_question("m%d" % i, "c", "s%d" % i, "/x", now=i, path=p)
        q = notify.load_questions(p)
        self.assertLessEqual(len(q), notify._QUESTIONS_MAX)
        self.assertIn("m%d" % (notify._QUESTIONS_MAX + 4), q)       # newest kept
        self.assertNotIn("m0", q)                                   # oldest dropped

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
                    "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "thread-z"}
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

    def _reply_msg(self, rid="rep1", ref="ping1", author=None, content="najprv 0.28.0"):
        return {"id": rid, "author": {"id": author or self.OWNER},
                "message_reference": {"message_id": ref}, "content": content}

    def _fetch(self, msgs):
        return lambda ch, token: [m for m in msgs
                                  if m.get("_channel", "thread-z") == ch]

    def test_delivers_answer_into_idle_pane(self):
        notify.record_question("ping1", "thread-z", "sid-abc",
                               "/home/x/restreamer", now=time.time(), path=self.qpath)
        state = {}
        panes = {"sid-abc": ("%1", IDLE)}
        logs = wd.deliver_discord_replies(
            time.time(), self._run, state, panes, dry_run=True,
            discord_fetch=self._fetch([self._reply_msg()]))
        self.assertTrue(any("reply→" in ln for ln in logs), logs)
        # question dropped on delivery; reply id deduped
        self.assertNotIn("ping1", notify.load_questions(self.qpath))
        self.assertIn("rep1", state["dreply_done"])

    def test_types_the_answer_when_not_dry_run(self):
        notify.record_question("ping1", "thread-z", "sid-abc", "/p",
                               now=time.time(), path=self.qpath)
        wd.deliver_discord_replies(
            time.time(), self._run, {}, {"sid-abc": ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([self._reply_msg(content="najprv 0.28.0")]))
        # send_continue types the literal text then Enter
        literal = [a for a in self.sent if "-l" in a]
        self.assertTrue(any("najprv 0.28.0" in a for a in literal),
                        "answer text must be typed into the pane: %r" % self.sent)
        self.assertTrue(any(a[-1] == "Enter" for a in self.sent))

    def test_busy_pane_is_not_typed_into(self):
        notify.record_question("ping1", "thread-z", "sid-abc", "/p",
                               now=time.time(), path=self.qpath)
        state = {}
        logs = wd.deliver_discord_replies(
            time.time(), self._run, state, {"sid-abc": ("%1", BUSY)}, dry_run=False,
            discord_fetch=self._fetch([self._reply_msg()]))
        self.assertFalse(any("-l" in a for a in self.sent),
                         "must NOT inject into a running turn (#233)")
        self.assertTrue(any("busy" in ln for ln in logs), logs)
        # not delivered → question stays for the next cycle, reply not deduped
        self.assertIn("ping1", notify.load_questions(self.qpath))
        self.assertNotIn("rep1", state.get("dreply_done", []))

    def test_absent_pane_retries_later(self):
        notify.record_question("ping1", "thread-z", "sid-gone", "/p",
                               now=time.time(), path=self.qpath)
        logs = wd.deliver_discord_replies(
            time.time(), self._run, {}, {}, dry_run=False,      # no live pane
            discord_fetch=self._fetch([self._reply_msg()]))
        self.assertTrue(any("no pane" in ln for ln in logs), logs)
        self.assertIn("ping1", notify.load_questions(self.qpath))   # kept

    def test_non_owner_reply_ignored(self):
        notify.record_question("ping1", "thread-z", "sid-abc", "/p",
                               now=time.time(), path=self.qpath)
        logs = wd.deliver_discord_replies(
            time.time(), self._run, {}, {"sid-abc": ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([self._reply_msg(author="666")]))
        self.assertEqual(logs, [])                                  # nothing routed
        self.assertFalse(any("-l" in a for a in self.sent))
        self.assertIn("ping1", notify.load_questions(self.qpath))

    def test_already_delivered_reply_not_reinjected(self):
        notify.record_question("ping1", "thread-z", "sid-abc", "/p",
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
        notify.record_question("ping1", "thread-z", "sid-abc", "/p",
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
