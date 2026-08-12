"""Terminal-answered ❓ pruning — the 'otazky' badge must be trustworthy.

2026-07-22 complaint: the statusline badge counted 14 machine-global questions
in a project with zero pending — and most of them were already answered by the
user TYPING DIRECTLY into the asking session (the map only dropped entries on
the Discord-reply route or at the 24h TTL). prune_answered_questions drops an
entry the moment its session's transcript shows a HUMAN prompt newer than the
❓ — machine-typed prompts (watchdog nudges/deliveries, auto-armed /goal,
harness task-notifications, slash-command echoes) and tool_result entries must
never count as an answer.
"""

import json
import sys
import time
import unittest
import unittest.mock as m
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify
import watchdog as wd

CWD = "/home/x/devel/demo"
SID = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def _user(epoch, content):
    return {"type": "user", "timestamp": _iso(epoch),
            "message": {"role": "user", "content": content}}


class PruneAnsweredQuestions(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        p = m.patch.object(notify, "_questions_path", lambda: self.qpath)
        p.start()
        self.addCleanup(p.stop)
        self.projects = Path(self.tmp.name) / "projects"
        self.now = time.time()
        self.qts = self.now - 3600                     # the ❓ pinged 1h ago

    def _record(self):
        notify.record_question("888001", "777001", SID, CWD, now=self.qts,
                               path=self.qpath, question="Ticket #9 — ako?")

    def _transcript(self, entries):
        d = self.projects / wd.encode_project_dir(CWD)
        d.mkdir(parents=True, exist_ok=True)
        (d / (SID + ".jsonl")).write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n")

    def _prune(self):
        return wd.prune_answered_questions(self.now,
                                           projects_dir=str(self.projects))

    def test_human_prompt_after_question_prunes_the_entry(self):
        self._record()
        self._transcript([_user(self.qts + 600, "nejake otazky na mna?")])
        logs = self._prune()
        self.assertTrue(any("pruned" in ln for ln in logs), logs)
        self.assertNotIn("888001", notify.load_questions(self.qpath))

    def test_human_prompt_before_question_keeps_it(self):
        self._record()
        self._transcript([_user(self.qts - 600, "sprav to takto")])
        self.assertEqual(self._prune(), [])
        self.assertIn("888001", notify.load_questions(self.qpath))

    def test_machine_prompts_never_count_as_answers(self):
        self._record()
        self._transcript([
            _user(self.qts + 100, "continue"),
            _user(self.qts + 200, "stuck-check: tvrdíš ⏳ WORKING ale ..."),
            _user(self.qts + 300, "Priorita: prio:bounce #12 — rieš"),
            _user(self.qts + 400, "/goal MASTER LOOP — ..."),
            _user(self.qts + 500, "Odpoveď z Discordu: 2026-07-22 ..."),
            _user(self.qts + 600, "<task-notification>\n<task-id>x</task-id>"),
            _user(self.qts + 650, "<command-name>/compact</command-name>"),
            _user(self.qts + 700, [{"type": "tool_result", "content": "ok"}]),
            # #366 -- GOAL_QUESTION_PARK_TEXT (the 30-min unanswered-question
            # backstop) was missing from _MACHINE_PROMPT_PREFIXES. Use the
            # REAL constant (review MINOR-2), not a hand-copied literal --
            # a hand-copy stays green forever even if the constant's own
            # wording later drifts away from the "question-timeout:" prefix
            # this test exists to lock.
            _user(self.qts + 750, wd.GOAL_QUESTION_PARK_TEXT),
        ])
        self.assertEqual(self._prune(), [])
        self.assertIn("888001", notify.load_questions(self.qpath))

    def test_the_park_text_constant_still_starts_with_the_locked_prefix(self):
        # #366 review MINOR-2: nothing else ties GOAL_QUESTION_PARK_TEXT to
        # the "question-timeout:" entry in _MACHINE_PROMPT_PREFIXES -- a
        # future rewording of the constant's OWN head could silently
        # regress production while every other test (which uses the real
        # constant, per the fix above) stays green for the wrong reason.
        self.assertTrue(wd.GOAL_QUESTION_PARK_TEXT.startswith("question-timeout:"),
                        wd.GOAL_QUESTION_PARK_TEXT)

    def test_compact_continuation_summary_never_counts_as_an_answer(self):
        # #366 -- ask-and-continue (❓ ASKED + ⏳ WORKING) keeps a session
        # working OTHER tickets; a ticket-boundary /compact (job 14, or the
        # synchronous #65 path) landing minutes later writes a REAL,
        # top-level `user`-typed entry (CC's own compact-continuation
        # summary) that is neither isMeta nor a <system-reminder> block --
        # _last_human_prompt_ts wrongly read it as "the user answered",
        # pruning the still-unanswered ❓ entry within minutes (the reported
        # incident's own timeline: ping delivered, entry gone a few minutes
        # later, footer shows no Q). Mirrors #350's own two-sided fix for
        # the sibling _goal_blocked_on_unanswered_question classifier:
        # the wording prefix AND CC's structural isCompactSummary flag.
        self._record()
        self._transcript([
            {"type": "user", "timestamp": _iso(self.qts + 120),
             "message": {"content":
                 "This session is being continued from a previous "
                 "conversation that ran out of context. The summary below "
                 "covers the earlier portion of the conversation.\n\n"
                 "Summary:\n1. Primary Request and Intent:\n..."}},
        ])
        self.assertEqual(self._prune(), [])
        self.assertIn("888001", notify.load_questions(self.qpath))

    def test_compact_continuation_summary_flagged_but_reworded_still_never_counts(self):
        # #350 round-2's own hardening: a future CC build could reword the
        # preamble -- the STRUCTURAL isCompactSummary flag must catch it
        # even when the wording no longer matches the known prefix at all.
        self._record()
        self._transcript([
            {"type": "user", "timestamp": _iso(self.qts + 120),
             "isCompactSummary": True,
             "message": {"content":
                 "A future CC build's totally reworded compact preamble "
                 "that shares no words with the old prefix at all."}},
        ])
        self.assertEqual(self._prune(), [])
        self.assertIn("888001", notify.load_questions(self.qpath))

    def test_prompt_within_grace_window_keeps_it(self):
        # never race the ❓ turn's own machinery — 30s grace
        self._record()
        self._transcript([_user(self.qts + 10, "ano")])
        self.assertEqual(self._prune(), [])

    def test_missing_transcript_keeps_the_entry(self):
        self._record()
        self.assertEqual(self._prune(), [])
        self.assertIn("888001", notify.load_questions(self.qpath))


class TranscriptFoundBySessionId(unittest.TestCase):
    """2026-07-22 montalu: the ❓ hook records the session's CURRENT dir
    (…/odoo/odoo-slovnormal) but CC keys the transcript by the LAUNCH dir
    (…/odoo) — prune looked in the cwd-encoded dir, found no transcript and
    kept 6 already-answered questions alive. The session id is unique across
    the projects tree, so the transcript must be found by SID GLOB when the
    cwd-encoded path misses."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        p = m.patch.object(notify, "_questions_path", lambda: self.qpath)
        p.start()
        self.addCleanup(p.stop)
        self.projects = Path(self.tmp.name) / "projects"
        self.now = time.time()
        self.qts = self.now - 3600

    def test_prunes_when_transcript_lives_under_the_launch_dir(self):
        sub = "/home/m/devel/odoo/odoo-slovnormal"      # recorded by the hook
        launch = "/home/m/devel/odoo"                    # CC transcript key
        notify.record_question("888001", "777001", SID, sub, now=self.qts,
                               path=self.qpath, question="Ticket #7 — ?")
        d = self.projects / wd.encode_project_dir(launch)
        d.mkdir(parents=True, exist_ok=True)
        (d / (SID + ".jsonl")).write_text(
            json.dumps(_user(self.qts + 600, "nejake otazky na mna?")) + "\n")
        logs = wd.prune_answered_questions(self.now,
                                           projects_dir=str(self.projects))
        self.assertTrue(any("pruned" in ln for ln in logs), logs)
        self.assertNotIn("888001", notify.load_questions(self.qpath))


# --------------------------------------------------------------------------- #
# #368 -- an unanswered ❓ is re-asked FRESH AND WHOLE at least once a day,
# never silently dropped. The map no longer age-prunes (see notify's own
# QuestionMap tests); THIS is what turns an old, still-unanswered `ts` into
# a fresh re-post instead of a no-op.
# --------------------------------------------------------------------------- #
class InSleepWindow(unittest.TestCase):
    def _at(self, hh, mm=0):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        return datetime.now(tz).replace(hour=hh, minute=mm, second=0,
                                        microsecond=0).timestamp()

    def test_midnight_through_559_is_asleep(self):
        self.assertTrue(wd._in_sleep_window(self._at(0, 0)))
        self.assertTrue(wd._in_sleep_window(self._at(3, 30)))
        self.assertTrue(wd._in_sleep_window(self._at(5, 59)))

    def test_six_and_later_is_awake(self):
        self.assertFalse(wd._in_sleep_window(self._at(6, 0)))
        self.assertFalse(wd._in_sleep_window(self._at(12, 0)))
        self.assertFalse(wd._in_sleep_window(self._at(23, 59)))


class RepingStaleQuestions(unittest.TestCase):
    BLOCK = ("**Otázka — projekt demo:** ktorú verziu nasadiť?\n"
            "1. najprv 0.28.0\n2. rovno 0.29.0\n\n❓ **rozhodnutie**")

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        self.now = time.time()

    def _record(self, mid, ts, block=None, sid=SID, cwd=CWD, chan="777001"):
        notify.record_question(mid, chan, sid, cwd, now=ts, path=self.qpath,
                               question=block if block is not None else self.BLOCK)

    def _fake_send(self, status="sent", mid="999001"):
        calls = []

        def fn(body, owner=None, dedup_key=None, dry_run=False,
               kind="default", return_message_id=False):
            calls.append({"body": body, "owner": owner,
                          "dedup_key": dedup_key, "kind": kind,
                          "dry_run": dry_run})
            return (status, mid) if return_message_id else status
        return fn, calls

    def _due_ts(self):
        return self.now - wd.QUESTION_REPING_S - 10

    def test_due_question_is_reposted_verbatim_with_the_questions_kind(self):
        self._record("888001", self._due_ts())
        send_fn, calls = self._fake_send()
        logs = wd.reping_stale_questions(self.now, send_fn, path=self.qpath)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["body"], self.BLOCK)      # verbatim, never shortened
        self.assertEqual(calls[0]["kind"], "questions")
        self.assertTrue(any("->" in ln for ln in logs), logs)

    def test_not_yet_due_question_is_left_alone(self):
        self._record("888001", self.now - 60)
        send_fn, calls = self._fake_send()
        wd.reping_stale_questions(self.now, send_fn, path=self.qpath)
        self.assertEqual(calls, [])
        self.assertIn("888001", notify.load_questions(self.qpath))

    def test_sleep_window_defers_without_touching_ts_or_sending(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        night_now = datetime.now(ZoneInfo("Europe/Bratislava")).replace(
            hour=3, minute=0, second=0, microsecond=0).timestamp()
        old_ts = night_now - wd.QUESTION_REPING_S - 10
        self._record("888001", old_ts)
        send_fn, calls = self._fake_send()
        logs = wd.reping_stale_questions(night_now, send_fn, path=self.qpath)
        self.assertEqual(calls, [])
        self.assertTrue(any("deferred sleep-window" in ln for ln in logs), logs)
        self.assertIn("888001", notify.load_questions(self.qpath))

    def _patch_channel(self, value="777001"):
        # reping re-resolves notification_channel(env=None) itself, which
        # reads the LIVE ~/.claude/.env (and tmux, via resolve_owner) -- an
        # unpatched test only passes on a box that HAS a configured Discord
        # questions channel (adversarial review #368: the retrack assertion
        # was green here purely because this dev box is provisioned; on a
        # clean box record_question refuses the empty channel and the test
        # fails). Patch it to a fixed snowflake so the test is hermetic.
        p = m.patch.object(notify, "notification_channel",
                           lambda env=None, owner=None, kind="default": value)
        p.start()
        self.addCleanup(p.stop)

    def test_successful_send_retracks_the_new_message_id_and_drops_the_old(self):
        self._patch_channel()
        self._record("888001", self._due_ts(), sid="sid-x", cwd=CWD)
        send_fn, calls = self._fake_send(status="sent", mid="999001")
        wd.reping_stale_questions(self.now, send_fn, path=self.qpath)
        q = notify.load_questions(self.qpath)
        self.assertNotIn("888001", q)
        self.assertIn("999001", q)
        self.assertEqual(q["999001"]["session"], "sid-x")
        self.assertEqual(q["999001"]["cwd"], CWD)
        self.assertEqual(q["999001"]["ts"], int(self.now))

    def test_sent_without_a_message_id_keeps_the_old_entry(self):
        # Adversarial review (#368): a genuine POST whose response body did
        # not parse to an id (_post_discord returns bare True -> send() says
        # ("sent", None)) must NOT drop the old key -- that re-asked ONCE
        # and then silently un-tracked the question forever, the exact
        # silent-loss failure mode #368 exists to kill. The day-bucketed
        # dedup key caps the same-day retry, so keeping it is spam-safe.
        self._patch_channel()
        old_ts = self._due_ts()
        self._record("888001", old_ts)
        send_fn, calls = self._fake_send(status="sent", mid=None)
        wd.reping_stale_questions(self.now, send_fn, path=self.qpath)
        q = notify.load_questions(self.qpath)
        self.assertIn("888001", q)
        self.assertEqual(q["888001"]["ts"], int(old_ts))

    def test_unresolvable_questions_channel_keeps_the_old_entry(self):
        # Same review finding, the other reachable leg: record_question
        # REFUSES a non-numeric/empty channel, so on a box where the
        # Python-side channel resolution comes up empty the retrack fails
        # -- the old entry must survive for a later retry, never be
        # dropped after a single re-ask.
        self._patch_channel("")
        old_ts = self._due_ts()
        self._record("888001", old_ts)
        send_fn, calls = self._fake_send(status="sent", mid="999001")
        wd.reping_stale_questions(self.now, send_fn, path=self.qpath)
        q = notify.load_questions(self.qpath)
        self.assertIn("888001", q)
        self.assertNotIn("999001", q)
        self.assertEqual(q["888001"]["ts"], int(old_ts))

    def test_failed_send_leaves_the_old_entry_untouched_for_a_retry(self):
        old_ts = self._due_ts()
        self._record("888001", old_ts)
        send_fn, calls = self._fake_send(status="error", mid=None)
        wd.reping_stale_questions(self.now, send_fn, path=self.qpath)
        q = notify.load_questions(self.qpath)
        self.assertIn("888001", q)
        self.assertEqual(q["888001"]["ts"], int(old_ts))

    def test_legacy_entry_without_block_falls_back_to_the_collapsed_question(self):
        d = {"888001": {"session": SID, "cwd": CWD, "channel": "777001",
                        "ts": self._due_ts(), "question": "legacy collapsed text"}}
        Path(self.qpath).write_text(json.dumps(d))
        send_fn, calls = self._fake_send()
        wd.reping_stale_questions(self.now, send_fn, path=self.qpath)
        self.assertEqual(calls[0]["body"], "legacy collapsed text")

    def test_dedup_key_is_bucketed_by_day_not_by_instant(self):
        self._record("888001", self._due_ts())
        send_fn, calls = self._fake_send()
        wd.reping_stale_questions(self.now, send_fn, path=self.qpath)
        self.assertEqual(
            calls[0]["dedup_key"],
            "question-reping:888001:%d" % int(self.now // wd.QUESTION_REPING_S))

    def test_owner_by_sid_is_passed_through_to_the_send(self):
        self._record("888001", self._due_ts(), sid="sid-marek")
        send_fn, calls = self._fake_send()
        wd.reping_stale_questions(self.now, send_fn, path=self.qpath,
                                  owner_by_sid={"sid-marek": "marek"})
        self.assertEqual(calls[0]["owner"], "marek")

    def test_dry_run_never_touches_the_map(self):
        old_ts = self._due_ts()
        self._record("888001", old_ts)
        send_fn, calls = self._fake_send()
        wd.reping_stale_questions(self.now, send_fn, path=self.qpath,
                                  dry_run=True)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["dry_run"])
        q = notify.load_questions(self.qpath)
        self.assertIn("888001", q)
        self.assertEqual(q["888001"]["ts"], int(old_ts))


if __name__ == "__main__":
    unittest.main()
