"""#132 — this repo must not be able to exit, restart or kill a Claude session.

On 2026-07-28 the api-watchdog typed `/exit` into a pane the user was actively
working in (pz-server), twice in three minutes, and relaunched his session. The
sender was `watchdog._restart_pane`, driven by three jobs: 12 `model_reconcile`,
18 `hooks_reconcile`, 23 `model_generation_reconcile`.

The measured verdict (see the ticket) removed all three:

  * job 18's premise is FALSE — Claude Code re-reads its hook configuration per
    event, so a hook deployed mid-session already takes effect and no restart is
    needed. Proven bidirectionally in an isolated scratch session: an entry added
    to settings.json mid-session fired immediately, and the same entry removed
    mid-session stopped firing immediately, with a control arm proving the event
    happened on every turn.
  * job 23 compared `transcript_first_model` (an API model id, e.g.
    `claude-opus-5`) against MANAGED_MODEL (a CLI alias, `claude-opus-5[1m]`).
    Those can never be equal, so its restart condition was permanently true for
    every session on every box, and the restart itself minted a new session id
    that defeated its own dedup — an unbounded restart engine.
  * job 12's remedy violates `model-awareness.md` ("the main session runs
    whatever the user set via /model — their call alone, never auto-downtier
    it") by restarting a session off a model the user deliberately chose.

The guard is therefore STRUCTURAL, not a prose rule and not a cleverer set of
pane heuristics: the capability is gone, and these tests fail if anything
reintroduces it.
"""

import re
import subprocess
import unittest
import unittest.mock
from pathlib import Path

import airuleset
import watchdog as wd

REPO = Path(__file__).resolve().parent.parent

# tmux payloads that END a Claude session rather than typing into it. `/exit` is
# the slash command; C-c / C-d interrupt or EOF the process; the three tmux
# subcommands destroy or replace the pane outright.
_SESSION_ENDING_PAYLOADS = (r"/exit", r"C-c", r"C-d")
_PANE_DESTROYING_SUBCOMMANDS = ("kill-session", "kill-pane", "kill-server",
                                "respawn-pane", "respawn-window")


def _code_files():
    """Every tracked Python/shell file that could actually SEND something —
    `tests/` excluded because this very file names the banned payloads (a lock
    test that greps for a literal will otherwise match its own prose), and
    docs/rules excluded because prose describing the removed mechanism is
    history, not capability."""
    out = subprocess.run(["git", "ls-files", "-z", "*.py", "*.sh"],
                         cwd=REPO, capture_output=True, text=True, check=True)
    for rel in out.stdout.split("\0"):
        if not rel or rel.startswith("tests/"):
            continue
        p = REPO / rel
        if p.is_file():
            yield rel, p.read_text(encoding="utf-8", errors="replace")


class TestNoSessionEndingKeystroke(unittest.TestCase):
    """No code path may hand a session-ending payload to `tmux send-keys`."""

    def test_no_code_file_sends_a_session_ending_payload(self):
        # Whitespace-collapsed so a call split across lines is still one match,
        # and anchored on `send-keys` APPEARING FIRST with the payload close
        # behind it — prose that merely mentions `/exit` never has a send-keys
        # call 80 characters ahead of it.
        offenders = []
        for rel, text in _code_files():
            flat = re.sub(r"\s+", " ", text)
            for payload in _SESSION_ENDING_PAYLOADS:
                for m in re.finditer(
                        r"send-keys.{0,80}?" + re.escape(payload), flat):
                    offenders.append("%s: ...%s..." % (rel, m.group(0)[:90]))
        self.assertEqual(
            offenders, [],
            "a code path sends a session-ending payload into a pane (#132):\n"
            + "\n".join(offenders))

    def test_no_code_file_destroys_or_respawns_a_pane(self):
        offenders = []
        for rel, text in _code_files():
            for sub in _PANE_DESTROYING_SUBCOMMANDS:
                if sub in text:
                    offenders.append("%s: %s" % (rel, sub))
        self.assertEqual(
            offenders, [],
            "a code path destroys or respawns a tmux pane (#132):\n"
            + "\n".join(offenders))

    def test_the_restart_machinery_is_gone(self):
        """`_restart_pane` and its three callers must not come back. Named
        individually so a reintroduction says WHICH one returned."""
        for name in ("_restart_pane", "_wait_for_shell_returns",
                     "_wait_for_relaunch", "_wait_for_idle_after_dialog",
                     "RELAUNCH_CMD", "model_reconcile", "hooks_reconcile",
                     "model_generation_reconcile", "_hooks_config_hash",
                     "hooks_settings_path", "_reconcile_candidate_panes"):
            self.assertFalse(
                hasattr(wd, name),
                "watchdog.%s is back — it was removed in #132 because it typed "
                "/exit into a live user session" % name)

    def test_run_once_never_sends_keystrokes_a_dry_run_would_hide(self):
        """A belt-and-braces read of the module's own send sites: every
        `send-keys` payload literal in watchdog/ must be an ordinary key or
        text, never a session-ending one."""
        src = (REPO / "watchdog" / "__init__.py").read_text(encoding="utf-8")
        flat = re.sub(r"\s+", " ", src)
        self.assertNotIn("/exit", flat,
                         "watchdog/ still references /exit (#132)")


class TestRunOnceDocstringIsAccurate(unittest.TestCase):
    """CLAUDE.md declares `run_once()`'s docstring the SINGLE SOURCE OF TRUTH
    for what each job does. It said "Seven jobs" while the file defined 23 — a
    stale index is how job 18 stayed unexamined for two days. Lock the count to
    the entries actually documented so it cannot rot silently again."""

    def test_header_count_matches_the_documented_job_entries(self):
        doc = wd.run_once.__doc__ or ""
        header = re.search(r"(\d+)\s+jobs per poll", doc)
        self.assertIsNotNone(
            header,
            "run_once's docstring must open with a NUMERIC job count "
            "('N jobs per poll') — a spelled-out word cannot be checked, and "
            "that is how 'Seven jobs' outlived 23 of them (#132)")
        documented = set(re.findall(r"^\s*\((\d+[a-z]?)\)", doc, re.M))
        self.assertEqual(
            int(header.group(1)), len(documented),
            "run_once's docstring says %s jobs but documents %d entries: %s"
            % (header.group(1), len(documented), sorted(documented)))

    def test_removed_jobs_are_recorded_as_removed_not_silently_dropped(self):
        """Job numbers are never reused (the #102 convention) — 12, 18 and 23
        must stay addressable, marked REMOVED, so every historical log line and
        comment referring to them still resolves."""
        doc = wd.run_once.__doc__ or ""
        for num in ("12", "18", "23"):
            entry = re.search(r"^\s*\(%s\)(.{0,400})" % num, doc, re.M | re.S)
            self.assertIsNotNone(entry, "job %s vanished from the docstring "
                                        "instead of being marked REMOVED" % num)
            self.assertIn("REMOVED", entry.group(1),
                          "job %s must be marked REMOVED (#132)" % num)


class TestWatchdogTimerDisableMarker(unittest.TestCase):
    """A deliberate `systemctl --user stop api-watchdog.timer` was silently
    undone by the next `airuleset.py push` — install unconditionally ran
    `enable --now`. That is how this ticket's own fleet-wide mitigation was
    found reverted, with the timer live on all 6 boxes. A marker file makes a
    deliberate stop survive a deploy."""

    def test_marker_path_is_exposed(self):
        self.assertTrue(hasattr(airuleset, "watchdog_disable_marker"))
        self.assertEqual(airuleset.watchdog_disable_marker().name,
                         "api-watchdog.disabled")

    def test_install_does_not_enable_the_timer_when_the_marker_exists(self):
        calls = []

        def fake_systemctl(args):
            calls.append(list(args))
            return 0, "", ""

        with unittest.mock.patch.object(airuleset, "_run_systemctl",
                                        side_effect=fake_systemctl), \
             unittest.mock.patch.object(airuleset, "watchdog_disable_marker",
                                        return_value=Path("/nonexistent")), \
             unittest.mock.patch.object(Path, "exists", return_value=True), \
             unittest.mock.patch.object(Path, "write_text"), \
             unittest.mock.patch.object(Path, "mkdir"), \
             unittest.mock.patch.object(Path, "read_text", return_value=""), \
             unittest.mock.patch("subprocess.run"):
            airuleset.setup_watchdog_service()

        enabled = [c for c in calls if "enable" in c]
        self.assertEqual(
            enabled, [],
            "install re-enabled the timer despite the disable marker — a "
            "deliberate stop must survive a push (#132)")


if __name__ == "__main__":
    unittest.main()
