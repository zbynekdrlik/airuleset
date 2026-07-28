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
        # `_reconcile_candidate_panes` is deliberately NOT here: it only
        # ENUMERATES panes and job 20 (goal re-arm) still needs it. The
        # capability that had to go is the sending, not the listing.
        for name in ("_restart_pane", "_wait_for_shell_returns",
                     "_wait_for_relaunch", "_wait_for_idle_after_dialog",
                     "RELAUNCH_CMD", "model_reconcile", "hooks_reconcile",
                     "model_generation_reconcile", "_hooks_config_hash",
                     "hooks_settings_path"):
            self.assertFalse(
                hasattr(wd, name),
                "watchdog.%s is back — it was removed in #132 because it typed "
                "/exit into a live user session" % name)

    def test_no_session_ending_string_survives_as_executable_code(self):
        """The AST-level companion to the regex above. Parses watchdog/ and
        checks only string constants that are NOT docstrings, so the removed
        jobs' history can keep describing `/exit` in prose (it must — the
        numbers stay addressable) while a literal `/exit` reaching any real
        expression fails. A plain substring assert would match its own
        explanation; a docstring-stripped AST walk cannot."""
        import ast
        src = (REPO / "watchdog" / "__init__.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        docstring_nodes = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", None)
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    docstring_nodes.add(id(body[0].value))
        offenders = [
            (n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstring_nodes
            and n.value.strip() in _SESSION_ENDING_PAYLOADS]
        self.assertEqual(
            offenders, [],
            "a session-ending payload survives as executable code in "
            "watchdog/ (#132): %s" % offenders)


class TestPzServerIncidentReplay(unittest.TestCase):
    """The incident itself, replayed through the REAL shipped `run_once`.

    dev1, 2026-07-28 — the pane `zbynek-0:12.0`, cwd `/home/newlevel/devel/pz-server`,
    a session the user was actively working in:

        12:30:34  OK restart (hooks changed) zbynek-0:12.0
        12:33:49  OK (model-gen-reconcile) zbynek-0:12.0 claude-opus-5 -> claude-opus-5[1m] (restarted)

    Every condition that produced those two lines is reconstructed here: a live
    `claude` pane, a resolvable transcript whose model is `claude-opus-5` (never
    the `[1m]` alias — that suffix does not exist in the API model field, which
    is precisely why job 23 fired), a hooks configuration that has changed since
    the session started, and a pane sitting idle at a bare prompt in the instant
    between turns — the one moment the old guards read as "safe".

    The assertion is that today's code sends NOTHING into that pane."""

    PANE = "%12"
    CWD = "/home/newlevel/devel/pz-server"
    IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"

    def _seed(self, projects_dir):
        import json
        enc = wd.encode_project_dir(self.CWD)
        d = Path(projects_dir) / enc
        d.mkdir(parents=True, exist_ok=True)
        f = d / "sess-pz.jsonl"
        f.write_text("".join(json.dumps(e) + "\n" for e in [
            {"type": "assistant", "timestamp": "2026-07-28T10:00:00.000Z",
             "message": {"model": "claude-opus-5",
                         "content": [{"type": "text", "text": "hi"}]}},
            {"type": "assistant", "timestamp": "2026-07-28T10:29:00.000Z",
             "message": {"model": "claude-opus-5",
                         "content": [{"type": "text", "text": "✅ DONE: hotovo"}]}},
        ]), encoding="utf-8")

    def test_the_pane_the_user_was_working_in_is_never_typed_into(self):
        import tempfile
        sent = []

        def fake_tmux(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return "%s\tclaude\t%s" % (self.PANE, self.CWD)
            if "display-message" in j:
                if argv[-1] == "#{pane_in_mode}":
                    return "0"
                if argv[-1] == "#{pane_current_command}":
                    return "claude"
                return "zbynek-0:12.0"
            if "send-keys" in j:
                sent.append(argv)
                return ""
            if "capture-pane" in j:
                return self.IDLE
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "projects"
            proj.mkdir()
            self._seed(proj)

            # The incident ran through cmd_watchdog, which wires both of the
            # params that armed jobs 12/18/23. They no longer exist — but the
            # replay must still ARM them if they ever come back, or it would
            # pass vacuously against the very code it is meant to catch. So
            # pass whatever the current signature accepts, and nothing else.
            import inspect
            accepted = inspect.signature(wd.run_once).parameters
            extra = {}
            if "target_model" in accepted:
                extra["target_model"] = "claude-opus-5[1m]"
            if "hooks_settings_path" in accepted:
                settings = Path(tmp) / "settings.json"
                settings.write_text(
                    '{"hooks": {"PostToolUse": [{"matcher": "Bash"}]}}',
                    encoding="utf-8")
                extra["hooks_settings_path"] = settings
                # job 18 restarts on a hash MISMATCH against its stored
                # baseline — seed the baseline as the incident had it, i.e.
                # recorded before the deploy that changed the hooks block.
                (Path(tmp) / "state.json").write_text(
                    '{"hooks_session_hash": {"sess-pz": "stale-baseline"}}',
                    encoding="utf-8")

            logs = wd.run_once(
                now=1785000000.0, dry_run=False, run=fake_tmux,
                send_fn=lambda *a, **k: None,
                projects_dir=proj, state_path=Path(tmp) / "state.json",
                pending_prefix=str(Path(tmp) / "pending-"),
                sleep_fn=lambda s: None, **extra)

        self.assertEqual(
            sent, [],
            "the watchdog typed into the pz-server pane again (#132): %s" % (sent,))
        for ln in logs:
            self.assertNotIn("restart", ln.lower(),
                             "a sweep still proposes a restart: %s" % logs)


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
