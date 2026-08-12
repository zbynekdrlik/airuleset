"""Stream-qualified Discord ping labels (user complaint 2026-07-20: every
stream's ping says just "odoo-erp" — gatekeeper, montalu and david are
indistinguishable on the phone). A ping's project label carries the STREAM
identity: the box's unix user is appended for sub-dev/gatekeeper stream users
(gatekeeper → odoo-erp-gatekeeper, montalu → odoo-montalu, david →
odoo-erp-david); the personal `newlevel` boxes keep the plain label."""

import os
import subprocess
import sys
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd

ROOT = Path(__file__).resolve().parent.parent
AIRULESET = ROOT / "airuleset.py"
SEND_HOOK = ROOT / "hooks" / "notify-discord-send.sh"


class TestWatchdogProjectLabel(unittest.TestCase):
    def test_stream_user_is_appended(self):
        with m.patch("getpass.getuser", return_value="gatekeeper"):
            self.assertEqual(wd.project_label("/home/gatekeeper/devel/odoo/odoo-erp"),
                             "odoo-erp-gatekeeper")
        with m.patch("getpass.getuser", return_value="montalu"):
            self.assertEqual(wd.project_label("/home/montalu/devel/odoo"),
                             "odoo-montalu")
        with m.patch("getpass.getuser", return_value="david"):
            self.assertEqual(wd.project_label("/home/david/devel/odoo-erp"),
                             "odoo-erp-david")

    def test_newlevel_and_root_stay_plain(self):
        for u in ("newlevel", "root"):
            with m.patch("getpass.getuser", return_value=u):
                self.assertEqual(wd.project_label("/home/newlevel/devel/restreamer"),
                                 "restreamer")

    def test_generic_dir_handling_still_composes(self):
        with m.patch("getpass.getuser", return_value="marek"):
            self.assertEqual(wd.project_label("/home/marek/devel/bakerion-ai/repo"),
                             "bakerion-ai/repo-marek")


class TestSendHookAppendsStreamUser(unittest.TestCase):
    """#369 superseded the hook's OWN inline `id -un` stream-qualifier block —
    PROJECT is now resolved via `notify --project-label --cwd`, the SAME
    canonical, single-source-of-truth call the run-card header and the
    per-project Discord thread routing already use (so a hook computing
    this once and reusing it for both the header text and the routing call
    can never disagree). The stream-qualification BEHAVIOR itself (unix
    user appended for gatekeeper/montalu/david, personal boxes stay plain)
    is unit-tested directly against `notify.project_label_for` in
    tests/test_notify_project_channel.py::TestProjectLabelFor — this class
    only proves the SHELL hook delegates to that canonical mechanism rather
    than re-deriving its own copy."""

    def test_hook_delegates_to_the_canonical_project_label_cli_mode(self):
        src = SEND_HOOK.read_text()
        self.assertIn("--project-label", src)
        self.assertIn("--cwd \"$CWD\"", src)
        # the OLD duplicate mechanism this ticket removed must be genuinely
        # gone, not merely supplemented — a hook that kept BOTH would have
        # two sources of truth that could silently disagree again.
        self.assertNotIn("id -un", src)
        self.assertNotIn('PROJECT="${PROJECT}-${STREAM_USER}"', src)

    def test_hook_header_project_matches_the_canonical_cli_output(self):
        # Functional proof: the hook's rendered header carries EXACTLY the
        # value `notify --project-label --cwd ROOT` itself returns for
        # this box's real unix user — not a value the hook derived on its
        # own. Runs the REAL `notify --project-label` (the same call the
        # hook makes), so this holds for whichever real user runs the
        # suite (a personal box or a stream box), without needing to fake
        # the OS-level identity `getpass.getuser()` resolves.
        expected = subprocess.run(
            [sys.executable, str(AIRULESET), "notify", "--project-label",
             "--cwd", str(ROOT)],
            capture_output=True, text=True).stdout.strip()
        self.assertTrue(expected, "notify --project-label produced nothing")
        env = {**os.environ, "ND_EMOJI": "✅", "ND_TEXT": "hotovo",
              "ND_CWD": str(ROOT), "DISCORD_NOTIFY_DRYRUN": "1"}
        env.pop("ND_DRYRUN_FILE", None)
        r = subprocess.run(["bash", str(SEND_HOOK)], input="",
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("**✅ %s**" % expected, r.stdout, r.stdout)


if __name__ == "__main__":
    unittest.main()


class TestPythonComposersQualifyStream(unittest.TestCase):
    """The run-card + api-error pings compose their header in notify (Python) —
    the third and fourth label points; the user's card still said bare
    'odoo-erp' after the shell/watchdog fix (2026-07-20 22:18)."""

    def test_run_card_header_carries_stream_user(self):
        import notify
        with m.patch("getpass.getuser", return_value="gatekeeper"):
            card = notify.compose_autopilot_card(
                repo="zbynekdrlik/odoo-erp",
                tickets=[{"n": 1770, "goal": "x", "achieved": "y"}],
                version="2.120.0", remaining=29)
        self.assertIn("odoo-erp-gatekeeper", card)

    def test_api_error_alert_carries_stream_user(self):
        import notify
        with m.patch("getpass.getuser", return_value="montalu"):
            body = notify.compose_api_error_alert("/home/montalu/devel/odoo",
                                                  "API Error 529")
        self.assertIn("odoo-montalu", body)

    def test_newlevel_composers_stay_plain(self):
        import notify
        with m.patch("getpass.getuser", return_value="newlevel"):
            card = notify.compose_autopilot_card(
                repo="zbynekdrlik/restreamer",
                tickets=[{"n": 5, "goal": "x", "achieved": "y"}],
                version="1.0.0", remaining=0)
        self.assertIn("restreamer", card)
        self.assertNotIn("restreamer-newlevel", card)


class NoDoubleSuffix(unittest.TestCase):
    def test_already_qualified_name_is_not_doubled(self):
        # 2026-07-22 live alert: "odoo-erp-david-david" — project_label on a
        # stream box already appends the user; stream_qualified must not re-add
        import notify
        with m.patch("getpass.getuser", return_value="david"):
            self.assertEqual(notify.stream_qualified("odoo-erp-david"),
                             "odoo-erp-david")
            self.assertEqual(notify.stream_qualified("odoo-erp"),
                             "odoo-erp-david")
