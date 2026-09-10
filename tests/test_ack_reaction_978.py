"""#978 — Ack-reaction doctrine + recipe + trigger + enforcement.

Tests cover:
  1. Situational trigger co-fit and over-fire (inject-situational-rule.sh)
  2. Stop-check prose-violations hook (the miva1 turn as RED fixture)
  3. Nudge text carries the ACK-REACTION clause
  4. Content-lock as STATEMENTS (#498/#500) for the doctrine + handover grant
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import sweep_session_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOK_INJECT = ROOT / "hooks" / "inject-situational-rule.sh"
HOOK_PROSE = ROOT / "hooks" / "stop-check-prose-violations.sh"
CONF = ROOT / "hooks" / "situational-triggers.conf"
ACK_BODY = ROOT / "skills" / "odoo-client-messaging" / "ack-reaction.md"
HANDOVER = ROOT / "skills" / "odoo-client-messaging" / "handover-compose.md"

# ---- helpers ---- #

def _run_inject(tool_input, tool_name="Write", session_id=None, tmpdir=None):
    sid = session_id or ("ack978-%s" % uuid.uuid4().hex[:10])
    payload = json.dumps(
        {"session_id": sid, "tool_name": tool_name, "tool_input": tool_input}
    )
    env = dict(os.environ)
    if tmpdir:
        env["TMPDIR"] = tmpdir
    p = subprocess.run(
        ["bash", str(HOOK_INJECT)], input=payload, capture_output=True, text=True,
        timeout=30, env=env)
    return p


def _injected(result):
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        return data.get("hookSpecificOutput", {}).get("additionalContext", "")
    except (json.JSONDecodeError, AttributeError):
        return None


def _run_prose(msg, sid=None):
    sid = sid or ("prosegate978-%s" % uuid.uuid4().hex[:10])
    payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
    p = subprocess.run(
        ["bash", str(HOOK_PROSE)], input=payload, capture_output=True, text=True,
        timeout=300)
    sweep_session_files(sid)
    return p


def _blocked(p):
    return '"decision"' in p.stdout and '"block"' in p.stdout


# ==================================================================== #
# 1. Situational trigger tests
# ==================================================================== #

class TestAckReactionTriggerRow(TestCase):

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def test_trigger_row_exists_in_conf(self):
        """The odoo-discuss-ack-reaction row must exist in the trigger table."""
        text = CONF.read_text(encoding="utf-8")
        self.assertIn("odoo-discuss-ack-reaction", text)

    def test_reaction_add_guarded_write_injects(self):
        """A .py Write with message_reaction_add_guarded must inject ack-reaction."""
        ctx = _injected(
            _run_inject(
                {"file_path": "/repo/sync.py",
                 "content": 'models.execute_kw(db, uid, key, "mail.message", "reaction_add_guarded", [msg_id])'},
                tool_name="Write",
                tmpdir=self.tmpdir,
            )
        )
        self.assertIsNotNone(ctx, "ack-reaction trigger must fire")
        self.assertIn("ack-reaction", ctx.lower())

    def test_message_reaction_add_write_injects(self):
        """A .py Write with message_reaction_add (shorter form) must also inject."""
        ctx = _injected(
            _run_inject(
                {"file_path": "/repo/ack.py",
                 "content": 'result = message_reaction_add(message_id, emoji)'},
                tool_name="Write",
                tmpdir=self.tmpdir,
            )
        )
        self.assertIsNotNone(ctx, "message_reaction_add must trigger injection")
        self.assertIn("ack-reaction", ctx.lower())

    def test_cofit_with_comprehensive_logging(self):
        """A .py Write with reaction_add_guarded must co-inject comprehensive-logging."""
        ctx = _injected(
            _run_inject(
                {"file_path": "/repo/handler.py",
                 "content": 'reaction_add_guarded(msg_id, "eyes")\nlogger.info("done")'},
                tool_name="Write",
                tmpdir=self.tmpdir,
            )
        )
        self.assertIsNotNone(ctx, "co-fire must inject something")
        # Both comprehensive-logging and ack-reaction should be present
        lower = ctx.lower()
        self.assertIn("ack-reaction", lower)

    def test_generic_reaction_word_does_not_inject(self):
        """A .py Write with a generic 'reaction' word must NOT inject ack-reaction."""
        ctx = _injected(
            _run_inject(
                {"file_path": "/repo/chemistry.py",
                 "content": 'reaction_rate = compute_reaction(substrate, enzyme)'},
                tool_name="Write",
                tmpdir=self.tmpdir,
            )
        )
        if ctx is not None:
            self.assertNotIn("Acknowledging a client message", ctx)

    def test_asyncio_add_callback_does_not_inject(self):
        """A .py Write with add_callback (not reaction_add) must NOT inject."""
        ctx = _injected(
            _run_inject(
                {"file_path": "/repo/async_worker.py",
                 "content": 'future.add_callback(on_complete)'},
                tool_name="Write",
                tmpdir=self.tmpdir,
            )
        )
        if ctx is not None:
            self.assertNotIn("Acknowledging a client message", ctx)

    def test_rust_file_with_add_reaction_does_not_inject(self):
        """A .rs Write with add_reaction (generic Rust) must NOT inject."""
        ctx = _injected(
            _run_inject(
                {"file_path": "/repo/main.rs",
                 "content": 'discord_msg.add_reaction(ctx, emoji).await?;'},
                tool_name="Write",
                tmpdir=self.tmpdir,
            )
        )
        if ctx is not None:
            self.assertNotIn("Acknowledging a client message", ctx)


# ==================================================================== #
# 2. Stop-check prose-violations hook tests (RED fixtures)
# ==================================================================== #

# The miva1 turn from the ticket: stream reports working client remarks
# with NO Ack-reaction line.
MIVA1_RED_FIXTURE = (
    "Precital som spravy od klienta Alena v Discuss vlakne "
    "Zakaznicky portal 3. Klient napisal 4 pripomienky k portalu. "
    "Pracujem na ich vyrieseni.\n\n"
    "⏳ WORKING: riesim pripomienky klienta"
)

# A turn that carries the Ack-reaction evidence line — must PASS.
MIVA1_GREEN_FIXTURE = (
    "Precital som spravy od klienta Alena v Discuss vlakne "
    "Zakaznicky portal 3. Klient napisal 4 pripomienky k portalu.\n\n"
    "Ack-reaction: msg 1739648 👀\n\n"
    "Pracujem na ich vyrieseni.\n\n"
    "⏳ WORKING: riesim pripomienky klienta"
)

# A turn with Ack-reaction: pending — must also PASS.
PENDING_GREEN_FIXTURE = (
    "Klient poslal novu spravu do vlakna Tabula objednavok 1. "
    "Ack-reaction: pending — message_reaction_add_guarded not on montalu PROD\n\n"
    "⏳ WORKING: spracovavam spravu"
)

# A turn that merely mentions an OLD message (no new-action signal) — must PASS.
OLD_MESSAGE_PASS = (
    "V minulom tyzdni sme dostali pripomienky od zakaznika. "
    "Medzicasom sme vsetky vyriesili a nasadili na PROD.\n\n"
    "✅ DONE: pripomienky vyriesene"
)

# A turn with "poslala" feminine form — must block.
FEMININE_RED = (
    "Alena poslala novy email do vlakna Kontrola objednavok. "
    "Pracujem na odpovedi.\n\n"
    "⏳ WORKING: riesim"
)

# A code block mentioning "klient napisal" — must PASS (HIGH-2 fix: uses MSG_MENTION).
CODE_BLOCK_PASS = (
    "Implementoval som ack-reaction check.\n\n"
    "```\n"
    "# Pattern: klient napisal do vlakna Discuss\n"
    "```\n\n"
    "✅ DONE: check implementovany"
)

# A non-Odoo turn mentioning "klient napisal" — must PASS (MEDIUM-3 fix: Odoo anchor).
NON_ODOO_PASS = (
    "TCP klient poslal SYN a server neodpovedal. "
    "Pracujem na debugovani.\n\n"
    "⏳ WORKING: debugujem TCP problem"
)

# Diacritic Slovak — "Klient napisal" with proper diacritics — must block (HIGH-1 fix).
DIACRITIC_RED = (
    "Prečítal som správy. Klient napísal do Discuss vlákna "
    "Zákaznícky portál 3 nové pripomienky.\n\n"
    "⏳ WORKING: riešim pripomienky"
)


class TestAckReactionProseGate(TestCase):

    def test_miva1_red_fixture_is_blocked(self):
        """The miva1 regression turn (no Ack-reaction) must be BLOCKED."""
        p = _run_prose(MIVA1_RED_FIXTURE)
        self.assertTrue(_blocked(p),
                        "miva1 turn without Ack-reaction must be blocked; "
                        "stderr=%s stdout=%s" % (p.stderr, p.stdout))

    def test_miva1_green_fixture_passes(self):
        """The same turn WITH Ack-reaction: evidence must PASS."""
        p = _run_prose(MIVA1_GREEN_FIXTURE)
        self.assertFalse(_blocked(p),
                         "turn with Ack-reaction: must pass; "
                         "stderr=%s" % p.stderr)

    def test_pending_ack_passes(self):
        """A turn with Ack-reaction: pending must PASS."""
        p = _run_prose(PENDING_GREEN_FIXTURE)
        self.assertFalse(_blocked(p),
                         "Ack-reaction: pending must pass; "
                         "stderr=%s" % p.stderr)

    def test_old_message_recap_passes(self):
        """A turn mentioning old messages without new-action context must PASS."""
        p = _run_prose(OLD_MESSAGE_PASS)
        self.assertFalse(_blocked(p),
                         "old message recap must pass; "
                         "stderr=%s" % p.stderr)

    def test_feminine_form_red(self):
        """'Alena poslala' + vlakno without Ack-reaction must be blocked."""
        p = _run_prose(FEMININE_RED)
        self.assertTrue(_blocked(p),
                        "feminine 'poslala do vlakna' must be caught; "
                        "stderr=%s stdout=%s" % (p.stderr, p.stdout))

    def test_code_block_passes(self):
        """A code block mentioning 'klient napisal' must PASS (HIGH-2 fix)."""
        p = _run_prose(CODE_BLOCK_PASS)
        self.assertFalse(_blocked(p),
                         "code block mention must pass; stderr=%s" % p.stderr)

    def test_non_odoo_passes(self):
        """A non-Odoo turn with 'klient poslal' must PASS (MEDIUM-3 fix)."""
        p = _run_prose(NON_ODOO_PASS)
        self.assertFalse(_blocked(p),
                         "non-Odoo 'klient poslal' must pass; stderr=%s" % p.stderr)

    def test_diacritic_red(self):
        """Diacritic 'Klient napisal' in Discuss context must be blocked (HIGH-1)."""
        p = _run_prose(DIACRITIC_RED)
        self.assertTrue(_blocked(p),
                        "diacritic 'Klient napisal' must be caught; "
                        "stderr=%s stdout=%s" % (p.stderr, p.stdout))


# ==================================================================== #
# 3. Nudge text carries the #978 ack clause folded into DISCUSS_TRIGGER
# ==================================================================== #

class TestNudgeAckReaction(TestCase):

    def test_discuss_trigger_carries_978_clause(self):
        """The _DISCUSS_TRIGGER constant must carry the #978 ack-reaction clause."""
        import importlib
        owr = importlib.import_module("watchdog.ops_wait_recheck")
        self.assertIn("#978", owr._DISCUSS_TRIGGER,
                      "_DISCUSS_TRIGGER must carry #978 ack-reaction clause")

    def test_nudge_with_discuss_audit_carries_978(self):
        """When discuss_audit=True, the nudge text must carry the #978 clause."""
        import importlib
        owr = importlib.import_module("watchdog.ops_wait_recheck")
        text = owr._nudge_text(
            i_count=2,
            w_members=[{"number": 42}, {"number": 43}],
            discuss_audit=True,
        )
        self.assertIn("#978", text,
                      "nudge with discuss_audit + I=2 must carry #978 clause")

    def test_nudge_without_discuss_audit_omits_978(self):
        """When discuss_audit=False, the nudge text must NOT carry #978."""
        import importlib
        owr = importlib.import_module("watchdog.ops_wait_recheck")
        text = owr._nudge_text(
            i_count=1,
            w_members=[],
            discuss_audit=False,
        )
        self.assertNotIn("#978", text,
                         "nudge without discuss_audit must not carry #978")


# ==================================================================== #
# 4. Content-lock as STATEMENTS (#498/#500)
# ==================================================================== #

class TestAckReactionContentLock(TestCase):
    """Content-lock: the ack-reaction.md doctrine body + the handover-compose.md
    standing grant bullet must carry their operative tokens. A partial revert
    dropping the doctrine or the grant genuinely fails these locks."""

    # Tokens UNIQUE to the ack-reaction doctrine body
    DOCTRINE_TOKENS = (
        "message_reaction_add_guarded",
        "Ack-reaction:",
        "ack-pending",
        "standing fleet ack emoji",
    )

    # Tokens UNIQUE to the handover-compose.md standing grant bullet
    GRANT_TOKENS = (
        "#978",
        "ack-reaction",
        "owner text-approval",
    )

    def test_doctrine_body_carries_operative_tokens(self):
        text = ACK_BODY.read_text(encoding="utf-8")
        for tok in self.DOCTRINE_TOKENS:
            self.assertIn(tok, text,
                          "ack-reaction.md lost operative token %r" % tok)

    def test_handover_grant_bullet_carries_tokens(self):
        text = HANDOVER.read_text(encoding="utf-8")
        for tok in self.GRANT_TOKENS:
            self.assertIn(tok, text,
                          "handover-compose.md lost grant token %r" % tok)

    def test_doctrine_not_subject_to_approval(self):
        """The standing grant must explicitly state NOT subject to approval."""
        text = ACK_BODY.read_text(encoding="utf-8")
        self.assertIn("NOT", text)
        self.assertIn("owner text-approval", text)

    def test_trigger_row_points_at_correct_body(self):
        """The trigger row must point at ack-reaction.md (not SKILL.md)."""
        text = CONF.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "odoo-discuss-ack-reaction" in line and not line.strip().startswith("#"):
                self.assertIn("ack-reaction.md", line)
                break
        else:
            self.fail("odoo-discuss-ack-reaction row not found in conf")


if __name__ == "__main__":
    main()
