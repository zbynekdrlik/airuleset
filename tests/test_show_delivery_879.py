"""#879 — a `secret show` one-shot URL delivered to the owner must go
through the ❓ marker machinery (pings the phone, sits in U), never as
a bare line in a ✅ report or status prose.

RED (proven against main 64e017ff): a message with `secret show` CLI
output (``endpoint-ttl=...`` + ``jednorazové zobrazenie``) and a URL,
ending ``✅ DONE``, passes the gate clean — the hook has no check.
The enforcement tests below fail on that hook; the control tests pass
both before and after.
"""

import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"

# --- Fixtures ---

# The incident shape: secret show output pasted into a ✅ report.
SECRET_SHOW_IN_REPORT = (
    "PIN pre kiosk je pripravený.\n\n"
    "name=kiosk_pin  endpoint-ttl=3600s  (jednorazové zobrazenie)\n\n"
    "URL: https://100.104.8.125:9871/a1b2c3d4e5f6/\n\n"
    "✅ DONE: PIN pripravený na vyzdvihnutie"
)

# The correct shape: same output inside a ❓ block.
SECRET_SHOW_IN_QUESTION = (
    "**Otázka — projekt airuleset (konfiguračný systém):**\n\n"
    "Vygeneroval som jednorazovú URL na vyzdvihnutie kiosk PIN-u.\n"
    "URL je platná 1 hodinu alebo do prvého otvorenia.\n\n"
    "name=kiosk_pin  endpoint-ttl=3600s  (jednorazové zobrazenie)\n\n"
    "URL: https://100.104.8.125:9871/a1b2c3d4e5f6/\n\n"
    "Ak URL už nefunguje, odpíš — vygenerujem novú.\n\n"
    "❓ NEEDS YOU: vyzdvihni si PIN na jednorazovej URL vyššie"
)

# A doctrine discussion quoting the pattern — no URL → must pass.
DOCTRINE_DISCUSSION = (
    "The `secret show` output contains `endpoint-ttl=600s` and "
    "`jednorazové zobrazenie` — this is the show-only discriminator.\n\n"
    "⏳ WORKING: implementing the hook check"
)

# A `secret request` output (different shape) — must pass even without ❓.
SECRET_REQUEST_OUTPUT = (
    "Credential endpoint ready.\n"
    "endpoint-ttl=600s  keep=900s\n"
    "URL: https://100.104.8.125:9870/x9y8z7/\n\n"
    "✅ DONE: secret request spustený, URL poslaná"
)


def _run_hook(msg, *, session_id=None):
    """Drive stop-check-prose-violations.sh with a minimal Stop payload."""
    sid = session_id or ("test-879-" + uuid.uuid4().hex[:12])
    payload = json.dumps({
        "type": "Stop",
        "session_id": sid,
        "session": {
            "cwd": str(ROOT),
            "account": {"email": "test@test.local"},
        },
        "transcript_path": "/dev/null",
        "tool_input": {},
        "conversation": [
            {"role": "assistant", "message": msg},
        ],
    })
    with tempfile.TemporaryDirectory() as tmp_home:
        env = {
            **os.environ,
            "HOME": tmp_home,
            "CLAUDE_SESSION_ID": sid,
        }
        r = subprocess.run(
            ["bash", str(HOOK)],
            input=payload, capture_output=True, text=True,
            env=env, timeout=30,
        )
    return r


class TestSecretShowDelivery(unittest.TestCase):
    """#879 — secret show URL without ❓ marker is blocked."""

    def test_red_show_output_in_report_blocked(self):
        """A secret show URL in a ✅ report (no ❓) → hard block."""
        r = _run_hook(SECRET_SHOW_IN_REPORT)
        self.assertIn("secret show", r.stderr.lower(),
                       "expected a secret-show block reason on stderr")
        self.assertNotEqual(r.returncode, 0,
                            "hook must block (exit 2) a secret show URL "
                            "delivered without ❓")

    def test_green_show_output_in_question_passes(self):
        """A secret show URL inside a ❓ block → passes."""
        r = _run_hook(SECRET_SHOW_IN_QUESTION)
        # The hook should not block this — rc 0.
        self.assertEqual(r.returncode, 0,
                         f"hook must pass when ❓ marker present; "
                         f"stderr={r.stderr[:300]}")

    def test_green_doctrine_discussion_passes(self):
        """Quoting the discriminator in prose with no URL → passes."""
        r = _run_hook(DOCTRINE_DISCUSSION)
        self.assertEqual(r.returncode, 0,
                         f"hook must pass doctrine discussion; "
                         f"stderr={r.stderr[:300]}")

    def test_green_secret_request_output_passes(self):
        """`secret request` output (no jednorazové) → passes."""
        r = _run_hook(SECRET_REQUEST_OUTPUT)
        self.assertEqual(r.returncode, 0,
                         f"hook must pass secret request output; "
                         f"stderr={r.stderr[:300]}")


if __name__ == "__main__":
    unittest.main()
