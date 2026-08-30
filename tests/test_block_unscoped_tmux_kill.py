"""#734 — a PreToolUse(Bash) guard against an UNSCOPED tmux server/session
kill, plus the doctrine/wiring locks around it.

Incident (dev1, 2026-08-27 00:21): a nested adversarial-review subagent
(fresh context, airuleset lane) live-checked `tmux list-clients` behaviour
for #731, spun up test sessions on the DEFAULT socket (no `-S`/`-L`, no
`TMUX_TMPDIR`), then "cleaned up" with the exact command

    tmux kill-server 2>/dev/null; echo "cleaned"

Bare `kill-server` with no socket scoping killed the owner's WHOLE live
default tmux server — session `zbynek`, every work window, his live Claude
Code session running inside it. Not an OOM; a self-inflicted unscoped kill.

The #613 test-isolation lock (`test_tmux_test_isolation_lock.py`) scans only
committed `tests/`+`hooks/` files — it is structurally blind to an ad-hoc
live Bash command, and the worktree-isolation guard reasons only about
git/file scope, never a tmux default socket. So nothing runtime-deterministic
stopped it. `hooks/block-unscoped-tmux-kill.sh` closes that gap, in the same
class as `block-history-rewrite.sh` / `block-broad-pkill.sh` — a deterministic
PreToolUse(Bash) deny that fires for EVERY model, including a fresh-context
subagent that never read a single rule.

The thesis: an UNSCOPED destructive tmux kill (default/inherited socket) is
blocked; a SOCKET-SCOPED kill of a private socket (`-S <path>` / `-L <name>`
— tmux's own documented selectors that override `$TMUX`) stays free, so the
fleet's own isolated-server teardown never trips its own guard.
"""
import json
import subprocess
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "block-unscoped-tmux-kill.sh"
HOOKS_JSON = REPO / "settings" / "hooks.json"

# Same generous hang-guard bound as the sibling hook tests (#444/#701): the
# hook does bounded work, but this box runs many concurrent full-suite runs.
HOOK_TIMEOUT_S = 120


def run_hook(cmd):
    payload = json.dumps({"tool_input": {"command": cmd}})
    return subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        timeout=HOOK_TIMEOUT_S,
    )


class TestHookWiring(TestCase):
    def test_hook_exists_and_executable(self):
        self.assertTrue(HOOK.exists(), f"missing hook: {HOOK}")
        self.assertTrue(HOOK.stat().st_mode & 0o111, "hook not executable")

    def test_wired_under_pretooluse_bash(self):
        cfg = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        bash_groups = [
            g for g in cfg["hooks"]["PreToolUse"] if g.get("matcher") == "Bash"
        ]
        commands = [h["command"] for g in bash_groups for h in g["hooks"]]
        self.assertTrue(
            any("block-unscoped-tmux-kill.sh" in c for c in commands),
            "block-unscoped-tmux-kill.sh not wired under PreToolUse(Bash)",
        )


class TestHookBlocksUnscopedKills(TestCase):
    def assertBlocked(self, cmd):
        r = run_hook(cmd)
        self.assertEqual(
            r.returncode, 2, f"expected BLOCK for: {cmd}\nstderr={r.stderr}"
        )
        # exit-2 reasons go to STDERR (stdout is invisible to the model) and
        # must hand back the scoped recipe (-S/-L), not just say "no".
        self.assertRegex(r.stderr, r"(?i)tmux")
        self.assertRegex(r.stderr, r"-S|-L")

    def test_the_exact_incident_command(self):
        # dev1 2026-08-27 00:21 — the byte-exact command that killed the box.
        self.assertBlocked('tmux kill-server 2>/dev/null; echo "cleaned"')

    def test_bare_kill_server(self):
        self.assertBlocked("tmux kill-server")

    def test_bare_kill_session(self):
        self.assertBlocked("tmux kill-session -t r731test")

    def test_bare_kill_pane(self):
        # #734 review 🟡: kill-pane on the default socket destroys the owner's
        # live pane — same incident class as kill-server.
        self.assertBlocked("tmux kill-pane -t 0")

    def test_bare_kill_window(self):
        self.assertBlocked("tmux kill-window -t 0")

    def test_bare_kill_client(self):
        self.assertBlocked("tmux kill-client")

    def test_sudo_prefix_does_not_hide_it(self):
        self.assertBlocked("sudo tmux kill-server")

    def test_env_prefix_does_not_hide_it(self):
        self.assertBlocked("TMUX_TMPDIR=/tmp/x tmux kill-server")

    def test_inside_bash_c_wrapper(self):
        self.assertBlocked("bash -c 'tmux kill-server'")

    def test_in_a_later_compound_segment(self):
        self.assertBlocked('echo cleaning && tmux kill-server')

    def test_pkill_bare_tmux_name(self):
        self.assertBlocked("pkill tmux")

    def test_pkill_dash_f_bare_tmux(self):
        self.assertBlocked("pkill -f tmux")

    def test_pkill_signal_and_bare_tmux(self):
        self.assertBlocked("pkill -9 tmux")

    def test_killall_tmux(self):
        self.assertBlocked("killall tmux")

    def test_pkill_dash_f_tmux_pattern_without_socket(self):
        # a -f pattern that references tmux but carries no socket selector —
        # it can still match the owner's default server process.
        self.assertBlocked('pkill -f "tmux new-session"')

    def test_composite_pgrep_feeds_kill(self):
        self.assertBlocked("kill $(pgrep -f tmux)")

    def test_composite_pgrep_pipe_xargs_kill(self):
        self.assertBlocked("pgrep -f tmux | xargs kill")


class TestHookAllowsScopedAndUnrelated(TestCase):
    def assertAllowed(self, cmd):
        r = run_hook(cmd)
        self.assertEqual(
            r.returncode, 0, f"expected ALLOW for: {cmd}\nstderr={r.stderr}"
        )

    def test_socket_path_scoped_kill_server(self):
        self.assertAllowed("tmux -S /tmp/iso376/sock kill-server")

    def test_socket_name_scoped_kill_server(self):
        self.assertAllowed("tmux -L iso376 kill-server")

    def test_socket_name_scoped_kill_session(self):
        self.assertAllowed("tmux -L iso376 kill-session -t t376")

    def test_socket_scoped_kill_pane_and_window(self):
        self.assertAllowed("tmux -L iso376 kill-pane -t 0")
        self.assertAllowed("tmux -S /tmp/iso376/sock kill-window -t 0")

    def test_glued_socket_name_selector(self):
        self.assertAllowed("tmux -Liso376 kill-server")

    def test_scoped_kill_server_with_redirect_and_echo(self):
        # the incident shape, but SCOPED — must pass untouched.
        self.assertAllowed('tmux -L iso376 kill-server 2>/dev/null; echo done')

    def test_non_kill_subcommands_pass(self):
        self.assertAllowed("tmux list-sessions")
        self.assertAllowed("tmux new-session -d -s foo")
        self.assertAllowed("tmux send-keys -t foo hello Enter")

    def test_pkill_socket_scoped_tmux_pattern(self):
        self.assertAllowed('pkill -f "tmux -L iso376"')

    def test_readonly_pgrep_feeds_nothing(self):
        self.assertAllowed("pgrep -f tmux")

    def test_plain_pid_kill(self):
        self.assertAllowed("kill 12345")

    def test_unrelated_command(self):
        self.assertAllowed("echo tmux kill-server is only text here")

    def test_bypass_marker_lifts_block(self):
        self.assertAllowed(
            "tmux kill-server  # airuleset:tmux-kill-ok one-off manual teardown")


class TestFailOpenOnGarbage(TestCase):
    def test_empty_command_passes(self):
        self.assertEqual(run_hook("").returncode, 0)

    def test_no_command_field_passes(self):
        r = subprocess.run(
            ["bash", str(HOOK)], input="{}", capture_output=True, text=True,
            timeout=HOOK_TIMEOUT_S)
        self.assertEqual(r.returncode, 0)

    def test_malformed_json_passes(self):
        r = subprocess.run(
            ["bash", str(HOOK)], input="not json at all",
            capture_output=True, text=True, timeout=HOOK_TIMEOUT_S)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":  # pragma: no cover
    main()
