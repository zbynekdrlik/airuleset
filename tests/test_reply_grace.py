"""#449: a Discord reply to a pruned/superseded ❓ must NEVER be lost silently.

Live incident (david@subdev, 2026-08-13): David answered a ❓ on his phone
while ALSO typing unrelated prompts at the terminal. prune_answered_questions
treats ANY later human prompt in the asking session as "answered at the
terminal" and hard-deleted the map entries; job 7 (deliver_discord_replies)
then had nothing to match — with an empty map it returns before even fetching
Discord — so the phone answer vanished with ZERO journal lines.

These tests lock the #449 fix:
  (1) a pruned entry moves to a GRACE store and a reply still routes
      NORMALLY (typed into the asking session, with the original question
      wording) for QUESTION_GRACE_S;
  (2) after grace expiry — or for any owner answer attempt that cannot be
      matched at all (reply to an untracked id, plain non-reply message in a
      questions thread) — job 7 must journal it AND ping the owner through
      the sanctioned notify path: the never-silent floor;
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
        # Both the statusline Q badge (statusbar.py) and the daily re-ask
        # (reping_stale_questions) read ONLY discord-questions.json — a
        # graced entry must live in a SEPARATE file, or #407's ghost and
        # the 2026-07-22 stale-badge problem both come back.
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

    def test_plain_message_in_questions_thread_is_never_silent(self):
        # secondary (a) of #449: parse_discord_reply requires an explicit
        # message_reference, so a plain (non-reply) owner message in the
        # questions thread was dropped with no trace. It must journal+ping.
        self._record()
        msg = {"id": _snow(self.now), "author": {"id": OWNER},
               "content": "mozes pouzit moznost 2"}
        logs = wd.deliver_discord_replies(
            self.now, self._run, {}, {SID: ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([msg]))
        self.assertTrue(any("orphan" in ln for ln in logs), logs)
        self.assertTrue(self.pings)
        self.assertTrue(any("Reply" in p["body"] for p in self.pings),
                        "the ping must teach the user to answer via Reply: "
                        "%r" % self.pings)

    def test_orphan_ping_dedups_per_message(self):
        self._record()
        msg = {"id": _snow(self.now), "author": {"id": OWNER},
               "content": "mozes pouzit moznost 2"}
        state = {}
        wd.deliver_discord_replies(
            self.now, self._run, state, {SID: ("%1", IDLE)}, dry_run=False,
            discord_fetch=self._fetch([msg]))
        wd.deliver_discord_replies(
            self.now + 60, self._run, state, {SID: ("%1", IDLE)},
            dry_run=False, discord_fetch=self._fetch([msg]))
        self.assertEqual(len(self.pings), 1,
                         "one unroutable message = ONE ping, ever: %r"
                         % self.pings)

    def test_dry_run_never_marks_orphan_state(self):
        # #304 discipline: a --dry-run sweep must not poison the real next
        # sweep's dedup state.
        self._record()
        msg = {"id": _snow(self.now), "author": {"id": OWNER},
               "content": "mozes pouzit moznost 2"}
        state = {}
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


if __name__ == "__main__":
    unittest.main()
