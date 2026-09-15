"""#1027 + #1033 — fleet worker-reaction on client-message intake, the
per-owner-configurable ack emoji, and the no-interim-workaround gate.

Three units, one lane:
  1. ACK EMOJI — the fleet ack reaction becomes the WORKER 👷 (U+1F477) via the
     single config key `ack_reaction_emoji` (per-owner/per-tenant overridable,
     fleet default 👷, legacy 👀 still selectable). Read by every consumer:
     the resolver module, the two skill docs, the prose Stop hook, and the
     watchdog nudge.
  2. INTAKE — the compose doctrine + state machine require reacting 👷 the
     moment a client message is picked up, and the filing gate cites it.
  3. NO INTERIM WORKAROUND — `gates.questionscope` blocks a ❓ approving a
     client message with workaround phrasing while the referenced #N has an
     open implementation lane.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKER = "\U0001F477"   # 👷
EYES = "\U0001F440"     # 👀

ACK_BODY = ROOT / "skills" / "odoo-client-messaging" / "ack-reaction.md"
ADD_RECIPE = ROOT / "skills" / "odoo-discuss-xmlrpc" / "add-reaction.md"
PROSE_HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"
HANDOVER = ROOT / "skills" / "odoo-client-messaging" / "handover-compose.md"
SKILL = ROOT / "skills" / "odoo-client-messaging" / "SKILL.md"


# ==================================================================== #
# Unit 1 — ack_reaction_emoji config key
# ==================================================================== #

class TestAckEmojiConfigKey(TestCase):

    def setUp(self):
        import importlib
        import ack_reaction
        importlib.reload(ack_reaction)
        self.mod = ack_reaction

    def test_fleet_default_is_worker(self):
        """The fleet default ack emoji is the WORKER 👷 (owner directive)."""
        self.assertEqual(self.mod.ACK_REACTION_EMOJI_DEFAULT, WORKER)
        self.assertEqual(self.mod.ack_reaction_emoji(), WORKER)

    def test_eyes_still_selectable(self):
        """The legacy #978 eyes 👀 stays selectable (in the selectable set)."""
        self.assertIn(WORKER, self.mod.SELECTABLE_ACK_EMOJIS)
        self.assertIn(EYES, self.mod.SELECTABLE_ACK_EMOJIS)

    def test_per_owner_override(self):
        """A per-owner override wins over the fleet default; unknown → default."""
        self.mod.ACK_REACTION_EMOJI_BY_OWNER["someowner"] = EYES
        try:
            self.assertEqual(self.mod.ack_reaction_emoji(owner="someowner"), EYES)
            self.assertEqual(self.mod.ack_reaction_emoji(owner="SomeOwner"), EYES)
            self.assertEqual(self.mod.ack_reaction_emoji(owner="nobody"), WORKER)
        finally:
            self.mod.ACK_REACTION_EMOJI_BY_OWNER.pop("someowner", None)

    def test_per_tenant_override_beats_owner(self):
        """A per-tenant override has the highest precedence."""
        self.mod.ACK_REACTION_EMOJI_BY_OWNER["o"] = EYES
        self.mod.ACK_REACTION_EMOJI_BY_TENANT["t"] = WORKER
        try:
            self.assertEqual(
                self.mod.ack_reaction_emoji(owner="o", tenant="t"), WORKER)
        finally:
            self.mod.ACK_REACTION_EMOJI_BY_OWNER.pop("o", None)
            self.mod.ACK_REACTION_EMOJI_BY_TENANT.pop("t", None)

    def test_resolver_never_raises(self):
        """A bad owner/tenant value never raises — falls back to the default."""
        self.assertEqual(self.mod.ack_reaction_emoji(owner=123), WORKER)
        self.assertEqual(self.mod.ack_reaction_emoji(tenant=object()), WORKER)


class TestAckEmojiConsumers(TestCase):
    """Every consumer reads/documents the WORKER default (single source)."""

    def test_ack_doctrine_uses_worker(self):
        t = ACK_BODY.read_text(encoding="utf-8")
        self.assertIn(WORKER, t)
        # Content-lock tokens kept from #978 (the sibling test asserts these too).
        for tok in ("standing fleet ack emoji", "Ack-reaction:", "ack-pending"):
            self.assertIn(tok, t)

    def test_ack_doctrine_states_config_key_and_escalation(self):
        t = ACK_BODY.read_text(encoding="utf-8")
        self.assertIn("ack_reaction_emoji", t)
        # The escalation rule: an explicit owner instruction about HIS channel
        # overrides the fleet default; the stream escalates the conflict.
        low = t.lower()
        self.assertIn("escalat", low)
        self.assertIn("owner", low)

    def test_add_recipe_uses_worker(self):
        t = ADD_RECIPE.read_text(encoding="utf-8")
        self.assertIn(WORKER, t)

    def test_prose_hook_guidance_uses_worker(self):
        t = PROSE_HOOK.read_text(encoding="utf-8")
        self.assertIn(WORKER, t)

    def test_watchdog_nudge_uses_worker(self):
        import importlib
        owr = importlib.import_module("watchdog.ops_wait_recheck")
        importlib.reload(owr)
        self.assertIn(WORKER, owr._DISCUSS_TRIGGER)
        self.assertIn("#978", owr._DISCUSS_TRIGGER)


if __name__ == "__main__":
    main()
