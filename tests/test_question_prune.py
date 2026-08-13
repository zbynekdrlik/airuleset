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
            # #366 -- a "question-timeout:"-prefixed nudge is machine-typed,
            # not a human answer. #403 deleted `GOAL_QUESTION_PARK_TEXT`
            # (and the whole `_goal_question_park_nudge` mechanism that used
            # to produce it) wholesale -- nothing in the new callback-model
            # `watchdog/goal.py` ever types this text into a pane any more.
            # The literal "question-timeout:" prefix stays a real,
            # deliberately-kept entry in `_MACHINE_PROMPT_PREFIXES` (it
            # costs nothing to leave and is harmless if some future/manual
            # source ever produces it again), so this fixture is now a
            # hand-typed literal — a historically faithful reconstruction
            # of the deleted constant's own text, not a live reference.
            _user(self.qts + 750,
                 "question-timeout: 30 min bez odpovede na poslednu "
                 "otazku. Zaparkuj TENTO tiket a pokracuj na iny."),
        ])
        self.assertEqual(self._prune(), [])
        self.assertIn("888001", notify.load_questions(self.qpath))

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
        # Pinned to LOCAL NOON of the current day, never bare time.time():
        # reping_stale_questions defers inside the 00:00-05:59 local sleep
        # window by design, so a wall-clock `now` made this whole class
        # time-of-day dependent -- green all day, 6 failures in the first
        # after-midnight gate run (found live 2026-08-13 00:04 CEST).
        # Noon is deterministically outside the window in every timezone
        # offset this fleet runs. The sleep-window branch itself is
        # covered by its own explicit night-timestamp test.
        _lt = time.localtime()
        self.now = time.mktime(
            (_lt.tm_year, _lt.tm_mon, _lt.tm_mday, 12, 0, 0, 0, 0, -1))

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


class RecordQuestionSupersede(unittest.TestCase):
    """Ghost questions (#407) — a NEW tracked ask supersedes the session's
    previous ask on the SAME channel at the map's single writer, so a
    reworded ❓ past the edit window can never leave an immortal duplicate
    behind. Channel-scoped: a mirror fan-out records one entry per target
    THREAD (same session, different channels) — siblings of one generation,
    never ghosts."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")

    def _load(self):
        return notify.load_questions(self.qpath)

    def test_reword_past_the_edit_window_supersedes_the_old_entry(self):
        # The full ghost-birth sequence from the ticket: ask -> reword past
        # the 15-min window (update_question refuses purely on AGE — the
        # fake http proves Discord was never even consulted) -> the hook
        # falls through to a fresh POST -> record_question with a NEW id.
        # The OLD entry must be superseded, never tracked alongside.
        t0 = 1_700_000_000
        t1 = t0 + notify._EDIT_WINDOW_S + 60
        notify.record_question("888001", "777001", SID, CWD, now=t0,
                               path=self.qpath, question="verzia 1?")
        calls = []

        def fake_http(token, method, url, payload=None):
            calls.append(method)
            return {"content": "❓ x"}

        edited = notify.update_question(SID, "verzia 2?",
                                        env={"DISCORD_BOT_TOKEN": "t"},
                                        now=t1, path=self.qpath,
                                        http=fake_http)
        self.assertFalse(edited)
        self.assertEqual(calls, [])            # refused on age alone
        notify.record_question("888002", "777001", SID, CWD, now=t1,
                               path=self.qpath, question="verzia 2?")
        self.assertEqual(sorted(self._load()), ["888002"])

    def test_mirror_pair_on_different_channels_both_survive(self):
        notify.record_question("888001", "777001", SID, CWD, now=1000,
                               path=self.qpath, question="q?")
        notify.record_question("888002", "777002", SID, CWD, now=1001,
                               path=self.qpath, question="q?")
        self.assertEqual(sorted(self._load()), ["888001", "888002"])

    def test_distinct_sessions_on_the_same_channel_both_survive(self):
        notify.record_question("888001", "777001", SID, CWD, now=1000,
                               path=self.qpath, question="a?")
        notify.record_question("888002", "777001", "other-sid", CWD, now=1001,
                               path=self.qpath, question="b?")
        self.assertEqual(sorted(self._load()), ["888001", "888002"])


class PruneCollapsesSupersededDuplicates(unittest.TestCase):
    """Ghost questions (#407) — PRE-EXISTING duplicate pairs (born before the
    record-time supersede shipped) are reaped by the EXISTING sweep: per
    (session, channel) only the NEWEST entry survives, with zero extra
    pings. Runs before reping in run_once, so a ghost dies un-pinged."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        p = m.patch.object(notify, "_questions_path", lambda: self.qpath)
        p.start()
        self.addCleanup(p.stop)
        self.projects = Path(self.tmp.name) / "projects"
        self.now = time.time()

    def _entry(self, ts, sid=SID, chan="777001", cwd=CWD, **kw):
        e = {"session": sid, "cwd": cwd, "channel": chan, "ts": ts,
             "question": "q?", "block": "q?"}
        e.update(kw)
        return e

    def _seed(self, entries):
        Path(self.qpath).write_text(json.dumps(entries))

    def _prune(self, dry_run=False):
        return wd.prune_answered_questions(self.now,
                                           projects_dir=str(self.projects),
                                           dry_run=dry_run)

    def test_ghost_pair_collapses_to_the_newest_without_any_transcript(self):
        # No transcript exists at all (a goal loop that never got a human
        # prompt) — the collapse must still reap the superseded entry.
        self._seed({"888001": self._entry(1000), "888002": self._entry(1960)})
        logs = self._prune()
        self.assertEqual(sorted(notify.load_questions(self.qpath)),
                         ["888002"])
        self.assertTrue(any("superseded" in ln for ln in logs), logs)

    def test_mirror_pair_is_never_collapsed(self):
        self._seed({"888001": self._entry(1000, chan="777001"),
                    "888002": self._entry(1001, chan="777002")})
        self._prune()
        self.assertEqual(sorted(notify.load_questions(self.qpath)),
                         ["888001", "888002"])

    def test_distinct_sessions_are_never_collapsed(self):
        self._seed({"888001": self._entry(1000),
                    "888002": self._entry(1001, sid="other-sid")})
        self._prune()
        self.assertEqual(sorted(notify.load_questions(self.qpath)),
                         ["888001", "888002"])

    def test_a_single_live_question_is_untouched(self):
        self._seed({"888001": self._entry(1000)})
        logs = self._prune()
        self.assertEqual(sorted(notify.load_questions(self.qpath)),
                         ["888001"])
        self.assertFalse(any("superseded" in ln for ln in logs), logs)

    def test_ts_tie_breaks_on_the_larger_message_id(self):
        # Discord snowflakes are time-ordered: on a ts tie the larger id IS
        # the later posting — deterministic, never arbitrary dict order.
        self._seed({"888002": self._entry(1000), "888001": self._entry(1000)})
        self._prune()
        self.assertEqual(sorted(notify.load_questions(self.qpath)),
                         ["888002"])

    def test_dry_run_logs_but_leaves_the_map_untouched(self):
        self._seed({"888001": self._entry(1000), "888002": self._entry(1960)})
        logs = self._prune(dry_run=True)
        self.assertEqual(sorted(notify.load_questions(self.qpath)),
                         ["888001", "888002"])
        self.assertTrue(any("superseded" in ln for ln in logs), logs)

    def test_malformed_and_bool_ts_entries_never_break_the_collapse(self):
        # A non-dict legacy value is skipped without crashing; a legacy
        # bool ts (the isinstance(True, int) trap) reads as oldest and is
        # collapsed away safely rather than raising.
        self._seed({"888000": "garbage",
                    "888001": self._entry(True),
                    "888002": self._entry(2000)})
        self._prune()
        q = notify.load_questions(self.qpath)
        self.assertIn("888002", q)
        self.assertNotIn("888001", q)


class GhostPairRepingsOnceAfterCollapse(unittest.TestCase):
    """Ghost questions (#407), integration — run_once orders prune BEFORE
    reping, so a ghost pair produces exactly ONE daily re-ask (the newest
    wording), not 2+ pings/day for one logical question forever."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        p = m.patch.object(notify, "_questions_path", lambda: self.qpath)
        p.start()
        self.addCleanup(p.stop)
        self.projects = Path(self.tmp.name) / "projects"
        # Same local-noon pin as RepingStaleQuestions (sleep-window safety).
        _lt = time.localtime()
        self.now = time.mktime(
            (_lt.tm_year, _lt.tm_mon, _lt.tm_mday, 12, 0, 0, 0, 0, -1))

    def test_prune_then_reping_sends_only_the_newest_block(self):
        due = self.now - wd.QUESTION_REPING_S - 10
        ghost = {"session": SID, "cwd": CWD, "channel": "777001",
                 "ts": due - 960, "question": "stara formulacia?",
                 "block": "stara formulacia?"}
        real = {"session": SID, "cwd": CWD, "channel": "777001",
                "ts": due, "question": "nova formulacia?",
                "block": "nova formulacia?"}
        Path(self.qpath).write_text(json.dumps({"888001": ghost,
                                                "888002": real}))
        wd.prune_answered_questions(self.now,
                                    projects_dir=str(self.projects))
        sent = []

        def send_fn(body, owner=None, dedup_key=None, dry_run=False,
                    kind="default", return_message_id=False):
            sent.append(body)
            return ("dedup", None) if return_message_id else "dedup"

        wd.reping_stale_questions(self.now, send_fn, path=self.qpath)
        self.assertEqual(sent, ["nova formulacia?"])


if __name__ == "__main__":
    unittest.main()
