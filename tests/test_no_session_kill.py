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
_SESSION_ENDING_PAYLOADS = (r"/exit", r"/quit", r"C-c", r"C-d")
_PANE_DESTROYING_SUBCOMMANDS = ("kill-session", "kill-pane", "kill-window",
                                "kill-server", "respawn-pane",
                                "respawn-window")


# #267: `scripts/measure_scrollback_holes.py` legitimately calls
# `tmux ... kill-server` -- but ONLY against a tmux SERVER PROCESS it
# creates itself moments earlier via `tmux -L <random-uuid> new-session`,
# an entirely separate socket/process from the box's real managed server
# (which every real Claude Code pane lives on, addressed with NO `-L`).
# It is structurally incapable of reaching a real user session: the
# socket name is a fresh `uuid.uuid4()` generated at the top of `main()`,
# never derived from or overlapping any real session identifier. This is
# the SAME class of thing #132 protects against ONLY in the sense that
# both say "kill-server" -- the guarded CAPABILITY (ending a session a
# real person is working in) is simply not present here, so this is a
# named, narrow exemption from the payload-string scan, not a weakening
# of what #132 actually forbids. Keep this list to files that make the
# identical isolated-`-L`-socket argument in their own code/comments --
# never widen it to a whole directory.
#
# #289: `scripts/measure_scrollback_events.py` extends the SAME harness
# (its own module docstring says so verbatim: "mirrors
# measure_scrollback_holes.py's harness exactly: isolated CLAUDE_CONFIG_DIR,
# isolated disposable tmux `-L` socket") -- every `tmux` call, kill-server
# included, is routed through `msh.tmux(sock, ...)`, which always prepends
# `-L sock` where `sock` is a fresh `uuid.uuid4()` generated per run, never
# derived from any real session identifier. Same structural argument as
# the #267 entry above, same narrow scope.
_ISOLATED_EXPERIMENT_HARNESSES = (
    "scripts/measure_scrollback_holes.py",
    "scripts/measure_scrollback_events.py",
)


def _code_files():
    """Every tracked Python/shell file that could actually SEND something —
    `tests/` excluded because this very file names the banned payloads (a lock
    test that greps for a literal will otherwise match its own prose), and
    docs/rules excluded because prose describing the removed mechanism is
    history, not capability."""
    out = subprocess.run(["git", "ls-files", "-z", "*.py", "*.sh"],
                         cwd=REPO, capture_output=True, text=True, check=True)
    for rel in out.stdout.split("\0"):
        if not rel or rel.startswith("tests/") or rel in _ISOLATED_EXPERIMENT_HARNESSES:
            continue
        p = REPO / rel
        if p.is_file():
            yield rel, p.read_text(encoding="utf-8", errors="replace")


def _strip_prose(rel, text):
    """`text` with comments and string literals removed, so a scan for a banned
    token cannot be tripped by prose that merely NAMES it. Python goes through
    `tokenize` (which drops COMMENT and STRING tokens, docstrings included);
    shell keeps only its non-comment lines. A file that will not tokenize is
    returned unchanged — better a false positive on this guard than a silent
    hole in it."""
    if rel.endswith(".py"):
        import io
        import tokenize
        try:
            return " ".join(
                tok.string for tok in
                tokenize.generate_tokens(io.StringIO(text).readline)
                if tok.type not in (tokenize.COMMENT, tokenize.STRING))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return text
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("#"))


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
        # Comments and docstrings are stripped first, for the same reason the
        # /exit scan has an AST companion: prose must stay free to NAME the
        # thing it forbids. `.sh` files keep only their non-comment lines;
        # `.py` files are reduced to their code tokens.
        offenders = []
        for rel, text in _code_files():
            code = _strip_prose(rel, text)
            for sub in _PANE_DESTROYING_SUBCOMMANDS:
                if sub in code:
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
        offenders = []
        # #433 extracted leaf modules out of __init__.py — the guard must
        # cover EVERY watchdog/*.py, or a payload could hide in a leaf
        # (the cluster-G review's 🔵 finding: this scan lagged the split).
        for py in sorted((REPO / "watchdog").glob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            docstring_nodes = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)):
                    body = getattr(node, "body", None)
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        docstring_nodes.add(id(body[0].value))
            offenders.extend(
                (py.name, n.lineno, n.value) for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docstring_nodes
                and n.value.strip() in _SESSION_ENDING_PAYLOADS)
        self.assertEqual(
            offenders, [],
            "a session-ending payload survives as executable code in "
            "watchdog/ (#132): %s" % offenders)


class TestIsolatedExperimentHarnessExemptionStaysNarrow(unittest.TestCase):
    """#267: `_ISOLATED_EXPERIMENT_HARNESSES` exempts a file from the
    kill-server scan ONLY because its `kill-server` call is structurally
    incapable of reaching a real managed session (a fresh `-L <uuid>`
    socket it creates itself, never the default socket every real pane
    lives on). This class re-verifies that claim DIRECTLY against the
    exempted file's own source, so the exemption cannot silently rot into
    a real hazard if the script is edited later -- a future edit that
    drops the `-L` scoping would be caught here even though the main scan
    is blind to this file by design."""

    def test_every_exempted_file_only_kills_an_dashL_scoped_server(self):
        for rel in _ISOLATED_EXPERIMENT_HARNESSES:
            p = REPO / rel
            self.assertTrue(p.is_file(), rel)
            src = p.read_text(encoding="utf-8")
            # every literal "kill-server" call must be reached through the
            # module's own `tmux(sock, ...)` helper -- never a bare
            # ["tmux", "kill-server"] argv with no -L socket in front of it.
            self.assertNotIn('"tmux", "kill-server"', src)
            self.assertNotIn("'tmux', 'kill-server'", src)
            self.assertIn("kill-server", src)  # the call this test is about must exist
            self.assertIn('"-L", sock', src)   # every tmux() call is -L-scoped

    def test_the_socket_name_is_freshly_random_never_a_fixed_or_real_name(self):
        src = (REPO / "scripts" / "measure_scrollback_holes.py").read_text(
            encoding="utf-8")
        self.assertIn("uuid.uuid4()", src)
        # never the bare managed-fleet default (no -L at all) or a name a
        # real session might plausibly use.
        self.assertNotIn('sock = "default"', src)


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
        inspected = []

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
                inspected.append(argv)
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
        # Positive control: the sweep must actually have LOOKED at the pane.
        # Without this, a refactor that bailed before pane enumeration would
        # satisfy the assertion above for entirely the wrong reason.
        self.assertTrue(
            any("capture-pane" in " ".join(c) and self.PANE in c
                for c in inspected),
            "the sweep never captured %s — the 'no keystrokes' result above "
            "would be vacuous: %s" % (self.PANE, inspected))
        for ln in logs:
            self.assertNotIn("restart", ln.lower(),
                             "a sweep still proposes a restart: %s" % logs)


class TestRunOnceDocstringIsAccurate(unittest.TestCase):
    """CLAUDE.md declares `run_once()`'s docstring the SINGLE SOURCE OF TRUTH
    for what each job does. It said "Seven jobs" while the file defined 23 — a
    stale index is how job 18 stayed unexamined for two days. Lock the count to
    the entries actually documented so it cannot rot silently again."""

    @staticmethod
    def _documented():
        """Whole-number job entries only. Letter sub-entries like (4a) belong
        to their parent job — counting them as jobs is half of how the FIRST
        version of this test passed on a still-wrong docstring: (4a) was
        counted as a job while job 10 was missing entirely, and the two errors
        cancelled to exactly the right total."""
        doc = wd.run_once.__doc__ or ""
        return set(re.findall(r"^\s*\((\d+)\)", doc, re.M))

    def test_header_count_matches_the_documented_job_entries(self):
        doc = wd.run_once.__doc__ or ""
        header = re.search(r"(\d+)\s+numbered jobs per poll", doc)
        self.assertIsNotNone(
            header,
            "run_once's docstring must open with a NUMERIC count ('N numbered "
            "jobs per poll') — a spelled-out word cannot be checked, and that "
            "is how 'Seven jobs' outlived 23 of them (#132)")
        documented = self._documented()
        self.assertEqual(
            int(header.group(1)), len(documented),
            "run_once's docstring says %s jobs but documents %d entries: %s"
            % (header.group(1), len(documented), sorted(documented, key=int)))

    def test_the_numbering_has_no_holes(self):
        """A missing number is invisible to a bare count (job 10 was absent for
        as long as the docstring existed). Numbers run 1..N with none skipped —
        a retired job keeps its number and says REMOVED, so a hole can only
        mean an undocumented job."""
        documented = {int(n) for n in self._documented()}
        expected = set(range(1, max(documented) + 1))
        self.assertEqual(
            expected - documented, set(),
            "run_once's docstring skips job number(s) %s — every number up to "
            "%d must be documented, retired ones included (#132)"
            % (sorted(expected - documented), max(documented)))

    def test_the_live_and_retired_split_is_stated_and_correct(self):
        """The header claims an L-live / R-retired split; both halves must
        match the entries themselves, so a job removed later cannot be dropped
        from the prose while the total still adds up."""
        doc = wd.run_once.__doc__ or ""
        m = re.search(r"(\d+)\s+numbered jobs per poll\s*—\s*(\d+)\s+LIVE and "
                      r"(\d+)\s*\n?\s*RETIRED", doc)
        self.assertIsNotNone(m, "the header must state 'N numbered jobs per "
                                "poll — L LIVE and R RETIRED'")
        total, live, retired = (int(m.group(i)) for i in (1, 2, 3))
        self.assertEqual(total, live + retired,
                         "header arithmetic: %d != %d + %d" % (total, live, retired))
        actually_removed = {
            n for n in self._documented()
            if "REMOVED" in (re.search(r"^\s*\(%s\)(.{0,400})" % n, doc,
                                       re.M | re.S) or
                             re.match("", "")).group(1)}
        self.assertEqual(
            len(actually_removed), retired,
            "header says %d retired but %d entries are marked REMOVED: %s"
            % (retired, len(actually_removed),
               sorted(actually_removed, key=int)))

    def test_every_documented_live_job_still_exists_in_the_code(self):
        """Cross-check the docstring against the SOURCE, not just against
        itself — a self-consistent docstring can still describe a job whose
        implementation is gone. Any function a LIVE entry names must still be
        defined and still be called somewhere in the module (a job's entry
        often names its helpers, which `goal_rearm` and friends call rather
        than `run_once` directly, so "called by run_once" is too strict).

        #403: `src` is the WHOLE `watchdog` package, not just `__init__.py`
        alone — the #402/#403 collapses moved several shared helpers'
        REAL call sites into `watchdog/goal.py`/`watchdog/compact.py`
        (`watchdog._janitor_clear_watch(...)`, module-qualified), while
        the function DEFINITIONS themselves stay in `__init__.py`. A
        same-file-only count reads such a helper as "defined but never
        called" the moment its only real caller lives in a sibling
        module — a false positive, not evidence of dead code (live-
        reproduced: `_janitor_clear_watch` is genuinely called from
        `goal.py`'s `deliver_goal`, invisible to a `__init__.py`-only
        scan)."""
        doc = wd.run_once.__doc__ or ""
        src = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((REPO / "watchdog").glob("*.py")))
        for num in sorted(self._documented(), key=int):
            entry = re.search(r"^\s*\(%s\)(.{0,600})" % num, doc,
                              re.M | re.S).group(1)
            if "REMOVED" in entry:
                continue
            for name in set(re.findall(r"\(([a-z_]{6,})\)", entry)
                            + re.findall(r"`([a-z_]{6,})`", entry)):
                if not hasattr(wd, name):
                    continue      # prose word, not an identifier
                self.assertGreaterEqual(
                    src.count("%s(" % name), 2,
                    "job %s documents %s, which is defined but never called — "
                    "a documented job with no live call site (#132)"
                    % (num, name))

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


class TestRemovedJobStateIsCleanedUp(unittest.TestCase):
    """The removed jobs left eight named stores in every managed box's
    `~/.claude/api-watchdog-state.json` (dev1 alone carried `modelswitch`,
    `hooks_session_hash`, `hooks_restarted`, `modelgen_restarted` and four
    more). Nothing writes or reads them now, and the generic cleanup rightly
    refuses to delete NAMED job stores — so they need an explicit one-shot
    drop, or they sit there forever looking like live tracking."""

    ORPHANS = ("modelswitch", "modelswitch_pending", "modelswitch_attempts",
               "hooks_session_hash", "hooks_restarted",
               "hooks_restart_attempts", "modelgen_restarted",
               "modelgen_restart_attempts")

    def test_a_sweep_drops_the_removed_jobs_state(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            seeded = {k: {"sess-old": True} for k in self.ORPHANS}
            seeded["goalarm"] = {"%1": 1}          # a LIVE named store
            state_path.write_text(json.dumps(seeded), encoding="utf-8")
            proj = Path(tmp) / "projects"
            proj.mkdir()
            wd.run_once(now=1785000000.0, dry_run=False,
                        run=lambda argv, timeout=8: "",
                        send_fn=lambda *a, **k: None,
                        projects_dir=proj, state_path=state_path,
                        pending_prefix=str(Path(tmp) / "pending-"))
            after = json.loads(state_path.read_text(encoding="utf-8"))

        for k in self.ORPHANS:
            self.assertNotIn(k, after,
                             "%s survived a sweep — removed-job state (#132)" % k)
        self.assertIn("goalarm", after,
                      "the cleanup ate a LIVE named job store — that is the "
                      "2026-07-21 ticket-fallback starvation all over again")


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
            rv = airuleset.setup_watchdog_service()

        enabled = [c for c in calls if "enable" in c]
        self.assertEqual(
            enabled, [],
            "install re-enabled the timer despite the disable marker — a "
            "deliberate stop must survive a push (#132)")
        self.assertTrue(rv, "the marker branch must still report success — "
                            "install did the work it was asked to do")
        self.assertIn(["daemon-reload"], calls,
                      "the marker branch must still refresh the units before "
                      "returning, or it leaves a half-configured box (#132)")
        self.assertTrue(
            any("disable" in c for c in calls),
            "the marker must also DISABLE the unit — a stopped-but-enabled "
            "timer comes back at the next boot (#132)")


if __name__ == "__main__":
    unittest.main()
