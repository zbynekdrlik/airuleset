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


# ==================================================================== #
# Unit 2 — intake filing gate: a Scope-gate: user-request ticket quoting a
# mail.message / msg id must cite the 👷 (Ack-reaction) intake reaction.
# ==================================================================== #

import os                                    # noqa: E402
import tempfile                              # noqa: E402


def _fake_empty_gh(tmpdir):
    """A `gh` stub whose `issue list` prints `[]` (near-dup inert) and every
    other call exits 1 — keeps the classifier fully hermetic."""
    bin_dir = os.path.join(tmpdir, "fakebin")
    os.makedirs(bin_dir, exist_ok=True)
    gh = os.path.join(bin_dir, "gh")
    with open(gh, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\n"
                 "import sys, json\n"
                 "a = sys.argv[1:]\n"
                 "if len(a) >= 2 and a[0] == 'issue' and a[1] == 'list':\n"
                 "    print(json.dumps([])); sys.exit(0)\n"
                 "sys.exit(1)\n")
    os.chmod(gh, 0o755)
    return bin_dir


def _classify(body, tmpdir):
    """Drive gates.filing.__main__.classify_command over a single
    `gh issue create -R zbynekdrlik/odoo-erp` filing whose body is `body`."""
    import importlib
    mod = importlib.import_module("gates.filing.__main__")
    body_file = os.path.join(tmpdir, "body.md")
    with open(body_file, "w", encoding="utf-8") as fh:
        fh.write(body)
    cmd = ("gh issue create -R zbynekdrlik/odoo-erp -t 'Klient: oprava filtra' "
           "-F %s" % body_file)
    log_path = os.path.join(tmpdir, "scope-gate.log")
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = _fake_empty_gh(tmpdir) + os.pathsep + old_path
    try:
        return mod.classify_command(cmd, "sid-1027", tmpdir, tmpdir, log_path,
                                    unattended=False)
    finally:
        os.environ["PATH"] = old_path


_INTAKE_BODY_NO_ACK = (
    "Klient Alena napísal do vlákna Zákaznícky portál pripomienku k filtru.\n"
    "Origin: mail.message 3122 — discuss.channel_288.\n\n"
    "Scope-gate: user-request\n"
    "Dedup-checked: no existing ticket for this filter bug\n"
)

_INTAKE_BODY_WITH_ACK = (
    "Klient Alena napísal do vlákna Zákaznícky portál pripomienku k filtru.\n"
    "Origin: mail.message 3122 — discuss.channel_288.\n"
    "Ack-reaction: msg 3122 👷\n\n"
    "Scope-gate: user-request\n"
    "Dedup-checked: no existing ticket for this filter bug\n"
)

_INTAKE_BODY_NON_CLIENT = (
    "Refactor the internal cache module — no client message involved.\n\n"
    "Scope-gate: user-request\n"
    "Dedup-checked: no existing ticket\n"
)


class TestIntakeFilingGate(TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _reasons(self, results):
        return [r[2] for r in results if r[0] == "BLOCK"]

    def test_client_msg_filing_without_ack_is_blocked(self):
        """A user-request ticket quoting a mail.message with no Ack-reaction
        citation is BLOCKED."""
        results = _classify(_INTAKE_BODY_NO_ACK, self.tmp)
        blocks = self._reasons(results)
        self.assertTrue(
            any("reaction" in r.lower() for r in blocks),
            "expected an intake-reaction block; got %r" % (results,))

    def test_client_msg_filing_with_ack_not_blocked_by_intake(self):
        """The SAME filing WITH an Ack-reaction citation is not blocked by the
        intake rule (it passes the gate)."""
        results = _classify(_INTAKE_BODY_WITH_ACK, self.tmp)
        blocks = self._reasons(results)
        self.assertFalse(
            any("reaction" in r.lower() for r in blocks),
            "intake rule must not fire when Ack-reaction is cited; got %r"
            % (results,))

    def test_non_client_user_request_not_blocked_by_intake(self):
        """A user-request ticket with NO client-message origin never trips the
        intake rule."""
        results = _classify(_INTAKE_BODY_NON_CLIENT, self.tmp)
        blocks = self._reasons(results)
        self.assertFalse(
            any("reaction" in r.lower() for r in blocks),
            "non-client user-request must not trip the intake rule; got %r"
            % (results,))


# ==================================================================== #
# Unit 2 — doctrine: the intake reaction step is wired into the compose
# doctrine + the state machine.
# ==================================================================== #

class TestIntakeDoctrine(TestCase):

    def test_handover_carries_intake_reaction_step(self):
        t = HANDOVER.read_text(encoding="utf-8")
        self.assertIn(WORKER, t)
        low = t.lower()
        self.assertIn("message_reaction_guarded", t)
        # The step: react the moment a client message is picked up, before
        # filing / dispatching.
        self.assertIn("intake", low)

    def test_skill_state_machine_names_intake_reaction(self):
        t = SKILL.read_text(encoding="utf-8")
        self.assertIn(WORKER, t)
        self.assertIn("ack-reaction.md", t)


if __name__ == "__main__":
    main()
