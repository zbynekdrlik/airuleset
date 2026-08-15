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
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify
import watchdog as wd


# #497 batch 2 — the job-7 reply POINTER now routes through the transcript-proof
# `send_verified` (the reply-pointer delivery, not the typed answer, which stays
# on send_continue). These tests assert the JOB LOGIC (typed when typable, popped
# once delivered), not the keystroke mechanics, so `send_verified` is replaced
# module-wide by a happy-path fake that byte-mirrors send_continue (type `-l --`
# + Enter, returns True). Swallowed-submit handling: test_send_verified_adoption.
def _typing_send_verified(pid, text, run=None, tpath=None, sleep_fn=None, logs=None):
    run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    return True


_SV_PATCHER = None


def setUpModule():
    global _SV_PATCHER
    _SV_PATCHER = m.patch.object(wd, "send_verified", _typing_send_verified)
    _SV_PATCHER.start()


def tearDownModule():
    if _SV_PATCHER is not None:
        _SV_PATCHER.stop()


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

    def test_stale_entries_are_never_pruned_by_age_on_write(self):
        # #368: an unanswered entry must survive past 24h+ so
        # watchdog.reping_stale_questions() has something to keep re-asking
        # daily — the old age-based prune (deleting it outright the moment
        # ANY later write swept the map) was the exact inversion of "ask at
        # least once a day, never silently drop" (the user's own directive,
        # 2026-08-11). Only a MALFORMED entry (not a dict) is still pruned.
        # Fixture re-pinned for the ghost-supersede work: the two entries
        # belong to DISTINCT sessions, because this lock's claim is about
        # AGE (a later write must not sweep an old entry) — a same-session
        # same-channel pair is a different fact (a superseded ask), owned
        # by its own dedicated tests in tests/test_question_prune.py.
        p = self._p()
        notify.record_question("100", "900", "s", "/x", now=0, path=p)
        notify.record_question("200", "900", "s2", "/x", now=100_000, path=p)
        q = notify.load_questions(p)
        self.assertIn("100", q)                # still there, far past 24h old
        self.assertIn("200", q)

    def test_block_field_stores_the_raw_newline_preserving_block(self):
        # #368: `question` stays the single-line-collapsed value
        # (compose_reply_prompt still needs it), but `block` keeps the
        # ORIGINAL structure — a daily re-ask reposts THIS verbatim, never
        # a flattened wall of prose.
        p = self._p()
        block = ("<@773451844110385193> **Otázka — projekt demo:** ktorú?\n"
                "1. a\n2. b\n\n❓ **rozhodnutie**")
        notify.record_question("111", "900", "sid", "/x", now=1000, path=p,
                               question=block)
        rec = notify.load_questions(p)["111"]
        self.assertIn("\n", rec["block"])                 # newlines preserved
        self.assertFalse(rec["block"].startswith("<@"))   # mention stripped
        self.assertIn("1. a\n2. b", rec["block"])
        self.assertNotIn("\n", rec["question"])            # unchanged, still one line

    def test_block_field_truncates_codepoint_safe(self):
        p = self._p()
        notify.record_question("111", "900", "sid", "/x", now=1000, path=p,
                               question="š" * 5000)
        rec = notify.load_questions(p)["111"]
        self.assertLessEqual(len(rec["block"]), notify._QUESTION_BLOCK_MAX)

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

    def test_malformed_legacy_entry_never_crashes_the_prune(self):
        # MINOR-7 sibling fix (#297/#298 review): the identical isinstance
        # guard applied to record_card_message's prune loop, mirrored here.
        import json as _json
        p = self._p()
        with open(p, "w", encoding="utf-8") as fh:
            _json.dump({"555": "not-a-dict", "666": None, "777": [1, 2]}, fh)
        self.assertTrue(notify.record_question(
            "888", "900", "sid-x", "/x", now=1000, path=p))
        q = notify.load_questions(p)
        self.assertIn("888", q)
        self.assertNotIn("555", q)
        self.assertNotIn("666", q)
        self.assertNotIn("777", q)

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
        self.cpath = str(Path(self.tmp.name) / "discord-cards.json")
        # point notify's question map + env at hermetic fixtures
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER,
                    "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "777001"}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_cards_path", lambda: self.cpath),
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
        if "capture-pane" in j:
            # #372 -- a genuinely BARE pane capture (a real `❯` boundary
            # line with nothing after it), matching what `send_continue`'s
            # own post-Enter verify actually observes on a successful
            # delivery. A bare `""` here does NOT model this: `_input_
            # line_text("")` returns None (no boundary locatable at all --
            # "capture failed"), which is a DIFFERENT, undeterminable state
            # this file's own #372 fix now correctly refuses to treat as a
            # confirmed delivery.
            return IDLE
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
        # (a dry-run diagnostic dropping the live question loses the answer).
        self.assertIn("888001", notify.load_questions(self.qpath))
        # #304: the reply id must NOT be dedup-marked either — this `state`
        # dict IS what run_once persists to disk unconditionally at the end
        # of every sweep (dry-run or not), so a mark left here by a
        # `--dry-run` troubleshooting invocation poisons the REAL next
        # sweep's dedup state and the user's actual answer is silently
        # skipped. (This assertion used to be `assertIn` — the ORIGINAL
        # comment here claimed "the reply id is still deduped in (unsaved)
        # state", which was false: the state is not unsaved, run_once saves
        # it every time.)
        self.assertNotIn("rep1", state.get("dreply_done", []))
        self.assertNotIn("rep1", state.get("dreply_acked", []))

    def test_delivers_from_a_channel_that_is_not_the_owners_normal_thread(self):
        # #296: a ❓ ping now routes to a SEPARATE per-owner questions thread
        # (claude-<owner>-q) — a DIFFERENT Discord channel id from the one
        # this fixture's env configures as "the owner's normal thread"
        # (DISCORD_NOTIFICATION_CHANNEL_ZBYNEK="777001", setUp above). Job 7
        # must still find + deliver it: it fetches from EVERY channel that
        # appears in the persisted question map, never a channel hardcoded
        # to the owner's "primary" thread — this is what lets the reply
        # pipeline keep working from the new thread with ZERO watchdog code
        # changes. A regression here would mean some future edit accidentally
        # scoped job 7 to a single, primary channel.
        notify.record_question("888002", "999888777", "sid-abc",
                               "/home/x/restreamer", now=time.time(),
                               path=self.qpath)
        state = {}
        panes = {"sid-abc": ("%1", IDLE)}
        q_msg = {"id": "repQ", "author": {"id": self.OWNER},
                "message_reference": {"message_id": "888002"},
                "content": "ano", "_channel": "999888777"}
        logs = wd.deliver_discord_replies(
            time.time(), self._run, state, panes, dry_run=True,
            discord_fetch=self._fetch([q_msg]))
        self.assertTrue(any("reply→" in ln for ln in logs), logs)
        # #304: a dry-run sweep must not mark the reply done in `state`
        # (that dict is persisted unconditionally by run_once) — the
        # "reply→" log line above is the proof this test actually cares
        # about (routing from the non-primary channel worked).
        self.assertNotIn("repQ", state.get("dreply_done", []))

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

    def test_a_dry_run_sweep_never_poisons_the_real_next_sweep(self):
        # #304: the exact failure mode the ticket describes — a
        # `python3 airuleset.py watchdog --once --dry-run` troubleshooting
        # run must never make the FOLLOWING real (non-dry-run) sweep, on the
        # SAME persisted `state`, believe the reply was already handled.
        notify.record_question("888001", "777001", "sid-abc", "/p",
                               now=time.time(), path=self.qpath)
        state = {}
        panes = {"sid-abc": ("%1", IDLE)}
        wd.deliver_discord_replies(
            time.time(), self._run, state, panes, dry_run=True,
            discord_fetch=self._fetch([self._reply_msg()]))
        logs = wd.deliver_discord_replies(
            time.time(), self._run, state, panes, dry_run=False,
            discord_fetch=self._fetch([self._reply_msg()]))
        self.assertTrue(any("reply→" in ln for ln in logs), logs)
        self.assertIn("rep1", state.get("dreply_done", []))

    def test_dry_run_delivery_via_idle_path_does_not_clear_real_alert_state(self):
        # #304 review MINOR-5: `_delivered`'s blocked.pop and the idle-path's
        # inputdead.pop ran unconditionally, so a --dry-run sweep that
        # happens to simulate delivery through the idle-pane fast path
        # silently wiped a REAL fallback-deadline clock and a REAL
        # wedge-episode alert counter — both persisted, both belonging to a
        # genuine earlier real sweep, neither actually cleared by anything
        # this dry-run call really did.
        notify.record_question("888001", "777001", "sid-abc", "/p",
                               now=time.time(), path=self.qpath)
        state = {"dreply_blocked": {"rep1": time.time() - 5},
                 "inputdead": {"sid-abc": 2}}
        panes = {"sid-abc": ("%1", IDLE)}
        wd.deliver_discord_replies(
            time.time(), self._run, state, panes, dry_run=True,
            discord_fetch=self._fetch([self._reply_msg()]))
        self.assertIn("rep1", state.get("dreply_blocked", {}))
        self.assertEqual(state.get("inputdead", {}).get("sid-abc"), 2)

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
        self.cpath = str(Path(self.tmp.name) / "cards.json")
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_cards_path", lambda: self.cpath),
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

    def test_dry_run_never_fakes_ticket_fallback_success(self):
        # #304 review MAJOR (found in adversarial review of the #304 fix
        # itself): the original code's `if dry_run: ok = True` faked a
        # successful gh comment post, then wrote a REAL, persisted
        # state["dreply_pointer"] entry — which a FOLLOWING real sweep
        # would type into the live pane as an instruction to go read a
        # ticket comment that was never actually posted. A --dry-run
        # troubleshooting call must make ZERO gh calls and leave
        # dreply_pointer untouched.
        self._record()
        now = time.time()
        state = {"dreply_blocked": {"repX": now - wd.DREPLY_TICKET_FALLBACK_S - 5}}
        run = ScriptedPaneRun([RUNNING_DRAFT])
        logs = wd.deliver_discord_replies(
            now, run, state, {"sid-abc": ("%1", RUNNING_DRAFT)}, dry_run=True,
            discord_fetch=self._fetch([self._reply()]), gh_comment=self._gh)
        self.assertEqual(self.gh_calls, [], logs)
        self.assertFalse(state.get("dreply_pointer"))
        self.assertNotIn("repX", state.get("dreply_done", []))
        # the reply stays pending, not silently dropped
        self.assertIn("888001", notify.load_questions(self.qpath))

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
        self.cpath = str(Path(self.tmp.name) / "cards.json")
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_cards_path", lambda: self.cpath),
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
        # swallowed) → ONE corrective Escape+Enter; second verify shows bare
        # → delivered. First item is send_continue's OWN pre-type capture
        # (issue #36 — it now checks the agent-strip selector before typing);
        # an ordinary IDLE pane there needs no escape.
        composed_tail = "auto-arm ho nalepí sám."
        run = ScriptedPaneRun([self.IDLE, self._wedged_pane(composed_tail), self.IDLE])
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

    def test_an_unreadable_verify_capture_is_never_confirmed_delivered(self):
        """#372 (4th incident, forensically flagged): a false "delivered"
        confirmation is a trust-breaking defect — the sender legitimately
        believes their Discord reply reached the session while it never
        did. `_input_line_text` returns `None` (undeterminable — a dialog,
        a spinner, a genuinely unreadable capture) as a DISTINCT value from
        `""` (genuinely bare, confirmed empty) — but the verify loop's
        `while t2 and tries < 2` / `if t2:` both treat `None` as FALSY,
        identically to a confirmed-empty box, so an UNREADABLE post-send
        capture was silently accepted as proof of delivery. This must be
        treated exactly like "still wedged": not marked delivered, no
        premature done-state, retried/reported next cycle."""
        UNREADABLE = "some fullscreen dialog with no boundary at all\n"
        # captures: [send_continue's own pre-type strip-selected check
        # (ordinary idle, no escape needed), the verify capture -- made
        # UNREADABLE, not merely "still shows our text"]
        run = ScriptedPaneRun([self.IDLE, UNREADABLE])
        state = {}
        logs = wd.deliver_discord_replies(
            time.time(), run, state, {"sid-abc": ("%1", self.IDLE)},
            dry_run=False, discord_fetch=lambda ch, t: [self._reply()],
            gh_comment=lambda *a: True)
        self.assertNotIn("repW", state.get("dreply_done", []),
                         "an unreadable verify capture must NEVER be "
                         "treated as a confirmed delivery: %r" % logs)
        self.assertIn("888001", notify.load_questions(self.qpath),
                      "the question must stay tracked -- not silently "
                      "dropped on an unverified 'delivery'")
        self.assertTrue(any("wedge" in ln.lower() or "unreadable" in ln.lower()
                            for ln in logs), logs)


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
        self.cpath = str(Path(self.tmp.name) / "cards.json")
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_cards_path", lambda: self.cpath),
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


# --------------------------------------------------------------------------- #
# #298 -- Discord reply on a per-ticket DONE card -> reopen the ticket with
# the remark. The send-time card map (repo/issue <- message id) notify.py
# needs to recover it later.
# --------------------------------------------------------------------------- #
class CardMap(unittest.TestCase):
    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "discord-cards.json")

    def test_record_and_load(self):
        p = self._p()
        self.assertTrue(notify.record_card_message(
            "111", "900", "zbynekdrlik/airuleset", 42, now=1000, path=p))
        c = notify.load_cards(p)
        self.assertEqual(c["111"]["repo"], "zbynekdrlik/airuleset")
        self.assertEqual(c["111"]["issue"], 42)
        self.assertEqual(c["111"]["channel"], "900")
        self.assertEqual(c["111"]["ts"], 1000)

    def test_missing_fields_are_rejected(self):
        p = self._p()
        self.assertFalse(notify.record_card_message("", "900", "o/r", 1, path=p))
        self.assertFalse(notify.record_card_message("111", "900", "", 1, path=p))
        self.assertFalse(notify.record_card_message("111", "900", "o/r", None, path=p))
        # non-numeric ids refused, mirroring record_question's own guard
        self.assertFalse(notify.record_card_message("<Mock id=1>", "900", "o/r",
                                                    1, path=p))
        self.assertFalse(notify.record_card_message("111", "thread-z", "o/r",
                                                    1, path=p))
        self.assertEqual(notify.load_cards(p), {})

    def test_stale_entries_pruned_on_write(self):
        p = self._p()
        notify.record_card_message("100", "900", "o/r", 1, now=0, path=p)
        notify.record_card_message("200", "900", "o/r", 2,
                                   now=notify._CARDS_TTL_S + 100, path=p)
        c = notify.load_cards(p)
        self.assertNotIn("100", c)
        self.assertIn("200", c)

    def test_hard_cap_keeps_newest(self):
        p = self._p()
        for i in range(notify._CARDS_MAX + 5):
            notify.record_card_message("6%04d" % i, "900", "o/r", i, now=i, path=p)
        c = notify.load_cards(p)
        self.assertLessEqual(len(c), notify._CARDS_MAX)
        self.assertIn("6%04d" % (notify._CARDS_MAX + 4), c)
        self.assertNotIn("60000", c)

    def test_load_bad_file_is_empty(self):
        p = self._p()
        Path(p).write_text("not json")
        self.assertEqual(notify.load_cards(p), {})

    def test_malformed_legacy_entry_never_crashes_the_prune(self):
        # MINOR-7 (#297/#298 review): a non-dict value (a bare scalar/list
        # crossing a legacy-file boundary) must not crash record_card_message
        # via a bare .get() — it is treated as immediately prunable instead.
        import json as _json
        p = self._p()
        with open(p, "w", encoding="utf-8") as fh:
            _json.dump({"555": "not-a-dict", "666": None, "777": [1, 2]}, fh)
        self.assertTrue(notify.record_card_message(
            "888", "900", "o/r", 1, now=1000, path=p))
        c = notify.load_cards(p)
        self.assertIn("888", c)
        self.assertNotIn("555", c)
        self.assertNotIn("666", c)
        self.assertNotIn("777", c)


class PostDiscordReturnsMessageId(unittest.TestCase):
    """_post_discord now returns the Discord message id on a real POST
    (#298 needs it to record the card map) — while staying TRUTHY for a
    caller that only checks success (an empty-body test double, a bare
    True/False mock elsewhere in this test suite)."""

    def test_returns_the_real_message_id(self):
        import unittest.mock as m

        def fake_urlopen(req, timeout=0):
            return m.Mock(read=lambda: b'{"id": "555666777"}')

        with m.patch.object(notify.urllib.request, "urlopen", fake_urlopen):
            got = notify._post_discord("tok", "123", "hi")
        self.assertEqual(got, "555666777")

    def test_empty_body_stays_truthy(self):
        import unittest.mock as m

        def fake_urlopen(req, timeout=0):
            return m.Mock(read=lambda: b"")

        with m.patch.object(notify.urllib.request, "urlopen", fake_urlopen):
            got = notify._post_discord("tok", "123", "hi")
        self.assertTrue(got)
        self.assertIsInstance(got, bool)   # never a fake "id" for an unreadable body

    def test_failure_is_falsy(self):
        import unittest.mock as m

        def fake_urlopen(req, timeout=0):
            raise OSError("boom")

        with m.patch.object(notify.urllib.request, "urlopen", fake_urlopen):
            got = notify._post_discord("tok", "123", "hi")
        self.assertFalse(got)


class SendReturnMessageId(unittest.TestCase):
    def setUp(self):
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_NOTIFICATION_CHANNEL_ID": "999"}
        p = m.patch.object(notify, "_read_env", lambda: dict(self.env))
        p.start()
        self.addCleanup(p.stop)

    def test_default_stays_a_bare_string(self):
        import unittest.mock as m
        with m.patch.object(notify, "_post_discord", return_value="123456"):
            got = notify.send("hi")
        self.assertEqual(got, "sent")

    def test_opt_in_returns_status_and_id(self):
        import unittest.mock as m
        with m.patch.object(notify, "_post_discord", return_value="123456"):
            got = notify.send("hi", return_message_id=True)
        self.assertEqual(got, ("sent", "123456"))

    def test_a_mocked_bare_true_never_masquerades_as_an_id(self):
        import unittest.mock as m
        with m.patch.object(notify, "_post_discord", return_value=True):
            status, mid = notify.send("hi", return_message_id=True)
        self.assertEqual(status, "sent")
        self.assertIsNone(mid)

    def test_no_config_returns_a_none_id_pair(self):
        self.env.pop("DISCORD_NOTIFICATION_CHANNEL_ID")
        got = notify.send("hi", return_message_id=True)
        self.assertEqual(got, ("no-config", None))

    def test_dry_run_returns_a_none_id_pair(self):
        got = notify.send("hi", dry_run=True, return_message_id=True)
        self.assertEqual(got, ("dry-run", None))


class SendKindRouting(unittest.TestCase):
    """#368: send(kind="questions") must route through the SAME per-owner
    `-q` questions-thread cascade `notification_channel(kind="questions")`
    already provides — needed so a Python-side daily re-ask (watchdog) lands
    in the owner's separate questions thread, not mixed into their normal
    ✅/card thread, mirroring what hooks/notify-discord-send.sh already does
    for every interactive ❓ ping."""

    def setUp(self):
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": "773451844110385193",
                    "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "777001",
                    "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q": "777099"}
        p = m.patch.object(notify, "_read_env", lambda: dict(self.env))
        p.start()
        self.addCleanup(p.stop)
        self.posted = []

    def _post(self, token, channel, content):
        self.posted.append(channel)
        return "555666"

    def test_kind_questions_routes_to_the_dedicated_thread(self):
        import unittest.mock as m
        with m.patch.object(notify, "_post_discord", self._post):
            status = notify.send("hi", owner="zbynek", kind="questions")
        self.assertEqual(status, "sent")
        self.assertEqual(self.posted, ["777099"])

    def test_kind_default_stays_on_the_normal_thread(self):
        import unittest.mock as m
        with m.patch.object(notify, "_post_discord", self._post):
            notify.send("hi", owner="zbynek")
        self.assertEqual(self.posted, ["777001"])

    def test_kind_questions_falls_back_to_the_normal_thread_when_unconfigured(self):
        del self.env["DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q"]
        import unittest.mock as m
        with m.patch.object(notify, "_post_discord", self._post):
            notify.send("hi", owner="zbynek", kind="questions")
        self.assertEqual(self.posted, ["777001"])


class ParseDiscordCardReply(unittest.TestCase):
    OWNER = "773451844110385193"
    CARDMAP = {"card1": {"repo": "zbynekdrlik/airuleset", "issue": 42,
                         "channel": "777001", "ts": 1000}}

    def _msg(self, **over):
        m = {"id": "rep1", "author": {"id": self.OWNER},
             "message_reference": {"message_id": "card1"},
             "content": "toto este chyba retry logika"}
        m.update(over)
        return m

    def test_valid_card_reply(self):
        r = wd.parse_discord_card_reply(self._msg(), {self.OWNER}, self.CARDMAP)
        self.assertEqual(r["repo"], "zbynekdrlik/airuleset")
        self.assertEqual(r["issue"], 42)
        self.assertEqual(r["text"], "toto este chyba retry logika")
        self.assertEqual(r["reply_id"], "rep1")

    def test_non_owner_rejected(self):
        self.assertIsNone(wd.parse_discord_card_reply(
            self._msg(author={"id": "666"}), {self.OWNER}, self.CARDMAP))

    def test_not_a_reply_rejected(self):
        m = self._msg()
        del m["message_reference"]
        self.assertIsNone(wd.parse_discord_card_reply(m, {self.OWNER}, self.CARDMAP))

    def test_reply_to_untracked_message_rejected(self):
        self.assertIsNone(wd.parse_discord_card_reply(
            self._msg(message_reference={"message_id": "unknown"}),
            {self.OWNER}, self.CARDMAP))

    def test_empty_content_rejected(self):
        self.assertIsNone(wd.parse_discord_card_reply(
            self._msg(content="<@1>"), {self.OWNER}, self.CARDMAP))

    def test_garbage_message_rejected(self):
        self.assertIsNone(wd.parse_discord_card_reply(None, {self.OWNER}, self.CARDMAP))
        self.assertIsNone(wd.parse_discord_card_reply("x", {self.OWNER}, self.CARDMAP))


class RepoLivePane(unittest.TestCase):
    def test_finds_matching_repo(self):
        import unittest.mock as m
        with m.patch.object(notify, "repo_name_for",
                            lambda cwd, run=None: "airuleset" if "one" in cwd
                            else "other"):
            got = wd._repo_live_pane(
                "airuleset",
                {"sid-1": "/home/x/one", "sid-2": "/home/x/two"},
                {"sid-1": ("%1", "cap1"), "sid-2": ("%2", "cap2")})
        self.assertEqual(got, ("sid-1", "%1", "/home/x/one"))

    def test_no_match_returns_none(self):
        import unittest.mock as m
        with m.patch.object(notify, "repo_name_for", lambda cwd, run=None: "other"):
            got = wd._repo_live_pane("airuleset", {"sid-1": "/x"},
                                     {"sid-1": ("%1", "cap1")})
        self.assertIsNone(got)

    def test_empty_name_returns_none(self):
        self.assertIsNone(wd._repo_live_pane("", {}, {}))

    def test_a_cwd_with_no_live_pane_is_never_matched(self):
        # cwd_by_sid can carry a sid that just dropped out of panes_by_sid
        # this sweep — must not crash / must not match.
        import unittest.mock as m
        with m.patch.object(notify, "repo_name_for", lambda cwd, run=None: "airuleset"):
            got = wd._repo_live_pane("airuleset", {"sid-gone": "/x"}, {})
        self.assertIsNone(got)


class FlagDeliveryTarget(unittest.TestCase):
    def test_question_kind_prefers_the_live_asking_session(self):
        target = {"kind": "question", "session": "sid-abc", "cwd": "/x"}
        panes = {"sid-abc": ("%1", "cap")}
        got = wd._flag_delivery_target(target, panes, {"sid-abc": "/x"})
        # MAJOR-1 (#297/#298 review): the exact-asking-session branch carries
        # `exact=True` — its own idle/draft gate, never job 8's stronger
        # at-rest discipline.
        self.assertEqual(got, ("sid-abc", "%1", "/x", True))

    def test_question_kind_falls_back_to_repo_when_session_is_dead(self):
        import unittest.mock as m
        target = {"kind": "question", "session": "sid-dead", "cwd": "/repo/one"}
        with m.patch.object(notify, "repo_name_for",
                            lambda cwd, run=None: "one" if "one" in cwd else "?"):
            got = wd._flag_delivery_target(
                target, {"sid-live": ("%2", "cap")}, {"sid-live": "/repo/one"})
        self.assertEqual(got, ("sid-live", "%2", "/repo/one", False))

    def test_card_kind_resolves_via_repo(self):
        import unittest.mock as m
        target = {"kind": "card", "repo": "zbynekdrlik/airuleset", "issue": 42}
        with m.patch.object(notify, "repo_name_for", lambda cwd, run=None: "airuleset"):
            got = wd._flag_delivery_target(
                target, {"sid-live": ("%3", "cap")}, {"sid-live": "/x"})
        self.assertEqual(got, ("sid-live", "%3", "/x", False))

    def test_nothing_live_returns_none(self):
        target = {"kind": "card", "repo": "o/r", "issue": 1}
        got = wd._flag_delivery_target(target, {}, {})
        self.assertIsNone(got)


class ComposeFlagPrompt(unittest.TestCase):
    def test_quotes_the_flagged_message_and_names_the_protocol(self):
        p = wd.compose_flag_prompt("<@123> pozor toto je zle")
        self.assertIn("pozor toto je zle", p)
        self.assertNotIn("<@123>", p)
        self.assertIn("❓", p)
        self.assertNotIn("\n", p)   # single line — send_continue types+Enter

    def test_empty_text_still_composes(self):
        p = wd.compose_flag_prompt("")
        self.assertTrue(p)


class FlaggedEmoji(unittest.TestCase):
    def test_question_mark_detected(self):
        msg = {"reactions": [{"emoji": {"name": "❓"}, "count": 1}]}
        self.assertEqual(wd._flagged_emoji(msg), "❓")

    def test_white_question_mark_detected(self):
        msg = {"reactions": [{"emoji": {"name": "❔"}, "count": 2}]}
        self.assertEqual(wd._flagged_emoji(msg), "❔")

    def test_zero_count_not_flagged(self):
        msg = {"reactions": [{"emoji": {"name": "❓"}, "count": 0}]}
        self.assertEqual(wd._flagged_emoji(msg), "")

    def test_other_emoji_not_flagged(self):
        msg = {"reactions": [{"emoji": {"name": "👍"}, "count": 5}]}
        self.assertEqual(wd._flagged_emoji(msg), "")

    def test_no_reactions_field(self):
        self.assertEqual(wd._flagged_emoji({}), "")
        self.assertEqual(wd._flagged_emoji({"reactions": None}), "")
        self.assertEqual(wd._flagged_emoji("x"), "")


class FlagTarget(unittest.TestCase):
    QMAP = {"q1": {"session": "sid-abc", "cwd": "/x"}}
    CARDMAP = {"c1": {"repo": "o/r", "issue": 5}}

    def test_resolves_question(self):
        t = wd._flag_target("q1", self.QMAP, self.CARDMAP)
        self.assertEqual(t, {"kind": "question", "session": "sid-abc", "cwd": "/x"})

    def test_resolves_card(self):
        t = wd._flag_target("c1", self.QMAP, self.CARDMAP)
        self.assertEqual(t, {"kind": "card", "repo": "o/r", "issue": 5})

    def test_untracked_message_is_none(self):
        self.assertIsNone(wd._flag_target("nope", self.QMAP, self.CARDMAP))


class FetchReactionUsers(unittest.TestCase):
    def test_empty_args_return_empty(self):
        self.assertEqual(wd.fetch_reaction_users("", "1", "❓", "tok"), [])
        self.assertEqual(wd.fetch_reaction_users("ch", "", "❓", "tok"), [])
        self.assertEqual(wd.fetch_reaction_users("ch", "1", "❓", ""), [])

    def test_parses_user_list(self):
        import unittest.mock as m
        with m.patch.object(wd, "_discord_get",
                            lambda url, token, timeout=6: b'[{"id":"1"},{"id":"2"}]'):
            got = wd.fetch_reaction_users("ch", "1", "❓", "tok")
        self.assertEqual(got, [{"id": "1"}, {"id": "2"}])

    def test_network_error_is_empty(self):
        import unittest.mock as m
        with m.patch.object(wd, "_discord_get", side_effect=OSError("x")):
            self.assertEqual(wd.fetch_reaction_users("ch", "1", "❓", "tok"), [])


class ReactedByOwner(unittest.TestCase):
    def test_owner_present(self):
        self.assertTrue(wd._reacted_by_owner([{"id": "1"}, {"id": "2"}], {"2"}))

    def test_owner_absent(self):
        self.assertFalse(wd._reacted_by_owner([{"id": "1"}], {"2"}))

    def test_empty_or_garbage(self):
        self.assertFalse(wd._reacted_by_owner([], {"2"}))
        self.assertFalse(wd._reacted_by_owner(None, {"2"}))
        self.assertFalse(wd._reacted_by_owner(["x"], {"2"}))


class CardReopenFlow(unittest.TestCase):
    def _fake_gh(self, calls, label_exists=True, comment_ok=True):
        def gh(argv, input_text=None):
            calls.append((list(argv), input_text))
            if argv[:3] == ["gh", "label", "list"]:
                return (True, '[{"name":"prio:bounce"}]' if label_exists else "[]")
            if argv[:3] == ["gh", "issue", "comment"]:
                return (comment_ok, "")
            return (True, "")
        return gh

    def test_happy_path_reopens_comments_and_labels(self):
        calls = []
        gh = self._fake_gh(calls)
        ok = wd._card_reopen_flow("o/r", 42, "toto este chyba", gh_fn=gh)
        self.assertTrue(ok)
        kinds = [c[0][:3] for c in calls]
        self.assertIn(["gh", "issue", "reopen"], kinds)
        self.assertIn(["gh", "issue", "comment"], kinds)
        self.assertIn(["gh", "issue", "edit"], kinds)
        comment_call = [c for c in calls if c[0][:3] == ["gh", "issue", "comment"]][0]
        self.assertIn("toto este chyba", comment_call[1])
        reopen_call = [c for c in calls if c[0][:3] == ["gh", "issue", "reopen"]][0]
        self.assertEqual(reopen_call[0], ["gh", "issue", "reopen", "42", "-R", "o/r"])

    def test_missing_label_is_created_never_forced(self):
        calls = []
        gh = self._fake_gh(calls, label_exists=False)
        wd._card_reopen_flow("o/r", 42, "x", gh_fn=gh)
        create_calls = [c for c in calls if c[0][:3] == ["gh", "label", "create"]]
        self.assertEqual(len(create_calls), 1)
        self.assertNotIn("--force", create_calls[0][0])

    def test_existing_label_is_never_recreated(self):
        calls = []
        gh = self._fake_gh(calls, label_exists=True)
        wd._card_reopen_flow("o/r", 42, "x", gh_fn=gh)
        create_calls = [c for c in calls if c[0][:3] == ["gh", "label", "create"]]
        self.assertEqual(len(create_calls), 0)

    def test_comment_failure_reports_false(self):
        calls = []
        gh = self._fake_gh(calls, comment_ok=False)
        ok = wd._card_reopen_flow("o/r", 42, "x", gh_fn=gh)
        self.assertFalse(ok)


IDLE_298 = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"


class DeliverDiscordRepliesCardReopen(unittest.TestCase):
    OWNER = "773451844110385193"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        self.cpath = str(Path(self.tmp.name) / "cards.json")
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER,
                    "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "777001"}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_cards_path", lambda: self.cpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        self.gh_calls = []
        self.sent = []

    def _run(self, argv, timeout=8):
        self.sent.append(argv)
        j = " ".join(argv)
        if "pane_in_mode" in j:
            return "0"
        # _nudge_repo_pane re-captures FRESH immediately before the send
        # (#176's own "never trust a stale capture" lesson) rather than
        # reusing panes_by_sid's earlier-in-the-sweep capture — so a fake
        # `capture-pane` reply is needed for the nudge test to see an idle
        # box at all.
        if "capture-pane" in j:
            return IDLE_298
        return ""

    def _gh(self, argv, input_text=None):
        self.gh_calls.append((list(argv), input_text))
        if argv[:3] == ["gh", "label", "list"]:
            return (True, '[{"name":"prio:bounce"}]')
        return (True, "")

    def test_reply_on_card_reopens_and_comments(self):
        notify.record_card_message("888005", "777001", "zbynekdrlik/airuleset",
                                   42, now=time.time(), path=self.cpath)
        msg = {"id": "rep1", "author": {"id": self.OWNER},
              "message_reference": {"message_id": "888005"},
              "content": "toto este nefunguje"}
        state = {}
        logs = wd.deliver_discord_replies(
            time.time(), self._run, state, {}, dry_run=False,
            discord_fetch=lambda ch, t: [msg], card_gh_fn=self._gh)
        self.assertTrue(any("card-reopen" in ln for ln in logs), logs)
        self.assertIn("rep1", state.get("dcard_done", []))
        comment_calls = [c for c in self.gh_calls if c[0][:3] == ["gh", "issue", "comment"]]
        self.assertEqual(len(comment_calls), 1)
        self.assertIn("toto este nefunguje", comment_calls[0][1])
        self.assertIn(["gh", "issue", "reopen", "42", "-R", "zbynekdrlik/airuleset"],
                      [c[0] for c in self.gh_calls])

    def test_second_reply_on_same_card_gets_a_new_comment_no_dup_reopen(self):
        notify.record_card_message("888005", "777001", "o/r", 7,
                                   now=time.time(), path=self.cpath)
        state = {"dcard_done": ["rep1"]}
        msg2 = {"id": "rep2", "author": {"id": self.OWNER},
               "message_reference": {"message_id": "888005"},
               "content": "dalsia poznamka"}
        wd.deliver_discord_replies(
            time.time(), self._run, state, {}, dry_run=False,
            discord_fetch=lambda ch, t: [msg2], card_gh_fn=self._gh)
        self.assertIn("rep2", state.get("dcard_done", []))
        comment_calls = [c for c in self.gh_calls if c[0][:3] == ["gh", "issue", "comment"]]
        self.assertEqual(len(comment_calls), 1)   # rep1 already handled

    def test_dry_run_makes_no_real_gh_calls(self):
        notify.record_card_message("888005", "777001", "o/r", 7,
                                   now=time.time(), path=self.cpath)
        msg = {"id": "rep1", "author": {"id": self.OWNER},
              "message_reference": {"message_id": "888005"}, "content": "x"}
        state = {}
        wd.deliver_discord_replies(
            time.time(), self._run, state, {}, dry_run=True,
            discord_fetch=lambda ch, t: [msg], card_gh_fn=self._gh)
        self.assertEqual(self.gh_calls, [])

    def test_non_owner_reply_on_card_ignored(self):
        notify.record_card_message("888005", "777001", "o/r", 7,
                                   now=time.time(), path=self.cpath)
        msg = {"id": "rep1", "author": {"id": "666"},
              "message_reference": {"message_id": "888005"}, "content": "x"}
        state = {}
        wd.deliver_discord_replies(
            time.time(), self._run, state, {}, dry_run=False,
            discord_fetch=lambda ch, t: [msg], card_gh_fn=self._gh)
        self.assertEqual(self.gh_calls, [])
        self.assertNotIn("rep1", state.get("dcard_done", []))

    def test_reopened_card_nudges_a_live_idle_pane_of_that_repo(self):
        import unittest.mock as m
        notify.record_card_message("888005", "777001", "zbynekdrlik/airuleset",
                                   9, now=time.time(), path=self.cpath)
        msg = {"id": "rep1", "author": {"id": self.OWNER},
              "message_reference": {"message_id": "888005"}, "content": "x"}
        state = {}
        cwd_by_sid = {"sid-live": "/home/x/airuleset"}
        panes = {"sid-live": ("%1", IDLE_298)}
        with m.patch.object(notify, "repo_name_for", lambda cwd, run=None: "airuleset"):
            wd.deliver_discord_replies(
                time.time(), self._run, state, panes, dry_run=False,
                discord_fetch=lambda ch, t: [msg], card_gh_fn=self._gh,
                cwd_by_sid=cwd_by_sid)
        literal = [a for a in self.sent if "-l" in a]
        self.assertTrue(any("9" in a[-1] for a in literal), self.sent)

    def test_no_cards_no_questions_is_a_noop(self):
        logs = wd.deliver_discord_replies(
            time.time(), self._run, {}, {}, dry_run=False,
            discord_fetch=lambda ch, t: [{"id": "x"}], card_gh_fn=self._gh)
        self.assertEqual(logs, [])
        self.assertEqual(self.gh_calls, [])

    def test_dry_run_does_not_mark_the_reply_done(self):
        # MAJOR-2 (#297/#298 review): a dry-run "reopen" must NEVER consume
        # the dedup slot, or the real reply is silently swallowed on the
        # first genuinely-live sweep.
        notify.record_card_message("888005", "777001", "o/r", 7,
                                   now=time.time(), path=self.cpath)
        msg = {"id": "rep1", "author": {"id": self.OWNER},
              "message_reference": {"message_id": "888005"}, "content": "x"}
        state = {}
        wd.deliver_discord_replies(
            time.time(), self._run, state, {}, dry_run=True,
            discord_fetch=lambda ch, t: [msg], card_gh_fn=self._gh)
        self.assertNotIn("rep1", state.get("dcard_done", []))

    def test_card_reply_already_done_makes_no_new_gh_calls_on_refetch(self):
        # MAJOR-3 (#297/#298 review): a SECOND sweep that re-fetches an
        # ALREADY-`dcard_done` reply (Discord's own channel history keeps
        # returning old messages every poll) must make zero new gh calls.
        notify.record_card_message("888005", "777001", "o/r", 7,
                                   now=time.time(), path=self.cpath)
        msg = {"id": "rep1", "author": {"id": self.OWNER},
              "message_reference": {"message_id": "888005"}, "content": "x"}
        state = {"dcard_done": ["rep1"]}
        wd.deliver_discord_replies(
            time.time(), self._run, state, {}, dry_run=False,
            discord_fetch=lambda ch, t: [msg], card_gh_fn=self._gh)
        self.assertEqual(self.gh_calls, [])

    def test_reopen_forgets_the_run_card_dedup_marker(self):
        # MAJOR-5 (#297/#298 review): a successful reopen must release the
        # ticket's own run-card dedup marker, so a fresh fix's completion
        # card can send again rather than being suppressed forever by the
        # FIRST (now-flagged) card's dedup claim. Isolate `_claude_dir` —
        # this drives the REAL dedup store, and the playbook's own
        # established rule requires it for any test reaching a notify
        # send/marker path.
        import unittest.mock as m
        import notify as ntfy
        dedup_dir = TemporaryDirectory()
        self.addCleanup(dedup_dir.cleanup)
        p = m.patch.object(ntfy, "_claude_dir", lambda: dedup_dir.name)
        p.start()
        self.addCleanup(p.stop)
        key = "airuleset#42"
        ntfy._dedup_claim(key)
        ntfy._dedup_mark_status(key, "sent")
        self.assertTrue(ntfy.marker_delivered(key))
        notify.record_card_message("888005", "777001", "zbynekdrlik/airuleset",
                                   42, now=time.time(), path=self.cpath)
        msg = {"id": "rep1", "author": {"id": self.OWNER},
              "message_reference": {"message_id": "888005"}, "content": "x"}
        state = {}
        wd.deliver_discord_replies(
            time.time(), self._run, state, {}, dry_run=False,
            discord_fetch=lambda ch, t: [msg], card_gh_fn=self._gh)
        self.assertFalse(ntfy.marker_delivered(key))


class DeliverDiscordRepliesFlagReact(unittest.TestCase):
    """(#297) A ❓/❔ reaction on a TRACKED message — non-owner rejection,
    second-sweep dedup, and the untracked-message fast path (MAJOR-3, the
    adversarial review's own requested mutation-proof regression tests)."""
    OWNER = "773451844110385193"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        self.cpath = str(Path(self.tmp.name) / "cards.json")
        import unittest.mock as m
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER,
                    "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "777001"}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_cards_path", lambda: self.cpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        self.react_calls = []
        self.sent = []

    def _run(self, argv, timeout=8):
        self.sent.append(argv)
        return ""

    def _flagged_msg(self, mid="888010"):
        return {"id": mid,
               "reactions": [{"emoji": {"name": "❓"}, "count": 1}]}

    def _react_fetch(self, users):
        def f(ch, mid, emoji, token):
            self.react_calls.append(mid)
            return users
        return f

    def test_reaction_from_non_owner_is_ignored(self):
        notify.record_card_message("888010", "777001", "zbynekdrlik/airuleset",
                                   9, now=time.time(), path=self.cpath)
        state = {}
        logs = wd.deliver_discord_replies(
            time.time(), self._run, state, {}, dry_run=False,
            discord_fetch=lambda ch, t: [self._flagged_msg()],
            reaction_fetch=self._react_fetch([{"id": "999999999999999999"}]))
        self.assertTrue(self.react_calls)          # the call WAS made
        self.assertFalse(any("flag-react" in ln for ln in logs), logs)
        self.assertNotIn("888010", state.get("dreact_done", []))

    def test_second_sweep_of_an_already_flagged_message_calls_reaction_fetch_zero_times(self):
        notify.record_card_message("888010", "777001", "zbynekdrlik/airuleset",
                                   9, now=time.time(), path=self.cpath)
        state = {"dreact_done": ["888010"]}
        wd.deliver_discord_replies(
            time.time(), self._run, state, {}, dry_run=False,
            discord_fetch=lambda ch, t: [self._flagged_msg()],
            reaction_fetch=self._react_fetch([{"id": self.OWNER}]))
        self.assertEqual(self.react_calls, [])

    def test_untracked_flagged_message_never_calls_reaction_fetch(self):
        # `888010` is flagged (nonzero ❓ count) but NEVER recorded in either
        # the question map or the card map — `_flag_target` returns None,
        # and the whole point of #297's design is that the extra
        # `reaction_fetch` call is spent ONLY on a tracked message. A
        # DIFFERENT card (a different numeric id) is seeded so the
        # function's own "nothing tracked at all -> return early" guard is
        # not what's keeping the count at zero — this genuinely exercises
        # the per-message `_flag_target` lookup inside the fetch loop.
        notify.record_card_message("888020", "777001", "o/r", 1,
                                   now=time.time(), path=self.cpath)
        state = {}
        wd.deliver_discord_replies(
            time.time(), self._run, state, {}, dry_run=False,
            discord_fetch=lambda ch, t: [self._flagged_msg()],
            reaction_fetch=self._react_fetch([{"id": self.OWNER}]))
        self.assertEqual(self.react_calls, [])
