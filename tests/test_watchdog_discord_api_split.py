"""#433 item G step 6 — `watchdog/discord_api.py` split.

The nine small Discord-REST + reply/card-parsing primitives (`_discord_get`,
`fetch_channel_messages`, `fetch_reaction_users`, `_react_ok`, `_snowflake_ts`,
`clean_reply_text`, `parse_discord_reply`, `parse_discord_card_reply`,
`_reacted_by_owner`) were moved VERBATIM out of `watchdog/__init__.py` into
`watchdog.discord_api`, then re-exported IN PLACE by a positional facade import
at the earliest removed block's position (`clean_reply_text`, above every later
removed block and above every `__init__`-resident caller).

The design estimated this a "true leaf", but the ACTUAL code makes it a
BACK-REFERENCE module: two intra-module co-moved cross-calls plus four
`__init__`-resident constants are reached through the package namespace —

  * `fetch_channel_messages` / `fetch_reaction_users` -> `watchdog._discord_get`
  * `parse_discord_reply` / `parse_discord_card_reply` -> `watchdog.clean_reply_text`
  * `clean_reply_text` -> `watchdog._MENTION_TOKEN_RX`, `watchdog._CONTROL_CHAR_RX`,
    `watchdog.DISCORD_REPLY_MAX_CHARS`
  * `_snowflake_ts` -> `watchdog._DISCORD_EPOCH_MS`

— written call-time `watchdog.<name>` (module top: `import watchdog`), so every
existing `patch.object(watchdog, "_discord_get"/"_react_ok"/...)` seam
(test_discord_reply, test_reply_grace, test_stash_delivery, hosted delivery, ...)
keeps working unchanged, AND so the three constants that RELOCATE to
`discord_replies.py` in step 9 stay resolvable via the re-export no matter which
module physically owns them.

These tests lock the invariants that keep those seams live:

  1. `import watchdog` stays clean in a FRESH subprocess, and importing
     `watchdog.discord_api` FIRST initializes the package cleanly.
  2. Every moved name is the SAME object at `watchdog.<name>` and at
     `watchdog.discord_api.<name>` (the C2 re-export contract).
  3. The call-time back-references RESOLVE (functional teeth — a constant
     back-ref reverted to a bare name NameErrors in the new module's namespace).
  4. The two co-moved intra-module cross-calls and the four constant reads go
     through the PACKAGE seam, not a module-local/frozen copy — patch-observing
     teeth, the ONLY kind that catches a bare-name / module-top-`from`-import
     regression that every functional test passes right through (design #1 hazard).
"""

import subprocess
import sys
import unittest
import re
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.discord_api as discord_api  # noqa: E402

# The nine names moved into discord_api.py, in definition order. A hand-
# maintained checklist the reviewer diffs against the facade import block.
MOVED_NAMES = [
    "clean_reply_text",
    "parse_discord_reply",
    "_snowflake_ts",
    "_discord_get",
    "fetch_channel_messages",
    "_react_ok",
    "parse_discord_card_reply",
    "fetch_reaction_users",
    "_reacted_by_owner",
]


class FreshSubprocessImportIsClean(unittest.TestCase):
    def test_import_watchdog_in_fresh_process(self):
        r = subprocess.run(
            [sys.executable, "-c", "import watchdog; print('ok')"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stdout.strip(), "ok")
        self.assertEqual(r.stderr.strip(), "")

    def test_import_discord_api_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly. `discord_api`'s `import watchdog` binds the (already fully
        # initialized — Python imports the parent package first) module object,
        # and every `watchdog.<name>` access is call-time, so a real call
        # through the constant back-ref (_MENTION_TOKEN_RX) resolves.
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.discord_api as d; print(d.clean_reply_text('<@123> hi'))"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stderr.strip(), "")
        self.assertEqual(r.stdout.strip(), "hi")


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_is_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(discord_api, name),
                                f"{name} missing from watchdog.discord_api")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(getattr(watchdog, name), getattr(discord_api, name),
                              f"watchdog.{name} is not watchdog.discord_api.{name}")

    def test_discord_api_lives_in_its_own_module_file(self):
        self.assertTrue(discord_api.__file__.endswith("watchdog/discord_api.py"),
                        discord_api.__file__)

    def test_moved_functions_report_discord_api_as_their_module(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertEqual(getattr(watchdog, name).__module__,
                                 "watchdog.discord_api")


class BackReferencesResolveAtCallTime(unittest.TestCase):
    """Functional calls through each call-time back-reference. Any of these
    would NameError the instant a `watchdog.` prefix on a `__init__`-resident
    CONSTANT were reverted to a bare name (the constant is not in
    discord_api's namespace) — teeth at the resolution level, not just identity."""

    def test_clean_reply_text_strips_mention_and_controls_and_caps(self):
        # exercises watchdog._MENTION_TOKEN_RX + watchdog._CONTROL_CHAR_RX
        self.assertEqual(watchdog.clean_reply_text("<@42>  a\x07b\nc"), "a b c")

    def test_clean_reply_text_length_cap_reads_the_constant(self):
        # exercises watchdog.DISCORD_REPLY_MAX_CHARS (real value 1500)
        long = "x" * 5000
        self.assertEqual(len(watchdog.clean_reply_text(long)),
                         watchdog.DISCORD_REPLY_MAX_CHARS)

    def test_snowflake_ts_decodes_via_epoch_constant(self):
        # exercises watchdog._DISCORD_EPOCH_MS; any real snowflake decodes to a
        # plausible post-2015 epoch. (Deterministic teeth for the exact value
        # live in ConstantSeamsGoThroughPackage below.)
        got = watchdog._snowflake_ts("175928847299117063")
        self.assertGreater(got, 1420070400.0)   # Discord epoch (2015-01-01)
        self.assertLess(got, 2000000000.0)      # well before 2033
        self.assertEqual(watchdog._snowflake_ts("not-a-number"), 0)


class CoMovedCrossCallsGoThroughPackageSeam(unittest.TestCase):
    """The two INTRA-module co-moved cross-calls are written `watchdog.<name>`,
    NOT bare. A bare name would resolve to the module-local co-moved function
    and PASS every functional test while silently bypassing the
    `patch.object(watchdog, ...)` seam (design #1 hazard). Only these
    patch-observing tests have teeth."""

    def test_fetch_channel_messages_calls_discord_get_via_watchdog(self):
        with mock.patch.object(watchdog, "_discord_get",
                               return_value=b'[{"id":"1"},{"id":"2"}]'):
            got = watchdog.fetch_channel_messages("ch", "tok")
        self.assertEqual(got, [{"id": "1"}, {"id": "2"}])

    def test_fetch_reaction_users_calls_discord_get_via_watchdog(self):
        with mock.patch.object(watchdog, "_discord_get",
                               return_value=b'[{"id":"7"}]'):
            got = watchdog.fetch_reaction_users("ch", "1", "✅", "tok")
        self.assertEqual(got, [{"id": "7"}])

    def test_parse_discord_reply_calls_clean_reply_text_via_watchdog(self):
        msg = {"id": "r1", "author": {"id": "owner"}, "content": "raw body",
               "message_reference": {"message_id": "q1"}}
        qmap = {"q1": {"session": "s", "cwd": "/c", "channel": "ch"}}
        with mock.patch.object(watchdog, "clean_reply_text",
                               return_value="SENTINEL-CLEANED"):
            r = watchdog.parse_discord_reply(msg, {"owner"}, qmap)
        self.assertIsNotNone(r)
        self.assertEqual(r["text"], "SENTINEL-CLEANED")

    def test_parse_discord_card_reply_calls_clean_reply_text_via_watchdog(self):
        msg = {"id": "r1", "author": {"id": "owner"}, "content": "raw body",
               "message_reference": {"message_id": "c1"}}
        cardmap = {"c1": {"repo": "o/r", "issue": 5, "channel": "ch"}}
        with mock.patch.object(watchdog, "clean_reply_text",
                               return_value="SENTINEL-CARD"):
            r = watchdog.parse_discord_card_reply(msg, {"owner"}, cardmap)
        self.assertIsNotNone(r)
        self.assertEqual(r["text"], "SENTINEL-CARD")


class ConstantSeamsGoThroughPackage(unittest.TestCase):
    """The four `__init__`-resident constants are read call-time
    `watchdog.<CONST>`, NOT frozen by a module-top `from watchdog import <CONST>`.
    A frozen import would pass every identity + functional test yet ignore a
    package-level patch — and three of these constants MOVE to
    discord_replies.py in step 9, so a frozen copy would strand there. These
    patch-observing tests fail against a frozen-import regression."""

    def test_length_cap_tracks_a_patched_constant(self):
        with mock.patch.object(watchdog, "DISCORD_REPLY_MAX_CHARS", 5):
            self.assertEqual(watchdog.clean_reply_text("abcdefghij"), "abcde")

    def test_mention_regex_tracks_a_patched_constant(self):
        # Patch the mention regex to strip the literal token "ZAP" instead;
        # a frozen module-top import would still use the real <@id> regex.
        with mock.patch.object(watchdog, "_MENTION_TOKEN_RX", re.compile("ZAP")):
            self.assertEqual(watchdog.clean_reply_text("keepZAPme <@9>"),
                             "keep me <@9>")

    def test_control_char_regex_tracks_a_patched_constant(self):
        # Patch the control-char regex to a no-op (matches nothing); the raw
        # BEL then survives the sub and only the final \s+ collapse runs.
        with mock.patch.object(watchdog, "_CONTROL_CHAR_RX", re.compile("(?!x)x")):
            self.assertEqual(watchdog.clean_reply_text("a\x07b"), "a\x07b")

    def test_snowflake_epoch_tracks_a_patched_constant(self):
        with mock.patch.object(watchdog, "_DISCORD_EPOCH_MS", 0):
            # id 4194304 == (1 << 22); >>22 -> 1 ms, +0 epoch -> 0.001 s
            self.assertAlmostEqual(watchdog._snowflake_ts("4194304"), 0.001)


if __name__ == "__main__":
    unittest.main()
