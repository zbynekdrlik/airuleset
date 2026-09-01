"""#716 — preserve the footer `U N` ticketless-question fold for #710-suppressed
owners (zbynek / marek).

#710 turned ❓ Discord DELIVERY off for zbynek/marek and redirected them to the
webterm + the footer `U N`. But the question-map record
(`notify.record_question`) is coupled to a successful Discord POST (it needs the
returned snowflake message-id), so a suppressed owner's ❓ was NEVER recorded —
`statusbar.ticketless_question_pings` (the sole feed for the `U N` ticketless
fold) saw nothing, so a genuinely-ticketless ❓ reached NO aggregate surface.
This ticket records a Discord-less "suppressed" map entry so the ticketless ❓
still folds into `U N`, and threads that new entry TYPE inertly through the two
watchdog consumers (re-ask churn, orphan re-fire).

Three coupled locks, one per scope item:
  1. the writer + the footer fold (record_question suppressed=True, the CLI, the
     interactive hook) — the suppressed ❓ folds into `U N`;
  2. `reping_stale_questions` treats send()'s "suppressed" as a re-ask CHOICE
     (refresh ts, keep the entry) not a transient failure (retry every sweep);
  3. `discord_replies._orphan_floor` marks `dorphan_done` on "suppressed" so an
     orphaned reply to an OFF owner's old card does not re-fire every sweep.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify                                             # noqa: E402
import statusbar                                          # noqa: E402
import watchdog as wd                                     # noqa: E402
import watchdog.questions as wq                           # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PENDING = ROOT / "hooks" / "notify-discord-pending.sh"
_DISCORD_EPOCH_MS = 1420070400000


def _snow(epoch):
    """A realistic Discord snowflake id encoding `epoch` (seconds)."""
    return str(int(epoch * 1000 - _DISCORD_EPOCH_MS) << 22)


# --------------------------------------------------------------------------- #
# 1. record_question(suppressed=True) — the Discord-less writer + the U N fold
# --------------------------------------------------------------------------- #
class TestSuppressedRecordAndFold(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-716-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self.path = str(self.home / ".claude" / "discord-questions.json")
        self.cwd = "/home/x/devel/demo"

    def _map(self):
        return json.loads(Path(self.path).read_text()) if os.path.exists(self.path) else {}

    def test_suppressed_entry_is_recorded(self):
        ok = notify.record_question(
            "", "", "sid-716-a", self.cwd,
            question="**Otázka — projekt demo:** čo mám urobiť?\n❓ NEEDS YOU: A či B?",
            path=self.path, suppressed=True)
        self.assertTrue(ok, "a suppressed record must succeed (Discord-less)")
        d = self._map()
        self.assertEqual(len(d), 1, d)
        key = next(iter(d))
        self.assertFalse(key.isdigit(),
                         "the suppressed key must be a non-Discord (non-digit) key: %r" % key)
        self.assertEqual(d[key]["channel"], "",
                         "the suppressed entry's channel must be empty (never a fetchable id)")
        self.assertEqual(d[key]["session"], "sid-716-a")
        self.assertTrue(d[key].get("suppressed") is True,
                        "the entry must be marked suppressed: %r" % d[key])

    def test_ticketless_suppressed_folds_into_U_N(self):
        # THE CORE GAP: a suppressed ticketless ❓ must count in the footer U N.
        notify.record_question(
            "", "", "sid-716-b", self.cwd,
            question="**Otázka — projekt demo:** ktorý layout?\n❓ NEEDS YOU: A či B?",
            path=self.path, suppressed=True)
        self.assertEqual(statusbar.question_ping_count(self.cwd, home=self.home), 1,
                         "a ticketless suppressed ❓ must fold into U N")

    def test_ticket_carrying_suppressed_is_deduped_out_of_ticketless_fold(self):
        # A ticket-carrying ❓ folds into U N via its needs-answer LABEL, so it
        # must be EXCLUDED from the ticketless fold (never double-counted) —
        # but its #N must still surface in question_map_ticket_refs.
        notify.record_question(
            "", "", "sid-716-c", self.cwd,
            question="**Otázka — projekt demo #42:** …\n❓ NEEDS YOU: A či B?",
            path=self.path, suppressed=True)
        self.assertEqual(statusbar.question_ping_count(self.cwd, home=self.home), 0,
                         "a ticket-carrying suppressed ❓ must NOT fold into the ticketless count")
        self.assertIn(42, statusbar.question_map_ticket_refs(self.cwd, home=self.home))

    def test_newest_suppressed_per_session_supersedes_in_place(self):
        # Deterministic per-session key => the newest ticketless ❓ overwrites
        # the older (mirrors the normal supersede's "newest ask per session").
        for q in ("prvá otázka?", "druhá otázka?"):
            notify.record_question("", "", "sid-716-d", self.cwd,
                                   question=q, path=self.path, suppressed=True)
        d = self._map()
        self.assertEqual(len(d), 1, "one tracked suppressed ❓ per session: %r" % d)
        self.assertIn("druhá", next(iter(d.values()))["block"])

    def test_digit_guard_intact_for_normal_path(self):
        # The incident-hardened guard MUST stay closed for the normal (non-
        # suppressed) path: a non-snowflake id/channel is still refused.
        self.assertFalse(
            notify.record_question("not-a-snowflake", "x", "sid", self.cwd,
                                   path=self.path))
        self.assertEqual(self._map(), {})

    def test_suppressed_channel_never_enters_fetch_set(self):
        # job 7 builds its Discord fetch set from every qmap entry's channel;
        # the suppressed sentinel MUST be "" so q_channels.discard("") drops it
        # (a literal "suppressed" channel would GET-poll a bogus Discord thread).
        notify.record_question("", "", "sid-716-e", self.cwd,
                               question="q?", path=self.path, suppressed=True)
        d = json.loads(Path(self.path).read_text())
        chans = {str(v.get("channel") or "") for v in d.values()}
        chans.discard("")
        self.assertEqual(chans, set(),
                         "a suppressed entry must contribute no fetchable channel")


# --------------------------------------------------------------------------- #
# 2. the interactive Stop-hook path records a suppressed entry for zbynek
# --------------------------------------------------------------------------- #
def _fake_curl_bin(http="200", mid="716999"):
    d = Path(tempfile.mkdtemp(prefix="airuleset-fakecurl-716-"))
    (d / "curl").write_text(
        "#!/usr/bin/env bash\n"
        "printf '%%s\\n%%s' '{\"id\":\"%s\"}' '%s'\n" % (mid, http))
    (d / "curl").chmod(0o755)
    return d


_NEEDS_YOU = (
    "**Otázka — projekt airuleset (nástroj na správu Claude Code pravidiel):** "
    "Pri tikete potrebujem tvoj výber.\n"
    "\n"
    "- Možnosť A (odporúčam)\n"
    "- Možnosť B\n"
    "\n"
    "❓ NEEDS YOU: ktorú možnosť mám použiť?")


class InteractiveSuppressedFold(unittest.TestCase):
    _n = 0

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-716-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cwd = Path(tempfile.mkdtemp(prefix="airuleset-716-cwd-"))
        self.addCleanup(shutil.rmtree, self.cwd, True)
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtok\n"
            "DISCORD_NOTIFICATION_CHANNEL_ID=123\n"
            "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=77799\n"
            "DISCORD_NOTIFICATION_CHANNEL_DAVID_Q=99977\n")

    def _sid(self, label):
        InteractiveSuppressedFold._n += 1
        sid = "t716-%s-%d-%d" % (label, os.getpid(), InteractiveSuppressedFold._n)
        for pre in ("lastq", "pending", "pending-cwd", "cardchk"):
            p = "/tmp/claude-discord-%s-%s" % (pre, sid)
            self.addCleanup(lambda p=p: os.path.exists(p) and os.remove(p))
        return sid

    def _fire(self, sid, owner, msg):
        cbin = _fake_curl_bin()
        self.addCleanup(shutil.rmtree, cbin, True)
        env = {**os.environ, "HOME": str(self.home),
               "PATH": str(cbin) + os.pathsep + os.environ["PATH"],
               "ND_BLOCK_SETTLE": "0", "TMUX_PANE": "",
               "AIRULESET_NOTIFY_OWNER": owner}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        env.pop("ND_DRYRUN_FILE", None)
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg,
                              "cwd": str(self.cwd)})
        return subprocess.run(["bash", str(PENDING)], input=payload, text=True,
                              capture_output=True, env=env)

    def _map(self):
        p = self.home / ".claude" / "discord-questions.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def test_zbynek_ticketless_question_folds_into_U_N(self):
        sid = self._sid("zbynek")
        r = self._fire(sid, "zbynek", _NEEDS_YOU)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = self._map()
        self.assertEqual(len(d), 1, "the suppressed ❓ must be recorded: %r" % d)
        key = next(iter(d))
        self.assertFalse(key.isdigit(), "not keyed by a Discord message id: %r" % key)
        self.assertEqual(d[key]["session"], sid)
        self.assertEqual(statusbar.question_ping_count(str(self.cwd), home=self.home), 1,
                         "a suppressed zbynek ❓ must fold into the footer U N")

    def test_david_still_records_a_real_discord_entry(self):
        # regression: david keeps FULL delivery — a real snowflake-keyed entry.
        sid = self._sid("david")
        r = self._fire(sid, "david", _NEEDS_YOU)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = self._map()
        self.assertIn("716999", d, "david's ❓ is keyed by its Discord message id: %r" % d)
        self.assertFalse(d["716999"].get("suppressed"),
                         "david's entry is a real delivered question, not suppressed")


# --------------------------------------------------------------------------- #
# 3. reping treats "suppressed" as a re-ask CHOICE — no per-sweep churn
# --------------------------------------------------------------------------- #
class TestRepingHonorsSuppressedWithoutChurn(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-716-reping-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self.path = str(self.home / ".claude" / "discord-questions.json")
        # a due suppressed entry (non-digit key, empty channel, old ts).
        Path(self.path).write_text(json.dumps({
            "suppressed:sid-1": {
                "session": "sid-1", "cwd": "/tmp/x", "channel": "",
                "ts": 1, "asked": 1, "suppressed": True,
                "block": "**Otázka — projekt X:** …\n❓ NEEDS YOU: čo?",
                "question": "…"}}))
        # #791 deleted the sleep-window deferral — the re-ask runs at any
        # wall clock, so no `_in_sleep_window` neutralisation is needed.

    def test_suppressed_reping_refreshes_ts_and_does_not_churn(self):
        calls = []

        def spy(block, **k):
            calls.append(k)
            return ("suppressed", None)

        now1 = 10 ** 9
        wq.reping_stale_questions(now=now1, send_fn=spy, path=self.path,
                                  account_owner="zbynek")
        d = json.loads(Path(self.path).read_text())
        self.assertIn("suppressed:sid-1", d, "the suppressed entry must be KEPT")
        self.assertEqual(d["suppressed:sid-1"]["ts"], now1,
                         "the entry's ts must be refreshed to the current re-ask bucket")
        # a SECOND sweep within the re-ask interval must NOT re-attempt.
        wq.reping_stale_questions(now=now1, send_fn=spy, path=self.path,
                                  account_owner="zbynek")
        self.assertEqual(len(calls), 1,
                         "a suppressed re-ask must fire at most once per interval, "
                         "not every 60s sweep: %r" % calls)


# --------------------------------------------------------------------------- #
# 4. the orphan floor marks done on "suppressed" — no re-fire every sweep
# --------------------------------------------------------------------------- #
OWNER = "773451844110385193"
SID = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"
CWD = "/home/x/devel/demo"
IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"


class TestOrphanFloorMarksDoneOnSuppressed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
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
        self.now = time.time()
        # establish 777001 as a QUESTION channel (a tracked entry on it).
        notify.record_question("888001", "777001", SID, CWD,
                               now=self.now - 3600, path=self.qpath,
                               question="Ticket #9 — ako?")

    def _run(self, argv, timeout=8):
        j = " ".join(argv)
        if "pane_in_mode" in j:
            return "0"
        if "capture-pane" in j:
            return IDLE
        return ""

    def _fetch(self, msgs):
        return lambda ch, token: [x for x in msgs
                                  if x.get("_channel", "777001") == ch]

    def _orphan_msg(self):
        # a reply to a card THIS box posted (888009 in dq_posted) that is no
        # longer tracked → the orphan floor fires.
        return {"id": _snow(self.now), "author": {"id": OWNER},
                "message_reference": {"message_id": "888009"},
                "content": "mozes pouzit moznost 2"}

    def test_suppressed_orphan_send_is_marked_done(self):
        state = {"dq_posted": {"888009": self.now}}
        with m.patch.object(notify, "send", lambda *a, **k: "suppressed"):
            wd.deliver_discord_replies(
                self.now, self._run, state, {SID: ("%1", IDLE)},
                dry_run=False, discord_fetch=self._fetch([self._orphan_msg()]))
        self.assertIn(str(self._orphan_msg()["id"]),
                      state.get("dorphan_done", []),
                      "a suppressed orphan ping is a made decision — mark it "
                      "done so it never re-fires every sweep")


# --------------------------------------------------------------------------- #
# 5. review-fix locks (#716 adversarial review): dry-run non-mutation,
#    transitional-supersede convergence, update_question skip, real fetch drive.
# --------------------------------------------------------------------------- #
SEND = ROOT / "hooks" / "notify-discord-send.sh"


class TestReviewFixes(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-716-rf-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self.path = str(self.home / ".claude" / "discord-questions.json")
        self.gpath = str(self.home / ".claude" / "discord-questions-grace.json")
        self.cwd = "/home/x/devel/demo"

    def _map(self):
        return json.loads(Path(self.path).read_text()) if os.path.exists(self.path) else {}

    # 🟡1 — a DRY-RUN preview must NOT write a real suppressed map entry.
    def test_dryrun_send_hook_does_not_mutate_the_map(self):
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtok\n"
            "DISCORD_NOTIFICATION_CHANNEL_ID=123\n"
            "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=77799\n")
        dryfile = self.home / "dry.out"
        env = {**os.environ, "HOME": str(self.home),
               "AIRULESET_NOTIFY_OWNER": "zbynek",
               "DISCORD_NOTIFY_DRYRUN": "1", "ND_DRYRUN_FILE": str(dryfile),
               "ND_EMOJI": "❓", "ND_BLOCK": "1",
               "ND_TEXT": "**Otázka — projekt demo:** čo?\n❓ NEEDS YOU: A či B?",
               "ND_CWD": self.cwd, "ND_SESSION_ID": "rf-sid-dry"}
        r = subprocess.run(["bash", str(SEND)], text=True, capture_output=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p = self.home / ".claude" / "discord-questions.json"
        self.assertFalse(p.exists() and json.loads(p.read_text()),
                         "a dry-run preview must never write a real suppressed "
                         "entry (a phantom footer U N from a mere preview)")

    # 🔵4 — a suppressed record graces a stale DELIVERED sibling (converges the
    # transitional U N double-count) but never a channel-less legacy entry.
    def test_suppressed_supersedes_stale_delivered_but_not_channelless(self):
        # a stale DELIVERED entry (real snowflake channel, older generation)
        notify.record_question("999888", "777001", "sid-sup", self.cwd,
                               now=1000, path=self.path, grace_path=self.gpath,
                               question="staré delivered?")
        # a channel-LESS legacy entry for the SAME session (must be untouched)
        d = self._map()
        d["legacy-nochan"] = {"session": "sid-sup", "cwd": self.cwd,
                              "channel": "", "ts": 900, "asked": 900,
                              "question": "legacy?", "block": "legacy?"}
        Path(self.path).write_text(json.dumps(d))
        # now a NEW ticketless suppressed ❓ for the same session
        notify.record_question("", "", "sid-sup", self.cwd, now=2000,
                               path=self.path, grace_path=self.gpath,
                               question="nové ticketless?", suppressed=True)
        left = self._map()
        self.assertNotIn("999888", left,
                         "the stale delivered sibling must be superseded (graced)")
        self.assertIn("suppressed:sid-sup", left)
        self.assertIn("legacy-nochan", left,
                      "a channel-less legacy entry must NOT be graced by the "
                      "suppressed record")
        g = notify.load_grace_questions(self.gpath)
        self.assertIn("999888", g, "the delivered sibling's replies still route via grace")

    # 🔵5 — update_question skips a suppressed entry (no doomed Discord GET).
    def test_update_question_skips_suppressed_entry(self):
        notify.record_question("", "", "sid-upd", self.cwd, question="q?",
                               path=self.path, suppressed=True)
        calls = []

        def http_spy(token, method, ep, body=None):
            calls.append((method, ep))
            return None

        r = notify.update_question("sid-upd", "reworded?",
                                   env={"DISCORD_BOT_TOKEN": "tok"},
                                   path=self.path, http=http_spy)
        self.assertFalse(r, "a suppressed entry has no card to edit")
        self.assertEqual(calls, [],
                         "no Discord GET/PATCH may be issued for a suppressed "
                         "entry (would be a doomed round-trip): %r" % calls)

    # 🔵6 — drive the REAL job-7 fetch-set build: a suppressed entry's "" channel
    # is never fetched, while a real delivered entry's channel IS.
    def test_suppressed_channel_never_fetched_by_real_job7(self):
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_MENTION_ZBYNEK": "773451844110385193",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "777001"}
        with m.patch.object(notify, "_questions_path", lambda: self.path), \
                m.patch.object(notify, "_read_env", lambda: dict(env)), \
                m.patch.object(wd, "_react_ok", return_value=True):
            # a REAL delivered entry (channel 777001) + a suppressed entry ("")
            notify.record_question("888001", "777001", "sid-real", self.cwd,
                                   now=time.time() - 60, path=self.path,
                                   question="ticket?")
            notify.record_question("", "", "sid-sup", self.cwd,
                                   question="ticketless?", path=self.path,
                                   suppressed=True)
            fetched = []

            def fetch_spy(ch, token):
                fetched.append(ch)
                return []

            wd.deliver_discord_replies(time.time(), lambda *a, **k: "",
                                       {}, {}, dry_run=False,
                                       discord_fetch=fetch_spy)
        self.assertIn("777001", fetched, "the real delivered channel IS fetched")
        self.assertNotIn("", fetched, "the suppressed entry's empty channel is never fetched")
        self.assertNotIn("suppressed:sid-sup", fetched)


if __name__ == "__main__":
    unittest.main()
