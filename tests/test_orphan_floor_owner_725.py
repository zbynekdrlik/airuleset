"""#725 (#716 review 🔵7) — the orphan floor resolves the ping's OWNER from the
card's CHANNEL, never `resolve_owner()`'s coin flip.

`_orphan_floor` (watchdog/discord_replies.py) used to call `notify.send(...,
kind="questions")` with NO explicit `owner=`, so `send()` fell back to
`resolve_owner()` — the CALLING PROCESS's own tmux session group, unrelated to
which owner's `-q` thread the orphaned card actually lives in. On a multi-owner
box (dev2: zbynek + marek + david sessions in the same sweep) that is a coin
flip; #716 made a `"suppressed"` outcome TERMINAL (`dorphan_done`), removing the
old "a later sweep's differently-lucky flip eventually resolves right" retry
safety net.

Two coupled locks:
  1. `notify.channel_owner(ch, env)` — a deterministic reverse resolver
     (channel id -> owner), inverting `notification_channel(kind="questions")`'s
     own cascade (`_Q` key, else the plain per-owner key). Exactly one matching
     owner -> that owner; zero or ambiguous (>1 match, e.g. two owners sharing a
     misconfigured channel) -> `None` — never a first-match/coin-flip (mirrors
     the #717 `_repo_owner_from_panes` single-unique-derivation-or-None shape).
  2. `_orphan_floor` resolves the owner from `ch` BEFORE sending and passes it
     explicitly to `send(owner=...)`; an unresolved channel skips the send
     entirely (an explicit decision-log line, never `resolve_owner()`).
"""

import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify                                              # noqa: E402
import watchdog as wd                                      # noqa: E402
import watchdog.discord_replies as dr                       # noqa: E402

OWNER_Z = "<@111>"
OWNER_M = "<@222>"


def _env_two_owners():
    return {
        "DISCORD_BOT_TOKEN": "tok",
        "DISCORD_MENTION_ZBYNEK": OWNER_Z,
        "DISCORD_MENTION_MAREK": OWNER_M,
        "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q": "700001",
        "DISCORD_NOTIFICATION_CHANNEL_MAREK_Q": "700002",
        "DISCORD_NOTIFICATION_CHANNEL_ID": "700099",
    }


# --------------------------------------------------------------------------- #
# 1. notify.channel_owner(ch, env) — the reverse resolver, unit-level
# --------------------------------------------------------------------------- #
class TestChannelOwnerResolver(unittest.TestCase):
    def test_resolves_via_q_key(self):
        self.assertEqual(notify.channel_owner("700001", _env_two_owners()),
                         "zbynek")
        self.assertEqual(notify.channel_owner("700002", _env_two_owners()),
                         "marek")

    def test_resolves_via_plain_owner_key_when_no_q_key_configured(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "777001"}
        self.assertEqual(notify.channel_owner("777001", env), "zbynek")

    def test_unmatched_channel_returns_none(self):
        self.assertIsNone(notify.channel_owner("999999", _env_two_owners()))

    def test_empty_or_missing_channel_returns_none(self):
        self.assertIsNone(notify.channel_owner("", _env_two_owners()))
        self.assertIsNone(notify.channel_owner(None, _env_two_owners()))

    def test_ambiguous_shared_channel_across_two_owners_returns_none(self):
        # two owners' OWN configured channel collides on the same id -- this
        # must NEVER coin-flip to either one.
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "700099",
              "DISCORD_NOTIFICATION_CHANNEL_MAREK": "700099"}
        self.assertIsNone(notify.channel_owner("700099", env))

    def test_bare_shared_id_key_is_never_mistaken_for_an_owner(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ID": "555555"}
        self.assertIsNone(notify.channel_owner("555555", env))

    def test_project_scoped_key_is_never_mistaken_for_a_q_owner(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_P_MONTALU": "888888"}
        self.assertIsNone(notify.channel_owner("888888", env))


# --------------------------------------------------------------------------- #
# 2. _orphan_floor wiring -- the routing fix at the actual call site
# --------------------------------------------------------------------------- #
class TestOrphanFloorResolvesOwnerFromChannel(unittest.TestCase):
    def setUp(self):
        p = m.patch.object(wd, "_orphan_answer_reason",
                           return_value="untracked-ref")
        p.start()
        self.addCleanup(p.stop)
        self.now = time.time()

    def _run_floor(self, ch, env, send_fn, mid="900001"):
        logs = []
        orphan_done, orphan_done_set = [], set()
        state = {}
        self.assertTrue(
            m.patch.object(notify, "send", send_fn).start())
        self.addCleanup(m.patch.stopall)
        dr._orphan_floor({"id": mid, "content": "moznost 2"}, ch,
                         set(), {}, {}, set(), self.now, env, False,
                         set(), orphan_done, orphan_done_set,
                         state, lambda: None, logs, {})
        return logs, orphan_done_set, state

    def test_resolved_owner_is_passed_explicitly_to_send(self):
        calls = []

        def _fake(*a, **k):
            calls.append(k)
            return "sent"

        self._run_floor("700001", _env_two_owners(), _fake)
        self.assertEqual(len(calls), 1, "send() must be called exactly once")
        self.assertEqual(calls[0].get("owner"), "zbynek",
                         "the owner passed to send() must come from the "
                         "card's channel (700001 -> zbynek's -q thread), "
                         "never resolve_owner()'s coin flip")
        self.assertEqual(calls[0].get("kind"), "questions")

    def test_second_owner_channel_resolves_to_second_owner(self):
        calls = []

        def _fake(*a, **k):
            calls.append(k)
            return "sent"

        self._run_floor("700002", _env_two_owners(), _fake, mid="900002")
        self.assertEqual(calls[0].get("owner"), "marek")

    def test_unresolvable_channel_skips_send_and_logs_the_decision(self):
        calls = []

        def _fake(*a, **k):
            calls.append(k)
            return "sent"

        logs, orphan_done_set, state = self._run_floor(
            "999999", _env_two_owners(), _fake, mid="900003")
        self.assertEqual(calls, [],
                         "an unresolvable channel must NEVER fall back to "
                         "resolve_owner()'s coin flip by calling send() "
                         "with no owner")
        self.assertTrue(any("owner unresolved" in line for line in logs),
                        "the skip must be an explicit decision-log line, "
                        "never a silent drop: %r" % logs)
        # non-terminal -- a config fix on a later sweep can still resolve it.
        self.assertNotIn("900003", orphan_done_set)
        self.assertNotIn("dorphan_done", state)

    def test_unconditional_orphan_journal_line_still_fires_when_unresolved(self):
        # the #449 never-silent floor line must survive regardless of
        # whether the owner resolves.
        logs, _orphan_done_set, _state = self._run_floor(
            "999999", _env_two_owners(), lambda *a, **k: "sent", mid="900004")
        self.assertTrue(any("reply orphaned" in line for line in logs), logs)

    def test_resolved_off_owner_suppressed_still_marks_done(self):
        # #716 terminal-marking semantics survive the new explicit-owner
        # routing: a resolved #710-OFF owner (zbynek) still gets one
        # confirmed "suppressed" decision, marked done so it never re-fires.
        calls = []

        def _fake(*a, **k):
            calls.append(k)
            return "suppressed"

        logs, orphan_done_set, state = self._run_floor(
            "700001", _env_two_owners(), _fake, mid="900005")
        self.assertEqual(calls[0].get("owner"), "zbynek")
        self.assertIn("900005", orphan_done_set)
        self.assertEqual(state.get("dorphan_done"), ["900005"])


if __name__ == "__main__":
    unittest.main()
