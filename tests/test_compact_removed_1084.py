"""#1084 L1 — machine-triggered compacts are REMOVED for good (hard-off in
code), owner ROZHODNUTÉ 2026-09-19: "už tie compacty vôbec nechcem … samotné
deploye na targety by to mali zabezpečiť". forestshop-dev slipped the #911
hand-placed `~/.claude/watchdog-disable-compact` flag and delivered `/compact`
after every Work Complete for two weeks — so the DISABLED state must not depend
on a per-box file a deploy cannot see. This lane makes the CODE default hard-off
and lets `install`/`push` be the fleet-wide guarantee.

These are the L1 "never delivers / never records" locks:
  - `compact_sweep` returns at its top with the removed journal line and NEVER
    calls `deliver_compact` — even for a fully-servable request + idle pane.
  - `cmd_compact_request` is a harmless stub (any flags → removed line, exit 0),
    so a stale caller in an old skill template can never break a turn.
  - `settings/hooks.json` carries NO compact hook, and the two `notify-compact-*`
    hook files are deleted.
  - `install` deletes a stale `~/.claude/watchdog-disable-compact` owner flag
    with a LOUD line; `push`'s per-box post-check prints `compact: hard-off (code)`.
  - the doctrine texts no longer instruct a `compact-request` self/record/status
    call anywhere (0 hits outside the history/rules-reference archive).

The compaction OBSERVATION helpers (`_pane_compacting`, `COMPACTING_MARKER`,
transcript compaction records that lane-reconcile / the goal jobs read) are
untouched — this lane removes the DELIVERY machinery only. L2 (a later lane)
deletes `deliver_compact` + the request store this early return orphans.
"""

import inspect
import json
import os
import shutil
import subprocess
import sys
import types
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_remote
import watchdog as wd
from watchdog import compact

ROOT = Path(__file__).resolve().parents[1]

REMOVED_LINE = ("compact: machine compacts removed (owner 2026-09-19, #1084) "
                "— native autocompact only")
INSTALL_LOUD_LINE = ("compact: stale owner flag removed — machine compacts are "
                     "gone in code (#1084)")
PUSH_POSTCHECK_LINE = "compact: hard-off (code)"


def _isolate_requests(testcase):
    """Own isolated compact-requests file — the live systemd watchdog runs this
    working tree every 60s, so a test must never touch the real ~/.claude copy."""
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    reqp = Path(d.name) / "compact-requests-test.json"
    for name in ("compact_requests_path", "compact_delivered_path",
                 "compact_sync_log_path", "compact_queued_path"):
        path = Path(d.name) / ("%s.json" % name)
        p = m.patch.object(compact, name, return_value=path)
        p.start()
        testcase.addCleanup(p.stop)
    return reqp


def _args(**kw):
    kw.setdefault("self", False)
    kw.setdefault("record", False)
    kw.setdefault("status", False)
    kw.setdefault("session", "")
    kw.setdefault("cwd", "")
    kw.setdefault("origin", "")
    return types.SimpleNamespace(**kw)


# --------------------------------------------------------------------------- #
# (a) compact_sweep returns at its top, never delivers
# --------------------------------------------------------------------------- #
class TestCompactSweepRemoved(unittest.TestCase):
    CWD = "/home/newlevel/devel/removed-sweep"

    def setUp(self):
        self.reqp = _isolate_requests(self)

    def test_journals_removed_line_and_never_calls_deliver(self):
        # A fully SERVABLE request + an idle pane: pre-#1084 this delivered.
        now = 1_000_000.0
        compact.record_compact_request("sess-r", self.CWD, now=now,
                                       path=self.reqp, origin="self-callback")
        keys = []
        with m.patch.object(compact, "deliver_compact",
                            side_effect=AssertionError(
                                "compact_sweep must NOT call deliver_compact (#1084)")):
            logs = compact.compact_sweep(
                now + 5, run=lambda *a, **k: keys.append((a, k)),
                projects_dir=None, requests_path=self.reqp)
        self.assertTrue(any("machine compacts removed" in ln and "#1084" in ln
                            for ln in logs),
                        "compact_sweep must journal the removed line: %r" % logs)
        self.assertEqual(keys, [], "compact_sweep must issue no keystrokes")

    def test_exact_removed_line(self):
        logs = compact.compact_sweep(1.0, run=None, requests_path=self.reqp)
        self.assertIn(REMOVED_LINE, logs)

    def test_never_delivers_even_when_owner_flag_absent_and_disable_ignored(self):
        # No flag read at all: the removed line fires regardless of the
        # (now-vestigial) owner disable flag state.
        compact.record_compact_request("sess-x", self.CWD, now=1000.0,
                                       path=self.reqp, origin="self-callback")
        with m.patch.object(compact, "deliver_compact",
                            side_effect=AssertionError("must not deliver")):
            logs = compact.compact_sweep(2000.0, run=None, requests_path=self.reqp)
        self.assertIn(REMOVED_LINE, logs)


# --------------------------------------------------------------------------- #
# (a) run_once never forces a full sweep for a pending compact
# --------------------------------------------------------------------------- #
class TestSweepUrgentNeverForcedByCompact(unittest.TestCase):
    def test_run_once_caller_passes_compact_pending_false(self):
        # run_once must call sweep_urgent with compact_pending=False, no matter
        # what compact requests exist — a banned behaviour cannot force a full
        # sweep any more (#1084).
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        creqp = Path(td.name) / "compact-requests.json"
        compact.record_compact_request("sid-x", "/repo", now=1055,
                                       path=str(creqp), origin="self-callback")
        state_path = Path(td.name) / "state.json"
        captured = {}
        real_su = wd.sweep_urgent

        def _spy(*a, **k):
            captured["compact_pending"] = k.get("compact_pending")
            return real_su(*a, **k)

        with m.patch.object(wd, "sweep_urgent", side_effect=_spy):
            wd.run_once(now=1060.0, state_path=str(state_path), dry_run=True,
                        compact_requests_path=str(creqp))
        self.assertEqual(captured.get("compact_pending"), False,
                         "run_once must pass compact_pending=False (#1084)")


# --------------------------------------------------------------------------- #
# (c) cmd_compact_request is a removed stub
# --------------------------------------------------------------------------- #
class TestCmdCompactRequestStub(unittest.TestCase):
    def _run(self, args):
        buf = []
        with m.patch("sys.stdout") as out:
            out.write = lambda s: buf.append(s)
            rc = airuleset.cmd_compact_request(args)
        return rc, "".join(buf)

    def test_self_is_a_noop_stub_exit_zero(self):
        rc, txt = self._run(_args(self=True))
        self.assertIn("machine compacts removed", txt)
        self.assertIn(rc, (None, 0))

    def test_record_is_a_noop_stub_exit_zero(self):
        rc, txt = self._run(_args(record=True, session="s", cwd="/x",
                                  origin="self-callback"))
        self.assertIn("machine compacts removed", txt)
        self.assertIn(rc, (None, 0))

    def test_status_is_a_noop_stub_exit_zero(self):
        rc, txt = self._run(_args(status=True, session="s"))
        self.assertIn("machine compacts removed", txt)
        self.assertIn(rc, (None, 0))

    def test_no_flags_is_a_noop_stub_exit_zero(self):
        # pre-#1084 this exited non-zero (usage); now it can never break a turn.
        rc, txt = self._run(_args())
        self.assertIn("machine compacts removed", txt)
        self.assertIn(rc, (None, 0))


# --------------------------------------------------------------------------- #
# (b) settings/hooks.json + the notify-compact hook files
# --------------------------------------------------------------------------- #
class TestHooksJsonHasNoCompactHook(unittest.TestCase):
    def test_no_compact_hook_command_in_hooks_json(self):
        data = json.loads((ROOT / "settings" / "hooks.json").read_text())
        cmds = []
        for _event, matchers in data.get("hooks", {}).items():
            for mt in matchers:
                for h in mt.get("hooks", []):
                    cmds.append(h.get("command", ""))
        offenders = [c for c in cmds if "compact" in c]
        self.assertEqual(offenders, [],
                         "hooks.json must carry no compact hook (#1084): %r" % offenders)

    def test_notify_compact_hook_files_are_deleted(self):
        for name in ("notify-compact-request.sh",
                     "notify-compact-subagent-boundary.sh"):
            self.assertFalse((ROOT / "hooks" / name).exists(),
                             "hooks/%s must be deleted (#1084)" % name)


# --------------------------------------------------------------------------- #
# (d) install deletes a stale owner flag with a LOUD line
# --------------------------------------------------------------------------- #
class TestInstallRemovesStaleFlag(unittest.TestCase):
    def test_removes_flag_and_returns_loud_line(self):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        claude = Path(td.name) / ".claude"
        claude.mkdir()
        flag = claude / "watchdog-disable-compact"
        flag.write_text("")
        line = airuleset._remove_stale_compact_flag(claude)
        self.assertFalse(flag.exists(), "the stale flag must be deleted (#1084)")
        self.assertEqual(line, INSTALL_LOUD_LINE)

    def test_no_flag_is_a_silent_noop(self):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        claude = Path(td.name) / ".claude"
        claude.mkdir()
        self.assertIsNone(airuleset._remove_stale_compact_flag(claude))


# --------------------------------------------------------------------------- #
# (d) push post-check prints the hard-off line per box
# --------------------------------------------------------------------------- #
class TestPushPostCheckCompactLine(unittest.TestCase):
    def test_fragment_prints_the_exact_line(self):
        frag = cli_remote._compact_hardoff_postcheck()
        r = subprocess.run(["bash", "-c", frag], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(PUSH_POSTCHECK_LINE, r.stdout)

    def test_postcheck_is_wired_into_the_deploy_loop(self):
        src = Path(cli_remote.__file__).read_text()
        self.assertIn("_compact_hardoff_postcheck(", src)


# --------------------------------------------------------------------------- #
# (d.2) #1084 L1b — the `compact: hard-off (code)` line must actually PRINT on a
#       real box. Root cause: the L1 lane appended the echo AFTER the gh-chain
#       (#1051) and Playwright (#1048/#1058) post-check GROUPS, whose success and
#       SKIP paths end in `exit 0`; `exit` inside a `{ … }` command group (not a
#       subshell) terminates the whole remote `sh -c`, so a trailing `&& echo`
#       after them is unreachable on every real box (the line printed 0× on
#       v0.1.351). Approach 1 moves the report line to run right after
#       `airuleset.py install`, BEFORE the gating groups.
#         (a) an argv-order lock (index of the compact echo vs install / GH-CHAIN);
#         (b) an EXECUTION test that RUNS the assembled post-install fragment
#             through `sh -c` — the class of miss the L1 shape-lock could not
#             catch (it asserted the echo is PRESENT in the string, but never
#             executed the shell, so it stayed green on the broken ordering).
# --------------------------------------------------------------------------- #
class TestCompactLinePrintsBeforeGatingPostChecks(unittest.TestCase):
    _NAMES = ("_compact_hardoff_postcheck", "_gh_chain_postcheck",
              "_playwright_chromium_postcheck")

    def _deploy_src(self):
        return inspect.getsource(cli_remote._deploy_to_all_remotes)

    # (a) argv-order lock -------------------------------------------------- #
    def test_compact_echo_runs_after_install_and_before_the_gating_postchecks(self):
        src = self._deploy_src()
        i_install = src.index("python3 airuleset.py install")
        i_compact = src.index("_compact_hardoff_postcheck()", i_install)
        i_gh = src.index("_gh_chain_postcheck()", i_install)
        i_pw = src.index("_playwright_chromium_postcheck()", i_install)
        self.assertLess(i_install, i_compact,
                        "the compact report line must run AFTER `airuleset.py install`")
        self.assertLess(
            i_compact, i_gh,
            "the compact report line must run BEFORE the gh-chain group — a "
            "`{ … } && exit 0` group swallows a trailing echo (#1084 L1b)")
        self.assertLess(
            i_compact, i_pw,
            "the compact report line must run BEFORE the Playwright group (#1084 L1b)")

    # (b) execution test --------------------------------------------------- #
    def _ordered_postcheck_fragments(self):
        """The three post-check fragment strings, ordered as
        `_deploy_to_all_remotes` interpolates them into `remote_cmd` — read from
        the SHIPPED source, so the ordering under test is the real one, never a
        test-local copy. This is what makes (b) RED on the L1 (broken) ordering
        and GREEN on Approach 1's reorder."""
        src = self._deploy_src()
        anchor = src.index("python3 airuleset.py install")
        funcs = {
            "_compact_hardoff_postcheck": cli_remote._compact_hardoff_postcheck,
            "_gh_chain_postcheck": cli_remote._gh_chain_postcheck,
            "_playwright_chromium_postcheck": cli_remote._playwright_chromium_postcheck,
        }
        ordered = sorted(self._NAMES, key=lambda n: src.index(n + "()", anchor))
        return [funcs[n]() for n in ordered]

    def _assembled_fragment(self):
        """The post-`git pull` remote fragment: `python3 airuleset.py install`
        then the post-checks in shipped source order. A no-op fake `airuleset.py`
        in the cwd makes the real `python3 airuleset.py install` exit 0 with no
        real install (the design's "a stub … a temp dir")."""
        return " && ".join(["python3 airuleset.py install"]
                           + self._ordered_postcheck_fragments())

    # the coreutils the deploy fragment invokes by name. A curated PATH holding
    # ONLY these makes the "no npx" SKIP path deterministic on any box: without
    # it, the fragment's `export PATH="$HOME/.local/bin:$PATH"` still leaves the
    # box's real npx resolvable, so `command -v npx` would find it and RUN it
    # (a real chromium launch) instead of SKIPping.
    _SYS_TOOLS = ("python3", "timeout", "cat", "head", "tail", "grep",
                  "mktemp", "rm", "sleep")

    def _sysbin(self):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        for tool in self._SYS_TOOLS:
            real = shutil.which(tool)
            if real:
                os.symlink(real, d / tool)
        return d

    def _box(self, *, with_npx):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        home = Path(td.name)
        (home / ".claude").mkdir()
        bindir = home / ".local" / "bin"
        bindir.mkdir(parents=True)
        # the Playwright probe reads this marker; present => it does NOT SKIP as
        # "not provisioned", so the success path reaches the (fake) npx probe.
        (home / ".claude" / "airuleset-playwright-browsers-path").write_text("/tmp/bp\n")
        # a fake gh that resolves and whose `--version` returns 0, so the gh-chain
        # group ends normally (rc 0, control flows on) rather than taking its
        # `command -v gh || exit 0` SKIP path.
        gh = bindir / "gh"
        gh.write_text("#!/bin/sh\nexit 0\n")
        gh.chmod(0o755)
        if with_npx:
            # a fake npx: the probe's `npx … screenshot` returns 0 => probe success
            # => the Playwright group `exit 0`s (its success path).
            npx = bindir / "npx"
            npx.write_text("#!/bin/sh\nexit 0\n")
            npx.chmod(0o755)
        # a no-op fake airuleset.py so the real `python3 airuleset.py install`
        # exits 0 in this cwd without touching the box.
        repo = home / "repo"
        repo.mkdir()
        (repo / "airuleset.py").write_text("import sys\nraise SystemExit(0)\n")
        return home, repo

    def _run(self, home, repo):
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["PATH"] = str(self._sysbin())     # only curated coreutils + the fakes
        env["AIRULESET_PW_POSTCHECK_RETRY_SLEEP"] = "0"   # no real 5 s wait
        # `/bin/sh` explicitly (not via PATH) — the curated PATH omits `sh`.
        return subprocess.run(["/bin/sh", "-c", self._assembled_fragment()],
                              cwd=str(repo), capture_output=True, text=True,
                              timeout=60, env=env)

    def test_line_prints_on_the_healthy_success_path(self):
        # gh + npx present, both succeed — the Playwright group `exit 0`s on its
        # probe-success path, which swallowed the trailing echo on v0.1.351.
        home, repo = self._box(with_npx=True)
        r = self._run(home, repo)
        self.assertEqual(r.returncode, 0, r.stderr + "\n" + r.stdout)
        self.assertIn(
            PUSH_POSTCHECK_LINE, r.stdout,
            "the compact hard-off line must print on a healthy box (gh + npx "
            "present) — it printed 0× on v0.1.351 (#1084 L1b). stdout=%r" % r.stdout)

    def test_line_prints_on_the_no_npx_skip_path(self):
        # no npx — the Playwright group takes its `exit 0` SKIP path, which also
        # terminated the remote shell before the trailing echo on v0.1.351.
        home, repo = self._box(with_npx=False)
        r = self._run(home, repo)
        self.assertEqual(r.returncode, 0, r.stderr + "\n" + r.stdout)
        self.assertIn(
            PUSH_POSTCHECK_LINE, r.stdout,
            "the compact hard-off line must print even when Playwright SKIPs "
            "(no npx) — the L1 ordering swallowed it (#1084 L1b). stdout=%r" % r.stdout)


# --------------------------------------------------------------------------- #
# (e) no LIVE doctrine text or machine-emitted nudge instructs a compact-request
#     call any more. Scanned surfaces: EVERY doctrine .md (modules/ + skills/,
#     which reach a session's system prompt) PLUS the live watchdog nudge string
#     (watchdog/lane_resources.py's _lane_nudge_text — the #1084-review miss the
#     old 5-file whitelist could not catch). The kept-for-L2 machinery's own
#     internal code COMMENTS (watchdog/compact.py, cards.py) and the historical
#     docs/autopilot-log.md are deliberately out of scope — they instruct no live
#     session and L2 deletes the machinery.
# --------------------------------------------------------------------------- #
NEEDLES = ("compact-request --self", "compact-request --record",
           "compact-request --status")


class TestDoctrineHasNoCompactRequestInstruction(unittest.TestCase):
    def _doctrine_md(self):
        files = sorted((ROOT / "modules").rglob("*.md"))
        files += sorted((ROOT / "skills").rglob("*.md"))
        return files

    def test_no_self_record_status_instruction_in_any_doctrine_md(self):
        offenders = []
        for path in self._doctrine_md():
            text = path.read_text(encoding="utf-8")
            for needle in NEEDLES:
                if needle in text:
                    offenders.append("%s: %s" % (path.relative_to(ROOT), needle))
        self.assertEqual(offenders, [],
                         "no doctrine .md may instruct a compact-request call (#1084): %r"
                         % offenders)

    def test_live_lane_nudge_carries_no_compact_request(self):
        # the watchdog lane-check nudge is machine-EMITTED into armed /goal
        # sessions — a live doctrine surface the .md scan above cannot see.
        from watchdog import lane_resources
        text = lane_resources._lane_nudge_text(10, 2, {"total": 5})
        self.assertNotIn("compact-request", text)
        self.assertNotIn("compact --self", text)


if __name__ == "__main__":
    unittest.main()
