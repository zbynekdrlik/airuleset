"""#704 — apply the analyze-not-ping doctrine fleet-wide: suppress airuleset's own
STATE / STALL heuristic owner-pings, the direct siblings of the already-suppressed
apierr / sesslimit / stuckalert(#688) / lanestall(#693) / oauthblock(#676) family.

Owner directive (2026-08-25, verbatim): "chodi mi od teba denne stovky sprav na
discord je to uplne nepouzitelne … spravny postup by mal byt analyzovat preco bola
taka situacia … a nie ma dokolecka spamovat niecim co len otravuje". The general
ruling: an idle / stall / no-work / session-stojí state NEVER owner-pings — the
machine channel keeps the verdict (watchdog journal + the `suppressed` delivery-log
line) and airuleset fixes the cause. The phone keeps ONLY: ❓ question, ✅ final
done, per-ticket run-card, acctblock, job-35 dead-fleet.

Measurement (issue #704 comment) confirmed these classes still reach the phone:
`goal-dark` (💀 "/goal loop zomrelo, spusti /autopilot"), `goalarm-expired`
(⚠️ "/goal auto-arm zlyhal"), `stuck-main-open`/`-recover` (🔒 "vetva stojí N dní"),
`delivery-stall` (📦 "N dní sa nič nedoručilo"), `inputdead` (⚠️ "session beží, vstup
mŕtvy"), `pwedge`/`pwedge-backlog`/`pwedge-submit-giveup` (prompt wedge), `busypane`
(🛑 "visí na WORKING, zaseknuto"), `long-turn` (🕰 "ťah beží dlho"),
`workingstall-giveup` / `textcall-giveup` (🛑 "session nereaguje, zamrzol proces").

Every one is a HEURISTIC "session/loop/lane died/stalled/idle" verdict with its own
auto-recovery (which never routes through `send()`, so suppression at the chokepoint
leaves it untouched) — exactly #688's own reasoning. Fix = add each to the EXISTING
#546 `SUPPRESSED_ALERT_PREFIXES` denylist; the `send()` gate POSTs nothing, returns
"suppressed", and leaves an explicit `suppressed` delivery-log line (never a silent
drop, #486/#134).

RED against the pre-#704 tree (where none of these prefixes are in
`SUPPRESSED_ALERT_PREFIXES`).
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify                                            # noqa: E402


class _HomeIsolated(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-state704-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self._restore_home)
        # A suppressed send must NEVER touch the network — a spy proves it.
        self._orig_post = notify._post_discord
        self.posts = []
        notify._post_discord = lambda *a, **k: self.posts.append((a, k)) or "999"
        self.addCleanup(lambda: setattr(notify, "_post_discord", self._orig_post))

    def _restore_home(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home

    @property
    def log(self):
        return self.home / ".claude" / "notify-delivery.log"

    def log_lines(self):
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text().splitlines() if ln.strip()]

    def _write_env(self):
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtokenxx\n"
            "DISCORD_NOTIFICATION_CHANNEL_ID=123456789\n")


# The LIVE keys each #704 state/stall sender emits, exactly as constructed in the
# watchdog (goal.py / repo_health.py / wedge.py / discord_replies.py / long_turn.py /
# __init__.py) — one representative per class, including a re-ping / recover variant.
STATE_STALL_KEYS = [
    "goal-dark:sid-abc:1000000",           # goal.py:2124 first ping
    "goal-dark:sid-abc:1000000:3",         # goal.py:2125 re-ping #3
    "goalarm-expired:sid-abc:1000480",     # goal.py:990
    "stuck-main-open:odoo-erp:1000000",    # repo_health.py:769
    "stuck-main-recover:odoo-erp:1000900", # repo_health.py:778 (recover pair)
    "delivery-stall:camera-box:1000",      # repo_health.py:285
    "inputdead:sid-abc12345",              # discord_replies.py:770
    "pwedge:12345:deadbeef",               # wedge.py:296
    "pwedge-backlog:12345:deadbeef",       # wedge.py:278
    "pwedge-submit-giveup:12345:deadbeef", # wedge.py:216
    "busypane:proj-key:1000000",           # __init__.py:3487
    "long-turn:sid-abc:1000000",           # long_turn.py:371
    "workingstall-giveup:proj-key:1000",   # __init__.py:3523
    "textcall-giveup:proj-key:1000",       # __init__.py:3345
]

# Classes that MUST keep pinging — the sanctioned set + the deliberately-KEPT
# question relay. `waiting:` carries the ACTUAL ❓ question text (a real question
# relay, NOT a stall verdict — __init__.py:3293), so it is NEVER suppressed. The
# operational classes (conformance / net-drift / gkreq / dorphan) are left to a
# needs-user-decision follow-up, not suppressed here. The owner-decision-digest
# class this list used to keep ("a digest the owner may want — follow-up") got
# its owner decision in #707: the class is ABOLISHED (cross-subject leak via the
# account_owner coin flip) — see TestOwnerDigestAbolished707 below.
STILL_DELIVERED_KEYS = [
    "waiting:proj-key:1000000",            # ❓ question relay — KEEP
    "acctblock:sid:1",                     # genuine account block — needs a human
    "airuleset#704",                       # per-ticket run-card
    "odoo-erp#4607",                       # per-ticket run-card (with a dash in name)
    "conformance:dev1:deploy:1000",        # config-drift ops alert — follow-up, not here
    "net-drift-open:odoo-erp:1000",        # backlog-trend — follow-up, not here
    "gkreq:odoo-erp:1000",                 # cross-stream request — follow-up
]


class TestStateStallSuppressed(_HomeIsolated):
    def test_classifier_recognises_every_state_stall_key(self):
        for k in STATE_STALL_KEYS:
            self.assertIsNotNone(
                notify._suppressed_alert_class(k),
                "%r must be a #704 owner-suppressed state/stall class" % k)

    def test_send_posts_nothing_and_returns_suppressed(self):
        self._write_env()          # fully configured — a normal key WOULD post
        for k in STATE_STALL_KEYS:
            self.posts.clear()
            r = notify.send("💀 stavová hláška", dedup_key=k)
            self.assertEqual(r, "suppressed", "%r should be suppressed" % k)
            self.assertEqual(self.posts, [], "%r must POST nothing" % k)

    def test_suppression_is_a_logged_decision_not_silent(self):
        # #486/#134: a suppressed send leaves an explicit delivery-log line.
        self._write_env()
        notify.send("body", dedup_key="goal-dark:sid:1")
        lines = [ln for ln in self.log_lines() if "suppressed" in ln]
        self.assertTrue(lines, "a suppressed state/stall send must be LOGGED")
        self.assertIn("goal-dark", lines[-1], "the log line names the key")

    def test_dry_run_suppressed_mutates_nothing(self):
        self._write_env()
        r = notify.send("body", dedup_key="stuck-main-open:x:2", dry_run=True)
        self.assertEqual(r, "suppressed")
        self.assertEqual(self.log_lines(), [],
                         "dry-run must not write to the delivery log")

    def test_prefix_boundary_no_false_match(self):
        # boundary-matched on ':'/'-' — a same-letters-but-different-namespace
        # key must NOT be swept in.
        self.assertIsNone(notify._suppressed_alert_class("goal-darker:1"))
        self.assertIsNone(notify._suppressed_alert_class("pwedgeother:1"))
        self.assertIsNone(notify._suppressed_alert_class("longturn:1"))
        self.assertIsNone(notify._suppressed_alert_class("stuck-mainish:1"))


class TestSanctionedAndQuestionRelayUntouched(_HomeIsolated):
    """The sanctioned set + the ❓ `waiting:` question relay MUST still deliver."""

    def test_none_of_the_kept_keys_is_suppressed(self):
        for k in STILL_DELIVERED_KEYS:
            self.assertIsNone(
                notify._suppressed_alert_class(k),
                "%r must NOT be suppressed (#704 keeps it pinging)" % k)

    def test_waiting_question_relay_still_delivers(self):
        # `waiting:` carries the real ❓ question text — the CRITICAL non-regression.
        self._write_env()
        r = notify.send("❓ **proj** — čaká na teba\n> naozajstná otázka",
                        dedup_key="waiting:proj-key:1000000")
        self.assertEqual(r, "sent", "the ❓ question relay must NOT be suppressed")

    def test_run_card_and_acctblock_still_deliver(self):
        self._write_env()
        for k in ("airuleset#704", "acctblock:s:9"):
            self.posts.clear()
            r = notify.send("body", dedup_key=k)
            self.assertEqual(r, "sent", "%r must still deliver" % k)

    def test_pre_existing_546_classes_unaffected(self):
        self.assertIsNotNone(notify._suppressed_alert_class("apierr-giveup:k:h:1"))
        self.assertIsNotNone(notify._suppressed_alert_class("sesslimit:s:1"))
        self.assertIsNotNone(notify._suppressed_alert_class("stuckalert:sid:1"))


class TestOwnerDigestAbolished707(_HomeIsolated):
    """#707 — the daily owner-decision digest class is ABOLISHED (owner ruling
    2026-08-26). The producer is a permanent no-op tombstone
    (`watchdog/questions.py::reping_owner_decision_tickets`); THIS is the
    belt-and-braces send()-chokepoint proof, so even STALE code on a
    not-yet-redeployed box can never ping. The class's own dedup_key
    (`owner-decision-digest:<day-bucket>`) is the denylist match — the #546
    layer is dedup_key-keyed by construction, no message-prefix mechanism
    needed. Machine channel keeps the decision: the `suppressed` delivery-log
    line below, never a silent drop."""

    def test_digest_send_posts_nothing_and_returns_suppressed(self):
        self._write_env()          # fully configured — a normal key WOULD post
        r = notify.send(
            "**Rozhodnutia čakajúce na teba (denný súhrn):**\n\n"
            "- automatizacie-montalu #285 — …",
            dedup_key="owner-decision-digest:20691")
        self.assertEqual(r, "suppressed",
                         "the abolished digest class must never POST (#707)")
        self.assertEqual(self.posts, [], "a suppressed digest must POST nothing")

    def test_digest_suppression_is_a_logged_decision_not_silent(self):
        self._write_env()
        notify.send("body", dedup_key="owner-decision-digest:20692")
        lines = [ln for ln in self.log_lines() if "suppressed" in ln]
        self.assertTrue(lines, "a suppressed digest send must be LOGGED")
        self.assertIn("owner-decision-digest", lines[-1],
                      "the log line names the key")

    def test_prefix_boundary_no_false_match(self):
        # boundary-matched on ':'/'-' — a same-letters-but-longer key must NOT
        # be swept in (the #704 HAZARD comment's discipline).
        self.assertIsNone(
            notify._suppressed_alert_class("owner-decision-digests:1"))


if __name__ == "__main__":
    unittest.main()
