"""#1137 (issue 1233 folded in) — ONE owner-level Discord policy.

Owner (2026-10-26): "v discorde skoro nič nečítam … mám v pätičke U". For
owner zbynek ONLY the genuine needs-a-human alarms reach Discord —
`acctblock:`, `credential-dead:`, a healthz DOWN and its RECOVERY, and the
job-35 dead-fleet `conformance-hb:` alarm — and everything else (✅ forwards
incl. the shell hook path, run-cards, gk*/bounce/handoff/orphan, `waiting:`,
keyless `--body` …) is logged `suppressed`. The policy applies per TARGET:
every DISCORD_MIRROR_* recipient by its own owner's policy, and an EMPTY owner
(the shared channel) as zbynek. Owner david (david1-4) is unchanged. A
`suppressed` send counts as delivered for the run-card gate, `notify
--run-card` and the job-25 card reconcile. Job 25's report reconcile
(`report-owed:` nudge) stays; its 📮 escalation rides the same policy.
"""

import contextlib
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import airuleset                                          # noqa: E402
import notify                                             # noqa: E402
import watchdog                                           # noqa: E402

SEND_SH = ROOT / "hooks" / "notify-discord-send.sh"


class _Home(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-1137-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True)
        p = m.patch.dict(os.environ, {"HOME": str(self.home)})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("AIRULESET_SHARED_CHANNEL_OWNER", None)   # the default
        self.posts = []
        for target, value in (
                ("_post_discord", lambda *a, **k: self.posts.append(a) or "1137001"),
                ("_claude_dir", lambda: str(self.home / ".claude"))):
            pp = m.patch.object(notify, target, value)
            pp.start()
            self.addCleanup(pp.stop)
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtokenxx\n"
            "DISCORD_NOTIFICATION_CHANNEL_ID=123456789\n"
            "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=770011\n"
            "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=770099\n"
            "DISCORD_NOTIFICATION_CHANNEL_DAVID=990011\n"
            "DISCORD_NOTIFICATION_CHANNEL_DAVID_Q=990099\n")

    def log(self):
        p = self.home / ".claude" / "notify-delivery.log"
        return p.read_text() if p.exists() else ""


class ZbynekPolicy(_Home):

    def test_non_allowed_classes_are_suppressed(self):
        for key in ("gkstale:odoo-erp:1", "gkreq:odoo-erp#5:1", "bounce:o#2:3",
                    "handoff:o#4", "orphan:123",
                    "janitor-stuck:abc:1", "card-unreported:x:1",
                    "airuleset#1137", "healthz:dev1:status:5", None):
            with self.subTest(key=key):
                r = notify.send("hotovo", owner="zbynek", dedup_key=key)
                self.assertEqual(r, "suppressed")
        self.assertEqual(self.posts, [])
        lines = [ln for ln in self.log().splitlines() if "suppressed" in ln]
        self.assertGreaterEqual(len(lines), 10, self.log())
        self.assertTrue(all("#1137" in ln for ln in lines), self.log())

    def test_alarm_classes_still_send(self):
        # review R3: conformance-hb (job 35 dead fleet, never in the footer)
        # and the healthz RECOVERY (pairs with the down alarm) are alarms too.
        for key in ("acctblock:5e55:1", "credential-dead:%5:2",
                    "healthz:dev1:down:3", "healthz:dev1:recovery:4",
                    "conformance-hb:dev2:5"):
            with self.subTest(key=key):
                self.assertEqual(notify.send("alarm", owner="zbynek",
                                             dedup_key=key), "sent")
        self.assertEqual(len(self.posts), 5)

    def test_waiting_question_suppressed(self):
        self.assertEqual(notify.send("❓ čaká", owner="zbynek", kind="questions",
                                     dedup_key="waiting:%5:9"), "suppressed")
        self.assertEqual(self.posts, [])

    def test_persona_routing_into_zbynek_is_suppressed(self):
        self.assertEqual(notify.send("hotovo", owner="montalu1",
                                     dedup_key="gkstale:x:1"), "suppressed")

    def test_suppressed_marker_counts_as_delivered(self):
        self.assertEqual(notify.send("card", owner="zbynek",
                                     dedup_key="airuleset#1137"), "suppressed")
        self.assertEqual(self.posts, [])
        self.assertTrue(notify.marker_delivered("airuleset#1137"))

    def test_dry_run_mutates_nothing(self):
        self.assertEqual(notify.send("x", owner="zbynek", dedup_key="gkstale:x:2",
                                     dry_run=True), "suppressed")
        self.assertEqual(self.log(), "")
        self.assertFalse(notify.marker_delivered("gkstale:x:2"))


class DavidUnchanged(_Home):

    def test_david_gets_everything(self):
        for owner in ("david", "david1"):
            for key in ("gkstale:odoo-erp:%s" % owner, "airuleset#%s" % owner):
                with self.subTest(owner=owner, key=key):
                    self.assertEqual(notify.send("x", owner=owner,
                                                 dedup_key=key), "sent")
        self.assertEqual(notify.send("❓", owner="david", kind="questions",
                                     dedup_key="waiting:%5:1"), "sent")
        self.assertEqual(len(self.posts), 5)


def _fake_curl(tmp):
    d = Path(tmp) / "bin"
    d.mkdir()
    (d / "curl").write_text("#!/usr/bin/env bash\n"
                            "printf '%s\\n%s' '{\"id\":\"1137999\"}' '200'\n")
    (d / "curl").chmod(0o755)
    return d


class ShellDonePath(unittest.TestCase):
    """The ✅ forward of `hooks/notify-discord-send.sh` (the idle-hook path)."""

    def _fire(self, owner):
        tmp = tempfile.mkdtemp(prefix="airuleset-1137-sh-")
        self.addCleanup(shutil.rmtree, tmp, True)
        home = Path(tmp) / "home"
        d = home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True)
        (d / ".env").write_text("DISCORD_BOT_TOKEN=x\n"
                                "DISCORD_NOTIFICATION_CHANNEL_ID=123\n"
                                "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=771\n"
                                "DISCORD_NOTIFICATION_CHANNEL_DAVID=991\n")
        cwd = Path(tmp) / "proj"
        cwd.mkdir()
        env = {**os.environ, "HOME": str(home), "TMUX_PANE": "",
               "PATH": str(_fake_curl(tmp)) + os.pathsep + os.environ["PATH"],
               "AIRULESET_NOTIFY_OWNER": owner, "ND_EMOJI": "✅",
               "ND_TEXT": "verzia na dashboarde nasadená %s" % owner,
               "ND_CWD": str(cwd), "ND_CONFIRM": "1",
               "AIRULESET_CONTENT_DEDUP_DIR": str(Path(tmp) / "cdedup")}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        r = subprocess.run(["bash", str(SEND_SH)], env=env, text=True,
                           capture_output=True, timeout=120)
        p = home / ".claude" / "notify-delivery.log"
        return r, (p.read_text() if p.exists() else "")

    @staticmethod
    def _status(log):
        return [ln.split()[1] for ln in log.splitlines() if len(ln.split()) > 1]

    def test_zbynek_done_is_suppressed(self):
        r, log = self._fire("zbynek")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("suppressed", self._status(log), log)
        self.assertNotIn("sent", self._status(log), log)

    def test_david_done_still_sent(self):
        r, log = self._fire("david")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sent", self._status(log), log)


def _card_args(**over):
    base = dict(
        run_card=True, autopilot_done=False, mention_prefix=False,
        content_dedup_claim=False, repo_name=False, newest_card=False,
        backfill_digest=False, provision_question_thread=False,
        provision_project_thread=False, project_label=False,
        record_question=False, edit_question=False, channel_id=False,
        owner=False, mirror_owners=False, question_ping_off=False, body=None,
        run=None, repo="o/x", issue=1137, pr=None,
        achieved="Zmergované 4d840f1d, suita zelená, overené",
        result=None, goal="Štíhly report a Discord politika",
        version="0.1.558", merge_sha="4d840f1d", url=None, review="ok",
        handoff=False, dedup_key=None, dry_run=False)
    base.update(over)
    return m.Mock(**base)


class RunCardSuppressed(unittest.TestCase):

    def test_suppressed_card_exits_zero(self):
        home = tempfile.mkdtemp(prefix="airuleset-1137-card-")
        self.addCleanup(shutil.rmtree, home, True)
        code = None
        err = io.StringIO()
        with m.patch.object(notify, "_claude_dir",
                            return_value=os.path.join(home, ".claude")), \
                m.patch.object(airuleset, "_gh_out",
                               return_value='{"title": "T", "labels": []}'), \
                m.patch.object(airuleset, "_write_autopilot_progress"), \
                m.patch("notify.send", return_value="suppressed"), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            try:
                airuleset.cmd_notify(_card_args())
            except SystemExit as exc:
                code = exc.code
        self.assertIn(code, (None, 0), err.getvalue())


class _Tmux:
    def __init__(self, pane, cwd, capture):
        self.panes = "%s\tclaude\t%s\n" % (pane, cwd)
        self.capture = capture

    def __call__(self, argv, timeout=8):
        if argv[:2] == ["tmux", "list-panes"]:
            return self.panes
        if argv[:2] == ["tmux", "capture-pane"]:
            return self.capture
        return ""


class WaitingPingKind(unittest.TestCase):
    """The `waiting:` ❓ relay rides the question kind, so the owner policy
    and the -q thread apply to it."""

    def test_waiting_ping_is_a_question(self):
        tmp = tempfile.mkdtemp(prefix="airuleset-1137-wd-")
        self.addCleanup(shutil.rmtree, tmp, True)
        projects = Path(tmp) / "projects"
        cwd = "/devel/asking1137"
        d = projects / watchdog.encode_project_dir(cwd)
        d.mkdir(parents=True)
        t = d / "5e55abc0-51d0-4a5e-9f1e-000000001137.jsonl"
        now = 1_000_000
        t.write_text(json.dumps({"type": "assistant", "isApiErrorMessage": False,
                                 "message": {"role": "assistant", "content": [
                                     {"type": "text", "text": "Hotovo."}]}}) + "\n")
        os.utime(t, (now - 200, now - 200))
        cache = m.patch.object(watchdog.usage, "_USAGE_CACHE_PATH",
                               str(Path(tmp) / "usage.json"))
        cache.start()
        self.addCleanup(cache.stop)
        calls = []

        def send(body, **kw):
            calls.append(kw)
            return "sent"
        cap = ("│ ❯ 1. Direction gates\n│ 2. Time + pairing\n"
               "│ Enter to select · Tab/Arrow keys to navigate · Esc to cancel")
        kw = dict(run=_Tmux("%5", cwd, cap), send_fn=send, projects_dir=projects,
                  state_path=str(Path(tmp) / "state.json"), grace=300,
                  wait_grace=120)
        watchdog.run_once(now=now, **kw)
        watchdog.run_once(now=now + 60, **kw)
        waiting = [c for c in calls
                   if str(c.get("dedup_key", "")).startswith("waiting:")]
        self.assertEqual(len(waiting), 1, calls)
        self.assertEqual(waiting[0].get("kind"), "questions")


class PerTargetPolicy(_Home):
    """Review R3: the policy filters EVERY target by its own owner, and an
    EMPTY owner (the shared channel) is zbynek."""

    def _mirror(self, line):
        p = self.home / ".claude" / "channels" / "discord" / ".env"
        p.write_text(p.read_text() + line + "\n")

    def test_zbynek_mirror_of_david_is_filtered(self):
        self._mirror("DISCORD_MIRROR_DAVID=zbynek")
        self.assertEqual(notify.send("x", owner="david",
                                     dedup_key="gkstale:o:7"), "sent")
        self.assertEqual([a[1] for a in self.posts], ["990011"])
        self.assertIn("mirror", self.log())
        self.assertEqual(notify.send("x", owner="david",
                                     dedup_key="acctblock:o:7"), "sent")
        self.assertEqual([a[1] for a in self.posts[1:]], ["990011", "770011"])

    def test_empty_owner_is_zbynek(self):
        self.assertEqual(notify.send("x", owner="", dedup_key="gkstale:o:8"),
                         "suppressed")
        self.assertEqual(notify.send("x", owner="", dedup_key=None),
                         "suppressed")
        self.assertEqual(self.posts, [])
        self.assertEqual(notify.send("x", owner="",
                                     dedup_key="conformance-hb:dev1:8"), "sent")
        self.assertEqual([a[1] for a in self.posts], ["123456789"])

    def test_question_ping_off_for_empty_owner(self):
        self.assertTrue(notify.question_ping_off(""))
        self.assertFalse(notify.question_ping_off("david"))


class ReportReconcileRestored(_Home):
    """Review R1: job 25's report-owed nudge is back; its 📮 escalation goes
    through notify.send and therefore through the owner policy."""

    def test_report_reconcile_is_wired(self):
        import watchdog.cards as cards
        self.assertTrue(callable(getattr(cards, "report_reconcile", None)))
        self.assertIs(watchdog.report_reconcile, cards.report_reconcile)
        self.assertIn("report_reconcile(", inspect.getsource(watchdog.run_once))
        self.assertIn("report_owed", inspect.getsource(cards.report_reconcile))

    def test_escalation_follows_the_owner_policy(self):
        import watchdog.cards as cards
        root = str(self.home / "repo")
        self.assertEqual(cards._report_escalate_ping(
            notify.send, root, [41], "#41", "zbynek", False), "suppressed")
        self.assertEqual(self.posts, [])
        self.assertEqual(cards._report_escalate_ping(
            notify.send, root, [42], "#42", "david", False), "sent")
        self.assertEqual([a[1] for a in self.posts], ["990011"])


class ShellPerTarget(unittest.TestCase):
    """Review R3 on the shell ❓/✅ path: mirrors filtered per owner, an empty
    owner (shared channel) is zbynek."""

    def _fire(self, owner, mirror=""):
        tmp = tempfile.mkdtemp(prefix="airuleset-1137-sh2-")
        self.addCleanup(shutil.rmtree, tmp, True)
        home = Path(tmp) / "home"
        d = home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True)
        (d / ".env").write_text("DISCORD_BOT_TOKEN=x\n"
                                "DISCORD_NOTIFICATION_CHANNEL_ID=123\n"
                                "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=771\n"
                                "DISCORD_NOTIFICATION_CHANNEL_DAVID=991\n"
                                + mirror)
        bindir = Path(tmp) / "bin"
        bindir.mkdir()
        urls = Path(tmp) / "urls"
        (bindir / "curl").write_text(
            "#!/usr/bin/env bash\n"
            "for a in \"$@\"; do case \"$a\" in https://*) "
            "printf '%%s\\n' \"$a\" >> %s;; esac; done\n"
            "printf '%%s\\n%%s' '{\"id\":\"1137998\"}' '200'\n" % urls)
        (bindir / "curl").chmod(0o755)
        cwd = Path(tmp) / "proj"
        cwd.mkdir()
        env = {**os.environ, "HOME": str(home), "TMUX_PANE": "",
               "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
               "AIRULESET_NOTIFY_OWNER": owner, "ND_EMOJI": "✅",
               "ND_TEXT": "per-target %s" % owner, "ND_CWD": str(cwd),
               "ND_CONFIRM": "1",
               "AIRULESET_CONTENT_DEDUP_DIR": str(Path(tmp) / "cdedup")}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        env.pop("AIRULESET_SHARED_CHANNEL_OWNER", None)          # the default
        r = subprocess.run(["bash", str(SEND_SH)], env=env, text=True,
                           capture_output=True, timeout=120)
        p = home / ".claude" / "notify-delivery.log"
        posted = urls.read_text().split() if urls.exists() else []
        return r, (p.read_text() if p.exists() else ""), posted

    def test_empty_owner_done_is_suppressed(self):
        r, log, posted = self._fire("")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(posted, [])
        self.assertIn(" suppressed ", log)

    def test_zbynek_mirror_of_david_is_skipped(self):
        r, log, posted = self._fire("david", "DISCORD_MIRROR_DAVID=zbynek\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(posted), 1, posted)
        self.assertIn("/channels/991/", posted[0])
        self.assertIn("mirror=zbynek", log)


class RunCardDryRunSuppressed(unittest.TestCase):
    """Review R6: a dry-run card for a suppressed owner writes no state."""

    def test_dry_run_suppressed_card_writes_nothing(self):
        home = tempfile.mkdtemp(prefix="airuleset-1137-card-dry-")
        self.addCleanup(shutil.rmtree, home, True)
        code = None
        err = io.StringIO()
        with m.patch.object(notify, "_claude_dir",
                            return_value=os.path.join(home, ".claude")), \
                m.patch.object(airuleset, "_gh_out",
                               return_value='{"title": "T", "labels": []}'), \
                m.patch.object(airuleset, "_write_autopilot_progress") as prog, \
                m.patch("notify.send", return_value="suppressed"), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            try:
                airuleset.cmd_notify(_card_args(dry_run=True))
            except SystemExit as exc:
                code = exc.code
        self.assertIn(code, (None, 0), err.getvalue())
        prog.assert_not_called()
        self.assertFalse(os.path.exists(
            os.path.join(home, ".claude", "notify-delivery.log")))


class WaitingSuppressedFoldsIntoU(unittest.TestCase):
    """Review R7: a suppressed `waiting:` relay records the #716 Discord-less
    `suppressed:<sid>` entry so the question shows in the footer U."""

    SID = "5e55abc0-51d0-4a5e-9f1e-000000001138"

    def _run(self, seed=None):
        tmp = tempfile.mkdtemp(prefix="airuleset-1137-wq-")
        self.addCleanup(shutil.rmtree, tmp, True)
        home = Path(tmp) / "home"
        (home / ".claude").mkdir(parents=True)
        qpath = home / ".claude" / "discord-questions.json"
        if seed is not None:
            qpath.write_text(json.dumps(seed))
        projects = Path(tmp) / "projects"
        cwd = "/devel/asking1138"
        d = projects / watchdog.encode_project_dir(cwd)
        d.mkdir(parents=True)
        t = d / ("%s.jsonl" % self.SID)
        now = 1_000_000
        t.write_text(json.dumps({"type": "assistant", "isApiErrorMessage": False,
                                 "message": {"role": "assistant", "content": [
                                     {"type": "text", "text": "Hotovo."}]}}) + "\n")
        os.utime(t, (now - 200, now - 200))
        for patch in (m.patch.object(watchdog.usage, "_USAGE_CACHE_PATH",
                                     str(Path(tmp) / "usage.json")),
                      m.patch.dict(os.environ, {"HOME": str(home)})):
            patch.start()
            self.addCleanup(patch.stop)
        cap = ("│ ❯ 1. Direction gates\n│ 2. Time + pairing\n"
               "│ Enter to select · Tab/Arrow keys to navigate · Esc to cancel")
        kw = dict(run=_Tmux("%5", cwd, cap),
                  send_fn=lambda body, **k: "suppressed",
                  projects_dir=projects, state_path=str(Path(tmp) / "state.json"),
                  grace=300, wait_grace=120)
        watchdog.run_once(now=now, **kw)
        watchdog.run_once(now=now + 60, **kw)
        return json.loads(qpath.read_text()) if qpath.exists() else {}

    def test_suppressed_relay_records_a_question(self):
        q = self._run()
        entry = q.get("suppressed:%s" % self.SID)
        self.assertIsNotNone(entry, q)
        self.assertTrue(entry.get("suppressed"))
        self.assertEqual(entry.get("cwd"), "/devel/asking1138")
        self.assertIn("Direction gates", entry.get("block", ""))

    def test_own_question_is_not_duplicated(self):
        key = "suppressed:%s" % self.SID
        seed = {key: {"session": self.SID, "cwd": "/devel/asking1138",
                      "channel": "", "ts": 1, "asked": 1, "question": "vlastná",
                      "block": "vlastná otázka", "suppressed": True}}
        q = self._run(seed)
        self.assertEqual(list(q), [key])
        self.assertEqual(q[key]["block"], "vlastná otázka")


if __name__ == "__main__":
    unittest.main()
