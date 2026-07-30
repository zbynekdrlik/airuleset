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


class TestDiscordNotifyCheckSshHint(TestCase):
    """Issue #151 (engineer's half): the install-time ssh one-liner must
    include `-i <identity>` when the CURRENT box's own REMOTE_HOSTS entry
    pins one (marek/david/simap on subdev all do) — a wrong-key attempt on
    subdev trips fail2ban and bans dev1 on every interface for an hour."""

    def _run(self, tmp):
        buf = io.StringIO()
        with mock.patch.object(airuleset, "CLAUDE_DIR", Path(tmp)):
            with contextlib.redirect_stdout(buf):
                airuleset.check_discord_notify_config()
        return buf.getvalue()

    def test_hint_includes_pinned_identity_for_simap(self):
        # simap@subdev pins ~/.secrets/gatekeeper_access_ed25519 in REMOTE_HOSTS.
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(airuleset, "_whoami", return_value="simap"):
                out = self._run(tmp)
        self.assertIn("-i ~/.secrets/gatekeeper_access_ed25519", out)
        self.assertIn("simap@100.118.174.27", out)
        self.assertNotIn("ssh <this-host>", out)

    def test_hint_includes_pinned_identity_for_david(self):
        # david@subdev pins the SAME identity — must not be dropped or swapped.
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(airuleset, "_whoami", return_value="david"):
                out = self._run(tmp)
        self.assertIn("-i ~/.secrets/gatekeeper_access_ed25519", out)
        self.assertIn("david@100.118.174.27", out)

    def test_hint_has_no_identity_flag_for_host_with_none(self):
        # montalu@subdev authorizes the DEFAULT key — no -i should be printed.
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(airuleset, "_whoami", return_value="montalu"):
                out = self._run(tmp)
        self.assertIn("ssh montalu@100.118.174.27", out)
        self.assertNotIn(" -i ", out)

    def test_hint_falls_back_to_placeholder_for_unrecognized_user(self):
        # Never guess wrong for a box that isn't in REMOTE_HOSTS at all.
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(airuleset, "_whoami", return_value="nobody-known"):
                out = self._run(tmp)
        self.assertIn("ssh <this-host>", out)
        self.assertNotIn(" -i ", out)


if __name__ == "__main__":
    main()
