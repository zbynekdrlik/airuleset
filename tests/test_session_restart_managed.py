"""Tests for managed session-restart drop-in provisioning (#947 follow-on).

RED->GREEN: each test verifies the declarative drop-in render + install
lifecycle.  Uses tmpdir fixtures — no real systemd, no ssh.
"""

from cli_filedrop_watchdog import (
    render_session_restart_dropin,
    setup_session_restart_dropin,
)


# --------------------------------------------------------------------------- #
# render_session_restart_dropin
# --------------------------------------------------------------------------- #

class TestRenderDropin:
    """render_session_restart_dropin returns the managed drop-in content."""

    def test_content_has_environment_line(self):
        content = render_session_restart_dropin()
        assert "Environment=AIRULESET_SESSION_RESTART_ACTION=1" in content

    def test_content_has_service_section(self):
        content = render_session_restart_dropin()
        assert "[Service]" in content

    def test_content_has_managed_comment(self):
        content = render_session_restart_dropin()
        assert "Managed by airuleset" in content


# --------------------------------------------------------------------------- #
# setup_session_restart_dropin — default ON
# --------------------------------------------------------------------------- #

class TestSetupDropinDefaultOn:
    """With no opt-out marker, install renders the drop-in."""

    def test_creates_dropin(self, tmp_path):
        dropin = tmp_path / "dropin.conf"
        optout = tmp_path / "optout-marker"
        hand = tmp_path / "hand.conf"
        reloads = []
        result = setup_session_restart_dropin(
            dropin_path=dropin,
            optout_path=optout,
            hand_path=hand,
            daemon_reload_fn=lambda: reloads.append(1),
        )
        assert result is True
        assert dropin.exists()
        assert "AIRULESET_SESSION_RESTART_ACTION=1" in dropin.read_text()

    def test_daemon_reload_on_first_write(self, tmp_path):
        dropin = tmp_path / "dropin.conf"
        optout = tmp_path / "optout-marker"
        hand = tmp_path / "hand.conf"
        reloads = []
        setup_session_restart_dropin(
            dropin_path=dropin,
            optout_path=optout,
            hand_path=hand,
            daemon_reload_fn=lambda: reloads.append(1),
        )
        assert len(reloads) == 1

    def test_idempotent_no_reload(self, tmp_path):
        """Second call with identical content skips daemon-reload."""
        dropin = tmp_path / "dropin.conf"
        optout = tmp_path / "optout-marker"
        hand = tmp_path / "hand.conf"
        reloads = []

        def reload_fn():
            reloads.append(1)

        setup_session_restart_dropin(
            dropin_path=dropin, optout_path=optout,
            hand_path=hand, daemon_reload_fn=reload_fn,
        )
        assert len(reloads) == 1
        setup_session_restart_dropin(
            dropin_path=dropin, optout_path=optout,
            hand_path=hand, daemon_reload_fn=reload_fn,
        )
        # No second reload — content unchanged.
        assert len(reloads) == 1

    def test_deletes_hand_written_file(self, tmp_path):
        """The hand-written gk drop-in is superseded and deleted."""
        dropin = tmp_path / "dropin.conf"
        optout = tmp_path / "optout-marker"
        hand = tmp_path / "hand.conf"
        hand.write_text("[Service]\nEnvironment=AIRULESET_SESSION_RESTART_ACTION=1\n")
        reloads = []
        setup_session_restart_dropin(
            dropin_path=dropin, optout_path=optout,
            hand_path=hand, daemon_reload_fn=lambda: reloads.append(1),
        )
        assert not hand.exists(), "hand-written drop-in should be deleted"
        assert dropin.exists()


# --------------------------------------------------------------------------- #
# setup_session_restart_dropin — opt-out
# --------------------------------------------------------------------------- #

class TestSetupDropinOptOut:
    """With the opt-out marker present, install removes the drop-in."""

    def test_removes_dropin_when_optout_present(self, tmp_path):
        dropin = tmp_path / "dropin.conf"
        optout = tmp_path / "optout-marker"
        hand = tmp_path / "hand.conf"
        # Pre-create the drop-in (simulating a previous install).
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(render_session_restart_dropin())
        # Now create the opt-out marker.
        optout.write_text("")
        reloads = []
        result = setup_session_restart_dropin(
            dropin_path=dropin, optout_path=optout,
            hand_path=hand, daemon_reload_fn=lambda: reloads.append(1),
        )
        assert result is True
        assert not dropin.exists()
        assert len(reloads) == 1  # daemon-reload after removal

    def test_optout_noop_when_no_dropin(self, tmp_path):
        """Opt-out with no existing drop-in — no daemon-reload needed."""
        dropin = tmp_path / "dropin.conf"
        optout = tmp_path / "optout-marker"
        hand = tmp_path / "hand.conf"
        optout.write_text("")
        reloads = []
        result = setup_session_restart_dropin(
            dropin_path=dropin, optout_path=optout,
            hand_path=hand, daemon_reload_fn=lambda: reloads.append(1),
        )
        assert result is True
        assert not dropin.exists()
        assert len(reloads) == 0


# --------------------------------------------------------------------------- #
# effective_state helper
# --------------------------------------------------------------------------- #

class TestEffectiveState:
    """effective_session_restart_state reports source correctly."""

    def test_managed_on(self, tmp_path):
        from cli_filedrop_watchdog import effective_session_restart_state
        dropin = tmp_path / "dropin.conf"
        optout = tmp_path / "optout-marker"
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(render_session_restart_dropin())
        action, source = effective_session_restart_state(
            dropin_path=dropin, optout_path=optout)
        assert action == "on"
        assert source == "managed"

    def test_optout(self, tmp_path):
        from cli_filedrop_watchdog import effective_session_restart_state
        dropin = tmp_path / "dropin.conf"
        optout = tmp_path / "optout-marker"
        optout.write_text("")
        action, source = effective_session_restart_state(
            dropin_path=dropin, optout_path=optout)
        assert action == "off"
        assert source == "opt-out"

    def test_no_dropin_no_optout(self, tmp_path):
        """No drop-in, no opt-out marker → off (env default)."""
        from cli_filedrop_watchdog import effective_session_restart_state
        dropin = tmp_path / "dropin.conf"
        optout = tmp_path / "optout-marker"
        action, source = effective_session_restart_state(
            dropin_path=dropin, optout_path=optout)
        assert action == "off"
        assert source == "env-default"
