"""#449: a Discord reply to a pruned/superseded ❓ must NEVER be lost silently.

Live incident (david@subdev, 2026-08-13): David answered a ❓ on his phone
while ALSO typing unrelated prompts at the terminal. prune_answered_questions
treats ANY later human prompt in the asking session as "answered at the
terminal" and hard-deleted the map entries; job 7 (deliver_discord_replies)
then had nothing to match — with an empty map it returns before even fetching
Discord — so the phone answer vanished with ZERO journal lines.

These tests lock the #449 fix, as SCOPED by #652:
  (1) a pruned entry moves to a GRACE store and a reply still routes
      NORMALLY (typed into the asking session, with the original question
      wording) for QUESTION_GRACE_S;
  (2) after grace expiry, a reply to a card THIS box posted (in dq_posted)
      but can no longer route — the genuine never-silent floor — must
      journal it AND ping the owner. #652 SCOPES this: a reply to a SIBLING
      box's card in the shared `-q` thread, and any plain non-reply message,
      trigger NOTHING (the pre-#652 "not-a-reply"/untracked-anyone firing is
      retired — it spammed the shared thread);
  (3) the fix composes with #407: a superseded ask stays routable while its
      Discord card is still answerable, and a graced entry can never re-ping
      (reping reads the MAIN map only) or re-inflate the statusline Q badge
      (statusbar reads discord-questions.json only — grace is a separate
      file).

Hermetic under BOTH runners: every store path is sandboxed by patching
notify._questions_path into a TemporaryDirectory — the grace path is DERIVED
from that dirname (the #437 isolation shape), notify.send and wd._react_ok
are patched, the Discord fetch is injected. Nothing touches the real
~/.claude.
"""

import json
import os
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

OWNER = "773451844110385193"
SID = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"
CWD = "/home/x/devel/demo"
IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
_DISCORD_EPOCH_MS = 1420070400000


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def _user(epoch, content):
    return {"type": "user", "timestamp": _iso(epoch),
            "message": {"role": "user", "content": content}}


def _snow(epoch):
    """A realistic Discord snowflake id encoding `epoch` (seconds)."""
    return str(int(epoch * 1000 - _DISCORD_EPOCH_MS) << 22)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.qpath = str(base / "discord-questions.json")
        self.cpath = str(base / "discord-cards.json")
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": OWNER,
                    "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "777001"}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_cards_path", lambda: self.cpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        r = m.patch.object(wd, "_react_ok", return_value=True)
        r.start()
        self.addCleanup(r.stop)
        self.pings = []

        def _fake_send(body, env=None, owner=None, dedup_key=None,
                       dry_run=False, return_message_id=False,
                       kind="default", project=None):
            self.pings.append({"body": body, "dedup_key": dedup_key,
                               "dry_run": dry_run, "kind": kind})
            return "dry-run" if dry_run else "sent"

        s = m.patch.object(notify, "send", _fake_send)
        s.start()
        self.addCleanup(s.stop)
        self.sent = []
        self.projects = base / "projects"
        self.now = time.time()

    def _run(self, argv, timeout=8):
        self.sent.append(argv)
        j = " ".join(argv)
        if "pane_in_mode" in j:
            return "0"
        if "capture-pane" in j:
            return IDLE
        return ""

    def _transcript(self, entries):
        d = self.projects / wd.encode_project_dir(CWD)
        d.mkdir(parents=True, exist_ok=True)
        (d / (SID + ".jsonl")).write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n")

    def _record(self, mid="888001", ch="777001", now=None,
                q="Ticket #9 — ako?"):
        notify.record_question(mid, ch, SID, CWD,
                               now=self.now - 3600 if now is None else now,
                               path=self.qpath, question=q)

    def _reply(self, rid=None, ref="888001", author=OWNER,
               content="odpoved 2", at=None):
        return {"id": rid or _snow(at or self.now),
                "author": {"id": author},
                "message_reference": {"message_id": ref},
                "content": content}

    def _fetch(self, msgs):
        return lambda ch, token: [x for x in msgs
                                  if x.get("_channel", "777001") == ch]

    def _prune(self, now=None, dry_run=False):
        return wd.prune_answered_questions(
            self.now if now is None else now,
            projects_dir=str(self.projects), dry_run=dry_run)


class ReplyAfterPruneStillRoutes(_Base):
    def test_reply_after_prune_is_delivered_not_lost(self):
        # THE INCIDENT: question asked, David types an UNRELATED prompt at
        # the terminal, prune removes the entry, THEN the phone reply
        # arrives. It must still be typed into the asking session.
        self._record()
        self._transcript([_user(self.now - 3000,
                                "potrebujeme znova opravit dochadzku")])
        self._prune()
        # the badge stays trustworthy: the entry LEAVES the main map
        self.assertNotIn("888001", notify.load_questions(self.qpath))
        state = {}
        logs = wd.deliver_discord_replies(
            self.now, self._run, state, {SID: ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([self._reply()]))
        self.assertTrue(any("reply→" in ln for ln in logs),
                        "a reply within the grace window must route "
                        "normally, got %r" % logs)
        literal = [a for a in self.sent if "-l" in a]
        self.assertTrue(any("odpoved 2" in a[-1] for a in literal),
                        "the answer text must be typed into the pane: %r"
                        % self.sent)

    def test_prune_moves_entry_to_grace_not_deletion(self):
        self._record()
        self._transcript([_user(self.now - 3000, "ine veci")])
        logs = self._prune()
        self.assertTrue(any("pruned" in ln for ln in logs), logs)
        self.assertNotIn("888001", notify.load_questions(self.qpath))
        self.assertIn("888001", notify.load_grace_questions(),
                      "a pruned entry must stay routable in the grace store")

    def test_dry_run_prune_touches_neither_store(self):
        self._record()
        self._transcript([_user(self.now - 3000, "ine veci")])
        self._prune(dry_run=True)
        self.assertIn("888001", notify.load_questions(self.qpath))
        self.assertEqual(notify.load_grace_questions(), {})


class SupersededAskStaysRoutable(_Base):
    def test_reply_to_superseded_ask_still_delivers(self):
        # #407's write-time supersede DELETED the old entry while its
        # Discord card stays visible and answerable-looking (documented
        # residual MAJOR-2) — with #449 it moves to grace instead, so an
        # answer on the OLD card still routes, carrying the OLD question's
        # own wording (the numbered options the user actually saw).
        self._record("888001", now=self.now - 7200, q="stará otázka")
        self._record("888002", now=self.now - 60, q="nová otázka")
        self.assertNotIn("888001", notify.load_questions(self.qpath))
        self.assertIn("888002", notify.load_questions(self.qpath))
        logs = wd.deliver_discord_replies(
            self.now, self._run, {}, {SID: ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([self._reply(ref="888001")]))
        self.assertTrue(any("reply→" in ln for ln in logs), logs)
        literal = [a for a in self.sent if "-l" in a]
        self.assertTrue(any("stará otázka" in a[-1] for a in literal),
                        "the delivered prompt must carry the OLD ask's own "
                        "wording: %r" % self.sent)

    def test_graced_entry_never_reinflates_badge_or_reping(self):
        # The statusline footer `U N` badge (statusbar.py) reads ONLY
        # discord-questions.json — a graced entry must live in a SEPARATE
        # file, or #407's ghost and the 2026-07-22 stale-badge problem both
        # come back. (Pre-#795 the daily re-ask, `reping_stale_questions`,
        # shared this same read-only-the-main-map concern; it is now a
        # permanent no-op tombstone and reads nothing at all.)
        self._record()
        self._transcript([_user(self.now - 3000, "ine veci")])
        self._prune()
        self.assertEqual(notify.load_questions(self.qpath), {},
                         "the MAIN map must be empty after the prune — it "
                         "is what the badge and the re-ask count")
        self.assertIn("888001", notify.load_grace_questions())


class NeverSilentFloor(_Base):
    def test_reply_after_grace_expiry_pings_instead_of_silence(self):
        # Even past QUESTION_GRACE_S the reply must never be SILENTLY
        # dropped: the channel is remembered, the unmatched answer produces
        # a journal line + an owner ping.
        self._record()
        self._transcript([_user(self.now - 3000, "ine veci")])
        state = {}
        # sweep 1: question still live → job 7 learns the channel
        wd.deliver_discord_replies(self.now, self._run, state, {},
                                   dry_run=False,
                                   discord_fetch=self._fetch([]))
        self._prune()
        later = self.now + 26 * 3600          # grace over, memory alive
        logs = wd.deliver_discord_replies(
            later, self._run, state, {SID: ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([self._reply(at=later)]))
        self.assertFalse(any("reply→" in ln for ln in logs), logs)
        self.assertTrue(any("orphan" in ln for ln in logs),
                        "an unroutable answer must leave a journal line, "
                        "got %r" % logs)
        self.assertTrue(self.pings,
                        "the owner must be pinged that the answer could "
                        "not be routed")
        self.assertEqual(notify.load_grace_questions(), {},
                         "the expired grace entry must be dropped")

    def test_plain_message_in_questions_thread_triggers_nothing(self):
        # #652 REVERSES #449's "never-silent for a non-reply": in a SHARED
        # -q thread the owner and the stream deliberately CHAT, and a plain
        # (non-reply) message must produce NO bot reaction at all ("my si
        # tam chceme aj pisat ked sa nieco diskutuje"). Only an explicit
        # reply to OUR OWN ❓ card may ever fire the floor.
        self._record()
        msg = {"id": _snow(self.now), "author": {"id": OWNER},
               "content": "mozes pouzit moznost 2"}
        logs = wd.deliver_discord_replies(
            self.now, self._run, {}, {SID: ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([msg]))
        self.assertFalse(any("orphan" in ln for ln in logs), logs)
        self.assertEqual(self.pings, [],
                         "a free non-reply message must trigger nothing: %r"
                         % self.pings)

    def test_orphan_ping_dedups_per_message(self):
        # #652: the at-most-once guarantee, now exercised with a reply to
        # OUR OWN dropped card (the only case that fires post-#652).
        state = {"dq_posted": {"888009": self.now},
                 "dreply_channels": {"777001": {"ts": self.now, "q": True}}}
        msg = self._reply(ref="888009", content="odpoved 2")
        wd.deliver_discord_replies(
            self.now, self._run, state, {SID: ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([msg]))
        wd.deliver_discord_replies(
            self.now + 60, self._run, state, {SID: ("%1", IDLE)},
            dry_run=False, discord_fetch=self._fetch([msg]))
        self.assertEqual(len(self.pings), 1,
                         "one unroutable reply to OUR card = ONE ping, ever: "
                         "%r" % self.pings)

    def test_dry_run_never_marks_orphan_state(self):
        # #304 discipline: a --dry-run sweep must not poison the real next
        # sweep's dedup state. Exercised with a reply to OUR OWN dropped card
        # (the case that fires post-#652).
        state = {"dq_posted": {"888009": self.now},
                 "dreply_channels": {"777001": {"ts": self.now, "q": True}}}
        msg = self._reply(ref="888009", content="odpoved 2")
        wd.deliver_discord_replies(
            self.now, self._run, state, {SID: ("%1", IDLE)}, dry_run=True,
            discord_fetch=self._fetch([msg]))
        self.assertNotIn("dorphan_done", state)

    def test_foreign_human_chatter_is_not_pinged_about(self):
        # a reply to another HUMAN's message is conversation, not a lost
        # answer — the floor must stay quiet on it.
        self._record()
        msg = {"id": _snow(self.now), "author": {"id": OWNER},
               "message_reference": {"message_id": "555000"},
               "referenced_message": {"author": {"id": "999", "bot": False}},
               "content": "hej, suhlasim"}
        logs = wd.deliver_discord_replies(
            self.now, self._run, {}, {SID: ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([msg]))
        self.assertFalse(any("orphan" in ln for ln in logs), logs)
        self.assertEqual(self.pings, [])


class SharedThreadOrphanScoping652(_Base):
    """#652: N stream boxes (david1, david2, codex-bridge, …) share ONE
    `-q` thread and each runs its own job 7. A reply to a card owned by a
    SIBLING box is "untracked by me" but is NOT a lost answer — it must
    trigger NO reaction here, so the ONLY box that ever reacts is the one
    that posted the card (fleet-wide at-most-once by construction). Live
    incident: David's «Možnosť 1» reply routed fine (✅) yet the non-owning
    boxes each spammed `⚠️ nedá sa priradiť`."""

    def _sibling_reply(self, ref="999999", content="Možnosť 1"):
        return {"id": _snow(self.now), "author": {"id": OWNER},
                "message_reference": {"message_id": ref},
                "content": content}

    def test_reply_to_a_sibling_boxs_card_triggers_nothing(self):
        # our box tracks 888001 (so the channel is fetched); the reply
        # targets 999999 = a card THIS box never posted (a sibling's).
        self._record()                       # 888001 -> qmap, channel 777001
        state = {}
        logs = wd.deliver_discord_replies(
            self.now, self._run, state, {SID: ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([self._sibling_reply()]))
        self.assertFalse(any("orphan" in ln for ln in logs),
                         "a reply to a SIBLING box's card must not orphan: %r"
                         % logs)
        self.assertEqual(self.pings, [],
                         "a reply to a SIBLING box's card must not ping: %r"
                         % self.pings)

    def test_reply_to_our_own_dropped_card_still_pings(self):
        # the genuine #449 case is PRESERVED: a card THIS box posted (in
        # dq_posted) but that has since dropped past grace still journals +
        # pings when the late answer finally arrives.
        state = {"dq_posted": {"888009": self.now},
                 "dreply_channels": {"777001": {"ts": self.now, "q": True}}}
        logs = wd.deliver_discord_replies(
            self.now, self._run, state, {SID: ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([self._sibling_reply(ref="888009")]))
        self.assertTrue(any("orphan" in ln for ln in logs), logs)
        self.assertTrue(self.pings,
                        "a reply to OUR OWN dropped card must still ping")

    def test_posted_ids_are_remembered_across_sweeps(self):
        # a card this box currently tracks is folded into state["dq_posted"]
        # so it stays recognizable as ours after it later drops.
        self._record()                       # 888001 -> qmap
        state = {}
        wd.deliver_discord_replies(
            self.now, self._run, state, {}, dry_run=False,
            discord_fetch=self._fetch([]))
        self.assertIn("888001", state.get("dq_posted", {}),
                      "a tracked card id must be remembered: %r"
                      % state.get("dq_posted"))

    def test_dry_run_never_persists_posted_ids(self):
        # #304 discipline: a --dry-run sweep must not write dq_posted.
        self._record()
        state = {}
        wd.deliver_discord_replies(
            self.now, self._run, state, {}, dry_run=True,
            discord_fetch=self._fetch([]))
        self.assertNotIn("dq_posted", state)


class PostedMemoryRetention652(_Base):
    """#652 review: `_refresh_posted_memory` must run its retention window
    from the card's LAST tracked sighting (= past grace-end), NOT frozen at
    first sight — a `setdefault` anchor collapsed the #449 late-answer window
    toward ~0h for a question graced late in its life (a real silent-loss
    regression the reviewers reproduced)."""

    HOUR = 3600

    def test_a_tracked_card_ts_is_refreshed_not_frozen(self):
        # THE 🔴 lock: an id folded 47h ago that is STILL tracked (in qmap)
        # must have its ts re-stamped to `now`, so it stays remembered for a
        # further full window past grace — never evicted at 48h-since-first.
        old = self.now - 47 * self.HOUR
        out = wd._refresh_posted_memory({"888001": old}, {"888001": {}}, self.now)
        self.assertEqual(out["888001"], self.now,
                         "a still-tracked card's memory ts must refresh to "
                         "now (not stay frozen at first fold): %r" % out)

    def test_an_untracked_expired_entry_is_pruned(self):
        # past the retention window AND no longer tracked -> dropped.
        out = wd._refresh_posted_memory(
            {"888009": self.now - 49 * self.HOUR}, {}, self.now)
        self.assertNotIn("888009", out)

    def test_an_untracked_in_window_entry_is_kept(self):
        out = wd._refresh_posted_memory(
            {"888009": self.now - 10 * self.HOUR}, {}, self.now)
        self.assertIn("888009", out)

    def test_a_future_ts_entry_is_kept_never_silent(self):
        # never-silent prefers remembering a genuine card id; the prune has no
        # `0 <= now - v` guard on purpose.
        out = wd._refresh_posted_memory(
            {"888009": self.now + 1000}, {}, self.now)
        self.assertIn("888009", out)

    def test_non_dict_state_is_tolerated(self):
        # a corrupt state value (list/None) must not crash a watchdog sweep.
        self.assertEqual(
            wd._refresh_posted_memory(["garbage"], {}, self.now), {})
        self.assertEqual(
            wd._refresh_posted_memory(None, {"x": {}}, self.now), {"x": self.now})

    def test_reply_to_our_card_after_retention_window_is_silent(self):
        # E2E boundary: a reply to OUR OWN card whose memory has aged out
        # (>window since last tracked) fires NOTHING — the deliberate
        # safe-silence residual #652 accepts.
        state = {"dq_posted": {"888009": self.now - 49 * self.HOUR},
                 "dreply_channels": {"777001": {"ts": self.now, "q": True}}}
        msg = {"id": _snow(self.now), "author": {"id": OWNER},
               "message_reference": {"message_id": "888009"},
               "content": "Možnosť 1"}
        logs = wd.deliver_discord_replies(
            self.now, self._run, state, {SID: ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([msg]))
        self.assertFalse(any("orphan" in ln for ln in logs), logs)
        self.assertEqual(self.pings, [])


class ExplicitPathSandboxesGrace(unittest.TestCase):
    """#449-review F1: callers passing an EXPLICIT `path=` (the pre-existing
    test population's own sandboxing convention) never consult the patched
    _questions_path — the grace write must land BESIDE that explicit path,
    never in the real ~/.claude (live-caught: the supersede fixture from
    test_question_prune.py materialised in the box's actual home store on
    every suite run)."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / ".claude").mkdir(parents=True)
        p = m.patch.dict(os.environ, {"HOME": str(self.home)})
        p.start()
        self.addCleanup(p.stop)
        self.store = Path(self.tmp.name) / "store"
        self.store.mkdir()
        self.qpath = str(self.store / "q.json")

    def test_supersede_with_explicit_path_graces_beside_it(self):
        notify.record_question("111000", "900100", "sid-x", "/p",
                               now=1000, path=self.qpath)
        notify.record_question("222000", "900100", "sid-x", "/p",
                               now=2000, path=self.qpath)
        beside = self.store / "discord-questions-grace.json"
        self.assertTrue(beside.is_file(),
                        "the graced supersedee must land beside the "
                        "explicit map path")
        self.assertIn("111000", json.loads(beside.read_text()))
        self.assertFalse(
            (self.home / ".claude" / "discord-questions-grace.json").exists(),
            "the REAL home store must never be touched")

    def test_grace_question_with_explicit_path_graces_beside_it(self):
        notify.record_question("333000", "900100", "sid-x", "/p",
                               now=1000, path=self.qpath)
        self.assertTrue(notify.grace_question("333000", path=self.qpath))
        beside = self.store / "discord-questions-grace.json"
        self.assertIn("333000", json.loads(beside.read_text()))
        self.assertFalse(
            (self.home / ".claude" / "discord-questions-grace.json").exists())


class GraceWriteFailureKeepsMain(unittest.TestCase):
    """#449-review F3/M1 teeth: 'a grace write failure keeps the main
    entry' is the fix's own headline safety sentence — force the failure
    and prove the ordering (grace-put FIRST, only then drop from main)."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        p = m.patch.object(notify, "_questions_path", lambda: self.qpath)
        p.start()
        self.addCleanup(p.stop)

    def test_failed_grace_write_never_drops_the_main_entry(self):
        notify.record_question("444000", "900100", "sid-x", "/p",
                               now=1000, path=self.qpath)
        with m.patch.object(notify, "_save_grace_questions",
                            return_value=False):
            self.assertFalse(notify.grace_question("444000", path=self.qpath))
        self.assertIn("444000", notify.load_questions(self.qpath),
                      "losing the answer is the one failure the store "
                      "exists to prevent — a failed grace write must keep "
                      "the entry routable in the MAIN map")


class OrphanPingConfirmation(_Base):
    """#449-review F2/M4: dorphan_done may be marked only on a CONFIRMED
    delivery — 'sent', or 'dedup' whose marker genuinely recorded a
    delivery (notify.marker_delivered). A bare claim ('dedup' after a
    failed POST) must not suppress the ping forever; the stale claim is
    released so the next sweep re-POSTs."""

    def _msg(self):
        # #652: the floor now fires ONLY for a reply to OUR OWN card, so the
        # confirmation/dedup teeth are exercised with a reply to a card THIS
        # box posted (888009 in dq_posted) that has since dropped.
        return {"id": _snow(self.now), "author": {"id": OWNER},
                "message_reference": {"message_id": "888009"},
                "content": "mozes pouzit moznost 2"}

    def _state(self):
        return {"dq_posted": {"888009": self.now}}

    def test_error_send_is_retried_and_never_marked(self):
        self._record()
        state = self._state()
        with m.patch.object(notify, "send",
                            lambda *a, **k: "error"):
            wd.deliver_discord_replies(
                self.now, self._run, state, {SID: ("%1", IDLE)},
                dry_run=False, discord_fetch=self._fetch([self._msg()]))
            logs2 = wd.deliver_discord_replies(
                self.now + 60, self._run, state, {SID: ("%1", IDLE)},
                dry_run=False, discord_fetch=self._fetch([self._msg()]))
        self.assertNotIn("dorphan_done", state)
        self.assertTrue(any("orphan" in ln for ln in logs2),
                        "an unconfirmed ping is retried next sweep")

    def test_bare_dedup_claim_is_not_confirmation_and_gets_released(self):
        self._record()
        state = self._state()
        released = []
        with m.patch.object(notify, "send", lambda *a, **k: "dedup"), \
                m.patch.object(notify, "marker_delivered",
                               lambda key: False), \
                m.patch.object(notify, "forget_marker", released.append):
            wd.deliver_discord_replies(
                self.now, self._run, state, {SID: ("%1", IDLE)},
                dry_run=False, discord_fetch=self._fetch([self._msg()]))
        self.assertNotIn("dorphan_done", state,
                         "a claim written before a failed POST is not a "
                         "delivery — never mark on it")
        self.assertEqual(len(released), 1,
                         "the stale claim must be released so the next "
                         "sweep genuinely re-POSTs: %r" % released)

    def test_dedup_with_recorded_delivery_marks_done(self):
        self._record()
        state = self._state()
        with m.patch.object(notify, "send", lambda *a, **k: "dedup"), \
                m.patch.object(notify, "marker_delivered",
                               lambda key: True):
            wd.deliver_discord_replies(
                self.now, self._run, state, {SID: ("%1", IDLE)},
                dry_run=False, discord_fetch=self._fetch([self._msg()]))
        self.assertIn(str(self._msg()["id"]),
                      state.get("dorphan_done", []))


class NonQuestionChannelStaysQuiet(_Base):
    """#449-review F3/M3 teeth: the orphan floor is deliberately scoped to
    QUESTION channels — an owner reply-to-untracked in a channel that only
    ever carried CARDS (the main claude-<owner> thread) must stay silent
    (✅/card chatter is not an answer attempt)."""

    def test_reply_to_untracked_in_a_cards_only_channel_is_silent(self):
        Path(self.cpath).write_text(json.dumps(
            {"555777": {"repo": "o/r", "issue": 7, "channel": "888999",
                        "ts": self.now}}))
        msg = {"id": _snow(self.now), "author": {"id": OWNER},
               "message_reference": {"message_id": "444333"},
               "content": "ok super", "_channel": "888999"}
        logs = wd.deliver_discord_replies(
            self.now, self._run, {}, {}, dry_run=False,
            discord_fetch=self._fetch([msg]))
        self.assertFalse(any("orphan" in ln for ln in logs), logs)
        self.assertEqual(self.pings, [])


class AuthorlessReferenceIsNeverSilent(_Base):
    """#449-review F5: a referenced_message dict WITHOUT a genuinely
    human-shaped author (author-less, degenerate) must fail toward the
    orphan ping — the never-silent mandate's own fail direction."""

    def test_authorless_referenced_message_still_pings(self):
        # #652: still scoped to OUR OWN card (999888 in dq_posted) — the F5
        # fail-toward-ping direction holds for a degenerate referenced_message
        # on a card THIS box posted.
        self._record()
        state = {"dq_posted": {"999888": self.now}}
        msg = {"id": _snow(self.now), "author": {"id": OWNER},
               "message_reference": {"message_id": "999888"},
               "referenced_message": {},
               "content": "odpoved na staru otazku"}
        logs = wd.deliver_discord_replies(
            self.now, self._run, state, {SID: ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([msg]))
        self.assertTrue(any("orphan" in ln for ln in logs), logs)
        self.assertTrue(self.pings)


# (#795: `RepingCrossChannelGrace` was REMOVED with the daily re-ask it
# exercised — the #449-review F4 cross-channel grace scenario it locked was
# specific to `reping_stale_questions`' own RETRACK path (a fresh id posted
# + the old key superseded across channels), which no longer exists now
# that `reping_stale_questions` is a permanent no-op tombstone. The grace
# mechanism itself (`notify.grace_question`) stays fully live for
# `prune_answered_questions`' own #407/#449 drop paths, locked elsewhere in
# this file.)


if __name__ == "__main__":
    unittest.main()
