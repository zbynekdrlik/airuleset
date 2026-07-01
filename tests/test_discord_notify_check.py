"""Locks the install-time Discord-notify config guard (2026-07-01).

Incident: the gatekeeper VPS sent NO Discord notifications. Root cause — its
`~/.claude/channels/discord/.env` (bot token + per-owner channels/mentions) was
never wired when the host was added; the `.env` is LOCAL and NOT git-deployed, so
`install` cannot carry it, and every notify call fail-safed to a SILENT no-op.
`check_discord_notify_config()` now warns LOUDLY at install time so the gap is
visible instead of discovered weeks later — and it must NEVER print the token.
"""

import contextlib
import io
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main, mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


class TestDiscordNotifyCheck(TestCase):
    def _run(self, tmp):
        buf = io.StringIO()
        with mock.patch.object(airuleset, "CLAUDE_DIR", Path(tmp)):
            with contextlib.redirect_stdout(buf):
                airuleset.check_discord_notify_config()
        return buf.getvalue()

    def test_warns_when_env_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(tmp)
        self.assertIn("Discord notify DISABLED", out)
        self.assertIn("silently NOT send", out)

    def test_warns_when_token_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "channels" / "discord"
            d.mkdir(parents=True)
            (d / ".env").write_text(
                "DISCORD_BOT_TOKEN=\nDISCORD_NOTIFICATION_CHANNEL_ID=123\n"
            )
            out = self._run(tmp)
        self.assertIn("DISCORD_BOT_TOKEN is empty", out)

    def test_ok_when_token_present_and_never_printed(self):
        secret = "abc.def.ghijklmnop"
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "channels" / "discord"
            d.mkdir(parents=True)
            (d / ".env").write_text(f"DISCORD_BOT_TOKEN={secret}\n")
            out = self._run(tmp)
        self.assertIn("configured", out)
        self.assertNotIn(secret, out)  # the token value must NEVER be printed


if __name__ == "__main__":
    main()
