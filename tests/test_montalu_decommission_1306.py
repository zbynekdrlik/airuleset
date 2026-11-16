"""#1306 — the montalu client is gone (owner 11.11.2026) and its accounts
montalu1..8@subdev were deleted with their homes (owner 16.11.2026, "ano mozes
ich zrusit"). Every airuleset row that names those ACCOUNTS leaves with them:
push targets, authority, notify routing, skill re-grants, the subdev ssh
allow-list, drop-lane ports, dominika's montalu5 observe tab, the stream
priority entry and the privilege inventory's reach text.

One invariant must SURVIVE the removal: odoo-erp still holds open tickets
labelled `stream:montalu` / `stream:montaluN` (that repo's call). While the
accounts existed those labels were excluded from the full-authority core via
AUTHORITY_BY_USER; without the rows they would flood the gatekeeper's I. The
retired labels (`cli_fleet.RETIRED_STREAM_LABELS`) keep them out exactly as
before.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import airuleset  # noqa: E402
import cli_drop_lanes  # noqa: E402
import cli_quals  # noqa: E402
import cli_webterm  # noqa: E402
import cli_webterm_only  # noqa: E402
import cli_webterm_profiles  # noqa: E402
import notify  # noqa: E402

MONTALU = re.compile(r"^montalu\d*$")
STREAMS = ["montalu"] + ["montalu%d" % i for i in range(1, 9)]


class TestAccountRowsGone(unittest.TestCase):
    def test_no_push_target(self):
        for h in airuleset.REMOTE_HOSTS:
            self.assertFalse(MONTALU.match(h.get("user", "")), h["name"])
            self.assertNotIn("montalu", h["name"])

    def test_no_authority_row(self):
        self.assertFalse([u for u in airuleset.AUTHORITY_BY_USER if MONTALU.match(u)])

    def test_no_rename_alias(self):
        aliases = airuleset.STREAM_RENAME_ALIASES
        self.assertFalse([k for k in list(aliases) + list(aliases.values())
                          if MONTALU.match(k)])

    def test_no_notify_routing(self):
        self.assertFalse([u for u in notify.STREAM_NOTIFY_OWNER if MONTALU.match(u)])

    def test_no_skill_regrant(self):
        self.assertFalse([u for u in airuleset.SKILLS_EXTRA_BY_USER if MONTALU.match(u)])

    def test_no_subdev_ssh_allow_row(self):
        self.assertFalse([u for u in cli_webterm_only.SUBDEV_ACCOUNTS_FALLBACK
                          if MONTALU.match(u)])
        hook = (ROOT / "hooks" / "block-subdev-ssh-misuse.sh").read_text(encoding="utf-8")
        self.assertNotIn('"montalu1": "default"', hook)
        self.assertNotIn("montalu[12345678]@subdev", hook)

    def test_no_drop_lane_port(self):
        self.assertFalse([k for k in cli_drop_lanes._GENERATED_DROP_PORTS
                          if MONTALU.match(k[1])])

    def test_no_dominika_montalu5_tab(self):
        ids = [e["id"] for e in cli_webterm_profiles.dominika_inventory()]
        self.assertNotIn("montalu5-subdev", ids)
        self.assertEqual(cli_webterm.WEBTERM_DASHBOARD_TABS["dominika"], ["miva1-subdev"])

    def test_no_stream_priority_row(self):
        data = json.loads((ROOT / "stream-priority.json").read_text(encoding="utf-8"))
        self.assertNotIn("montalu", data)

    def test_privilege_reach_text(self):
        src = (ROOT / "cli_privileges.py").read_text(encoding="utf-8")
        self.assertNotIn("montalu1-8", src)
        self.assertNotIn("montalu/2/3/4", src)


class TestRetiredLabelsStayOutOfTheCore(unittest.TestCase):
    """The invariant the rows used to carry, now carried by the retired set."""

    def test_core_exclusion_keeps_every_montalu_label(self):
        excl = cli_quals._core_search_excl().split()
        for name in STREAMS:
            with self.subTest(name=name):
                self.assertIn("-label:stream:%s" % name, excl)

    def test_still_stream_labelled(self):
        for name in STREAMS:
            with self.subTest(name=name):
                self.assertTrue(cli_quals._ticket_is_stream_labeled(
                    [{"name": "stream:%s" % name}]))

    def test_retired_set_is_exactly_the_montalu_family(self):
        self.assertEqual(set(airuleset.RETIRED_STREAM_LABELS), set(STREAMS))

    def test_watchdog_full_authority_bounce_query_excludes_them(self):
        import watchdog as wd
        quals = wd._bounce_quals("/home/newlevel/devel/demo")
        for name in STREAMS:
            with self.subTest(name=name):
                self.assertIn("-label:stream:%s" % name, quals[0].split())

    def test_a_live_stream_is_not_retired(self):
        self.assertFalse(set(airuleset.RETIRED_STREAM_LABELS)
                         & set(airuleset.AUTHORITY_BY_USER))


if __name__ == "__main__":
    unittest.main()
