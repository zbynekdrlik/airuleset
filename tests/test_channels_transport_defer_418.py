"""#418 — Channels pilot decision lock.

The pilot evaluated adopting Claude Code's native Discord PLUGIN transport
(`discord@claude-plugins-official`: `<channel source="discord">` inbound +
MCP `reply`/`fetch_messages`) for the ❓-question / reply-routing flow, and
DEFERRED it — the native channel is per-session-bound with no message_id→
session routing table, so it cannot serve airuleset's fan-in/fan-out star
(many askers → one shared per-owner thread → reply routed back to the exact
asker, cross-machine). Full evaluation + rejected seams live on issue #418.

This test locks TWO things so a future native-now re-audit (#423) re-validates
against the recorded decision instead of silently re-litigating it:

  1. the DECISION is durably recorded in the path-scoped internals rule
     (`.claude/rules/airuleset-internals.md`) and cannot be silently dropped;
  2. the custom transport the decision KEEPS still exists in code — the REST
     poster + the message_id→session map + the job-7 reply parser. If a future
     adoption removes any of them while the doc still says "KEPT", this test
     fails and forces re-opening the #418 decision.

Written as a unittest.TestCase so `python3 -m unittest discover -s tests`
(cmd_push's gate) genuinely collects it — a bare `def test_x()` file with no
TestCase is silently skipped by that discovery mechanism.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INTERNALS = REPO / ".claude" / "rules" / "internals-notify.md"  # #482: #418 decision moved here
NOTIFY = REPO / "notify" / "__init__.py"
# #433 step 6 relocated the job-7 reply parser (parse_discord_reply) OUT of
# watchdog/__init__.py into watchdog/discord_api.py (re-exported into the
# package via the facade). The machinery is KEPT, only moved — track it to its
# new home so this #418 "still-kept" lock stays honest instead of failing on
# the move.
WATCHDOG_DISCORD_API = REPO / "watchdog" / "discord_api.py"

# Distinctive substrings of the #418 decision bullet — sampled from its head,
# the decisive reason, the KEEP verdict, and the re-audit trigger, so the whole
# decision (not just a header) is proven present and verbatim.
DECISION_ANCHORS = [
    "The native Discord PLUGIN transport",
    "CANNOT carry the ❓/reply-routing flow",
    "per-session-BOUND with no `message_id`→session routing table",
    "it is DEFERRED and the custom REST + `discord-questions.json` map + "
    "watchdog job 7 transport is KEPT (#418",
    "Re-audit trigger (per #423's permanent native-now process)",
]


class TestChannelsTransportDeferRecorded(unittest.TestCase):
    def test_internals_rule_exists(self):
        self.assertTrue(
            INTERNALS.exists(),
            "the #418 decision needs a durable, path-scoped surface to live on",
        )

    def test_decision_recorded_verbatim(self):
        text = INTERNALS.read_text(encoding="utf-8")
        for anchor in DECISION_ANCHORS:
            self.assertIn(
                anchor, text,
                "#418 decision anchor missing from the internals rule "
                "(silently dropped?): %r" % anchor[:70],
            )


class TestKeptTransportStillExists(unittest.TestCase):
    """The decision is KEEP-custom; lock that the custom code it keeps is
    actually present, so 'KEPT' can never quietly become a lie."""

    def test_rest_poster_kept(self):
        text = NOTIFY.read_text(encoding="utf-8")
        self.assertIn(
            "def _post_discord", text,
            "the REST transport the #418 decision keeps is gone from notify/",
        )
        self.assertIn(
            "discord.com/api/v10", text,
            "notify no longer posts via the Discord REST API — re-open #418",
        )

    def test_question_map_kept(self):
        text = NOTIFY.read_text(encoding="utf-8")
        self.assertIn(
            "def record_question", text,
            "the message_id→session map (`record_question`) the #418 decision "
            "keeps is gone — re-open #418",
        )

    def test_job7_reply_parser_kept(self):
        text = WATCHDOG_DISCORD_API.read_text(encoding="utf-8")
        self.assertIn(
            "def parse_discord_reply", text,
            "watchdog job-7 reply routing (`parse_discord_reply`) the #418 "
            "decision keeps is gone — re-open #418",
        )


if __name__ == "__main__":
    unittest.main()
