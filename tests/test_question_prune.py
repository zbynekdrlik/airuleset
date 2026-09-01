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

import inspect
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
# #368's daily re-ask is RETIRED (#795, owner ruling 2026-09-01): a ❓ is
# asked ONCE, the footer `U N` holds it, the owner invokes processing
# himself (#606) -- the watchdog never automatically re-asks again.
# --------------------------------------------------------------------------- #
class NoSleepWindowGate(unittest.TestCase):
    # #791: the 00:00-05:59 `_in_sleep_window` helper was DELETED — there is
    # no night/day difference, so the watchdog carries no night-hour gate at
    # all. Guard against a re-introduction.
    def test_in_sleep_window_helper_is_gone(self):
        self.assertFalse(hasattr(wd, "_in_sleep_window"),
                         "the night-hour gate must stay removed (#791)")


class RepingStaleQuestionsIsRetired(unittest.TestCase):
    """#795: `reping_stale_questions` is a PERMANENT NO-OP tombstone — the
    daily question re-ask (#368) is abolished. Every test that used to live
    here (due-question repost, retrack/drop semantics, bucketed dedup,
    owner_by_sid passthrough, dry-run) locked behavior of the RETIRED
    mechanism and was removed with it (the #707 pattern applied to its
    sibling `reping_owner_decision_tickets`). The tombstone must reach NO
    seam at all, even against a map that would have been genuinely due."""

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

    def test_never_sends_touches_the_map_or_returns_anything_but_empty(self):
        old_ts = self.now - 30 * 24 * 3600     # unambiguously "due" under #368
        self._record("888001", old_ts)
        calls = []

        def send_fn(body, owner=None, dedup_key=None, dry_run=False,
                    kind="default", return_message_id=False):
            calls.append(1)
            return ("sent", "mid")

        out = wd.reping_stale_questions(self.now, send_fn, path=self.qpath)
        self.assertEqual(out, [])
        self.assertEqual(calls, [], "the retired daily re-ask must NEVER send")
        q = notify.load_questions(self.qpath)
        self.assertIn("888001", q)
        self.assertEqual(q["888001"]["ts"], int(old_ts),
                         "the retired daily re-ask must NEVER touch the map")

    def test_tolerates_any_stale_call_shape(self):
        # A tombstone kept for stale callers must survive EVERY call shape a
        # pre-#795 caller could use — including a `send_fn=None` (unwired)
        # sweep and none at all.
        self.assertEqual(wd.reping_stale_questions(), [])
        self.assertEqual(wd.reping_stale_questions(0, None, path=None), [])
        self.assertEqual(
            wd.reping_stale_questions(
                self.now, lambda *a, **k: "sent", dry_run=True,
                path=self.qpath, owner_by_sid={}, owner_by_cwd={},
                owners_seen=set(), account_owner="x", reping=1),
            [])

    def test_docstring_names_the_retirement(self):
        doc = wd.reping_stale_questions.__doc__ or ""
        self.assertIn("#795", doc)
        self.assertIn("no-op", doc.lower())


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


# (#795: `GhostPairRepingsOnceAfterCollapse` was REMOVED with the daily
# re-ask it integration-tested — "run_once orders prune BEFORE reping, so a
# ghost pair produces exactly ONE daily re-ask" has no meaning once
# `reping_stale_questions` is a permanent no-op and run_once never calls it
# at all. `PruneCollapsesSupersededDuplicates` above still locks the
# collapse itself, which prune performs for its own #407 reasons.)


class SupersedeIsAskGenerationGuarded(unittest.TestCase):
    """Adversarial-review findings on the ghost fix (#407) — "newest ask
    wins" compares ASK GENERATION (the `asked` field, preserved across
    `record_question`'s own retracks), never the record time — a re-tracked
    entry's fresh record ts must never invert the ask order against a LIVE,
    newer question the same session tracks on the target channel. (#795: the
    ORIGINAL retrack producer of this scenario, the daily
    `reping_stale_questions` re-ask, is retired — but `record_question`'s
    `asked_ts` param and the generation-guarded collapse it feeds stay live,
    since `record_question`/`prune_answered_questions` are general
    map-writer/collapse primitives, not reping-specific; the two tests below
    exercise them directly instead of via a retired retrack.)"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        p = m.patch.object(notify, "_questions_path", lambda: self.qpath)
        p.start()
        self.addCleanup(p.stop)
        self.projects = Path(self.tmp.name) / "projects"
        _lt = time.localtime()
        self.now = time.mktime(
            (_lt.tm_year, _lt.tm_mon, _lt.tm_mday, 12, 0, 0, 0, 0, -1))

    def test_collapse_prefers_the_newer_ask_generation_over_record_time(self):
        # The shared channel holds the live ask (older record ts, newer
        # GENERATION) and a re-posted old ask (fresh record ts, older
        # generation — the shape a retrack used to produce). The collapse
        # must keep the newer GENERATION — the live question.
        live = {"session": SID, "cwd": CWD, "channel": "777001",
                "ts": self.now - 3600, "question": "ziva otazka?",
                "block": "ziva otazka?"}
        repost = {"session": SID, "cwd": CWD, "channel": "777001",
                  "ts": self.now, "asked": int(self.now - 90000),
                  "question": "stara otazka?", "block": "stara otazka?"}
        Path(self.qpath).write_text(json.dumps({"888100": live,
                                                "999001": repost}))
        wd.prune_answered_questions(self.now,
                                    projects_dir=str(self.projects))
        self.assertEqual(sorted(notify.load_questions(self.qpath)),
                         ["888100"])

    def test_record_supersede_is_generation_guarded(self):
        # Recording with an OLD asked_ts (a re-track) must not supersede a
        # newer-generation entry; a genuinely NEW ask (no asked_ts) still
        # supersedes everything older on its channel.
        t0, t1 = 1_700_000_000, 1_700_050_000
        notify.record_question("888100", "777001", SID, CWD, now=t1,
                               path=self.qpath, question="ziva?")
        notify.record_question("999001", "777001", SID, CWD, now=t1 + 40000,
                               path=self.qpath, question="stara?",
                               asked_ts=t0)
        self.assertEqual(sorted(notify.load_questions(self.qpath)),
                         ["888100", "999001"])
        notify.record_question("999002", "777001", SID, CWD, now=t1 + 50000,
                               path=self.qpath, question="nova?")
        self.assertEqual(sorted(notify.load_questions(self.qpath)),
                         ["999002"])


class RunOnceNeverCallsTheRetiredReping(unittest.TestCase):
    """#795: run_once's registry entry for the daily re-ask is GONE outright
    (the #707 pattern — a retired job's `_add(...)` call is removed, not
    left calling a now-inert tombstone). `prune_answered_questions` — the
    #407 ghost-pair collapse this class used to test an ORDERING against —
    stays registered and fully live; there is simply nothing after it to
    order against any more."""

    def test_run_once_source_never_invokes_reping_stale_questions(self):
        src = inspect.getsource(wd.run_once)
        self.assertNotIn("reping_stale_questions(", src,
                         "run_once must never call the retired daily re-ask")
        self.assertIn("prune_answered_questions(", src)

    def test_run_once_has_no_questions_path_param(self):
        params = inspect.signature(wd.run_once).parameters
        self.assertNotIn("questions_path", params,
                         "the retired re-ask's sole consumer param must be gone")


if __name__ == "__main__":
    unittest.main()
