"""#916 — `stop-check-prose-violations.sh` must block a turn that REPORTS
having posted to Odoo chatter / Discuss / message_post WITHOUT carrying
read-back evidence that 0 messages have escaped HTML tags.

The #915 PreToolUse hook guards the posting ACTION (blocks message_post
payloads with HTML tags lacking body_is_html). This Stop-side check guards
the REPORTING: when the assistant's message describes having performed Odoo
posting, it must carry a read-back evidence line proving the posted messages
were verified (e.g. "0 escaped messages", "body_is_html verified").

The check fires ONLY when the message mentions posting action + Odoo context
outside of quotes/code (MSG_MENTION), so a message merely DESCRIBING the
hook or a rule is not gated.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import sweep_session_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"


def _run(msg, sid=None):
    sid = sid or ("prosegate916-%s" % uuid.uuid4().hex[:10])
    payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
    p = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        timeout=300)
    sweep_session_files(sid)
    return p


def _blocked(p):
    return '"decision"' in p.stdout and '"block"' in p.stdout


# ---- BLOCK fixtures: a posting report WITHOUT read-back evidence ---- #

EN_POSTED_CHATTER = (
    "I posted the acceptance message to the client's project.task chatter "
    "on montalu PROD via message_post. The message includes the delivery "
    "summary and partner_ids for the contacts."
)

EN_SENT_DISCUSS = (
    "Sent the status update to the client Discuss thread via message_post. "
    "The thread is discuss.channel_288 on montalu PROD."
)

SK_ODOSLAL_CHATTER = (
    "Odoslal som akceptacnu spravu do chatteru project.task na montalu PROD "
    "cez message_post. Sprava obsahuje dodacie zhrnutie."
)

SK_NAPISAL_CHATTER = (
    "Napisal som klientovi do chatteru cez message_post na project.task "
    "na montalu PROD."
)


# ---- PASS fixtures: posting report WITH read-back evidence ---- #

EN_WITH_READBACK = (
    "I posted the acceptance message to the client's project.task chatter "
    "on montalu PROD via message_post. Read-back check: 0 escaped messages "
    "found in the posted content."
)

EN_WITH_ESCAPED_ZERO = (
    "Sent the status update via message_post to discuss.channel_288. "
    "Verified: 0 escaped HTML tags in the posted messages."
)

EN_WITH_BODY_IS_HTML = (
    "Posted to chatter via message_post with body_is_html=True verified. "
    "Content renders correctly on PROD."
)

SK_WITH_READBACK = (
    "Odoslal som spravu do chatteru cez message_post. "
    "Read-back: 0 escaped messages."
)

EN_WITH_UNVERIFIED = (
    "I posted the acceptance message to chatter via message_post. "
    "UNVERIFIED: cannot read back the posted message — no API access."
)


# ---- PASS fixtures: turns that do NOT involve Odoo posting ---- #

NO_POSTING_TECHNICAL = (
    "Implemented the new feature. CI is green. The hook "
    "block-odoo-message-post-without-html.sh checks for body_is_html "
    "in message_post payloads."
)

NO_POSTING_DISCUSSION = (
    "The `message_post` method requires `body_is_html=True` when the "
    "body contains HTML tags. See the #915 hook for details."
)

NO_POSTING_PLAIN_REPORT = (
    "Deployed v0.1.193. The version label shows correctly on the dashboard. "
    "All tests green, PR merged."
)

BACKTICK_MENTION = (
    "The hook checks `message_post` payloads for `body_is_html`. "
    "I posted the fix to the `chatter` module. Read-back not needed for code."
)


class TestOdooReadbackBlock(TestCase):
    """Posting reports WITHOUT read-back evidence must be blocked."""

    def test_en_posted_chatter_no_readback(self):
        p = _run(EN_POSTED_CHATTER)
        self.assertTrue(_blocked(p), p.stderr)

    def test_en_sent_discuss_no_readback(self):
        p = _run(EN_SENT_DISCUSS)
        self.assertTrue(_blocked(p), p.stderr)

    def test_sk_odoslal_chatter_no_readback(self):
        p = _run(SK_ODOSLAL_CHATTER)
        self.assertTrue(_blocked(p), p.stderr)

    def test_sk_napisal_chatter_no_readback(self):
        p = _run(SK_NAPISAL_CHATTER)
        self.assertTrue(_blocked(p), p.stderr)


class TestOdooReadbackPass(TestCase):
    """Posting reports WITH read-back evidence must pass."""

    def test_en_with_readback(self):
        p = _run(EN_WITH_READBACK)
        self.assertFalse(_blocked(p), p.stderr)

    def test_en_with_escaped_zero(self):
        p = _run(EN_WITH_ESCAPED_ZERO)
        self.assertFalse(_blocked(p), p.stderr)

    def test_en_with_body_is_html(self):
        p = _run(EN_WITH_BODY_IS_HTML)
        self.assertFalse(_blocked(p), p.stderr)

    def test_sk_with_readback(self):
        p = _run(SK_WITH_READBACK)
        self.assertFalse(_blocked(p), p.stderr)

    def test_en_with_unverified_escape(self):
        p = _run(EN_WITH_UNVERIFIED)
        self.assertFalse(_blocked(p), p.stderr)


class TestOdooReadbackNonPostingPass(TestCase):
    """Turns that do NOT involve Odoo posting must NOT trigger the check."""

    def test_technical_discussion_of_hook(self):
        p = _run(NO_POSTING_TECHNICAL)
        self.assertFalse(_blocked(p), p.stderr)

    def test_backtick_mention(self):
        p = _run(NO_POSTING_DISCUSSION)
        self.assertFalse(_blocked(p), p.stderr)

    def test_plain_report_no_odoo(self):
        p = _run(NO_POSTING_PLAIN_REPORT)
        self.assertFalse(_blocked(p), p.stderr)

    def test_backtick_posted_chatter(self):
        p = _run(BACKTICK_MENTION)
        self.assertFalse(_blocked(p), p.stderr)


class TestCompanionBullet(TestCase):
    """The 'chatter = helper only' doctrine bullet exists in handover-compose.md."""

    def test_chatter_helper_bullet_present(self):
        hc = ROOT / "skills" / "odoo-client-messaging" / "handover-compose.md"
        text = hc.read_text()
        self.assertIn("helper", text.lower())
        self.assertIn("915", text)

    def test_body_is_html_pointer(self):
        hc = ROOT / "skills" / "odoo-client-messaging" / "handover-compose.md"
        text = hc.read_text()
        self.assertIn("body_is_html", text)


if __name__ == "__main__":
    main()
