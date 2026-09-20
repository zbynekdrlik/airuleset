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
untouched — this lane removes the DELIVERY machinery only.

#1084 L2 (this file also carries the DELETION locks now that L2 has landed):
  - `deliver_compact` and the whole request/delivered/queued store, the
    submit-verify + sync-attempt helpers, and the cooldown/boundary/live-bg
    machinery are DELETED from `watchdog.compact` (asserted absent by
    `hasattr`) — `compact.py` shrinks to the pane resolvers + the observation
    helpers the surviving jobs read.
  - `airuleset.py compact-request` is UNREGISTERED — an unknown subcommand
    (argparse rejects it, `SystemExit`), not a stub; it is gone from the
    dispatch table and `cmd_compact_request` no longer exists.
  - the surviving symbols the other jobs reference are KEPT and intact:
    `pending_compact_hold` (now unconditionally `False` — no machine compact is
    ever pending), `_find_pane_for_session`, `resolve_self_pane`,
    `resolve_declared_window_pane`, `_COMPACT_COMPLETION_HEADING_RX`,
    `_compact_recent_human_activity`, `compact_sync_log_path`.
"""

import inspect
import json
import os
import shutil
import subprocess
import sys
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


# --------------------------------------------------------------------------- #
# (a) compact_sweep returns at its top, never delivers (L2: `deliver_compact`
#     and the request store are GONE, so the sweep CANNOT deliver by
#     construction — it just journals the removed line each sweep).
# --------------------------------------------------------------------------- #
class TestCompactSweepRemoved(unittest.TestCase):
    def test_journals_removed_line_and_issues_no_keystrokes(self):
        keys = []
        logs = compact.compact_sweep(
            1_000_005.0, run=lambda *a, **k: keys.append((a, k)),
            projects_dir=None)
        self.assertTrue(any("machine compacts removed" in ln and "#1084" in ln
                            for ln in logs),
                        "compact_sweep must journal the removed line: %r" % logs)
        self.assertEqual(keys, [], "compact_sweep must issue no keystrokes")

    def test_exact_removed_line(self):
        logs = compact.compact_sweep(1.0, run=None)
        self.assertIn(REMOVED_LINE, logs)

    def test_no_delivery_symbol_exists_to_call(self):
        # L2: there is no `deliver_compact` left for the sweep to call.
        self.assertFalse(hasattr(compact, "deliver_compact"),
                         "deliver_compact must be deleted in L2 (#1084)")


# --------------------------------------------------------------------------- #
# (a) run_once never forces a full sweep for a pending compact
# --------------------------------------------------------------------------- #
class TestSweepUrgentNeverForcedByCompact(unittest.TestCase):
    def test_run_once_caller_passes_compact_pending_false(self):
        # run_once must call sweep_urgent with compact_pending=False — a banned
        # behaviour cannot force a full sweep any more (#1084). L2: the
        # compact_requests_path plumbing is gone, so run_once takes no such kwarg.
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        state_path = Path(td.name) / "state.json"
        captured = {}
        real_su = wd.sweep_urgent

        def _spy(*a, **k):
            captured["compact_pending"] = k.get("compact_pending")
            return real_su(*a, **k)

        with m.patch.object(wd, "sweep_urgent", side_effect=_spy):
            wd.run_once(now=1060.0, state_path=str(state_path), dry_run=True)
        self.assertEqual(captured.get("compact_pending"), False,
                         "run_once must pass compact_pending=False (#1084)")

    def test_run_once_rejects_the_deleted_compact_requests_path_kwarg(self):
        # L2: the parameter is gone from run_once's signature.
        self.assertNotIn("compact_requests_path",
                         inspect.signature(wd.run_once).parameters,
                         "run_once must not carry the deleted compact_requests_path "
                         "plumbing (#1084 L2)")


# --------------------------------------------------------------------------- #
# (c) L2: `compact-request` is UNREGISTERED — an unknown subcommand, not a stub.
# --------------------------------------------------------------------------- #
class TestCompactRequestUnregistered(unittest.TestCase):
    def test_cmd_compact_request_function_is_gone(self):
        self.assertFalse(hasattr(airuleset, "cmd_compact_request"),
                         "cmd_compact_request must be deleted in L2 (#1084)")

    def test_not_in_the_dispatch_table(self):
        # The command dispatch dict (SUBCOMMANDS) no longer routes it.
        self.assertNotIn("compact-request", airuleset.SUBCOMMANDS,
                         "compact-request must not be a routable command (#1084 L2)")

    def test_argparse_rejects_the_subcommand(self):
        # An unknown subcommand -> argparse errors out with SystemExit (code 2)
        # during parse_args, before any dispatch.
        import contextlib
        import io
        with m.patch.object(sys, "argv",
                            ["airuleset", "compact-request", "--self"]):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    airuleset.main()


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

    def test_install_through_the_gates_is_and_chained_never_seq_or_or(self):
        # #1084 L1b review F2: the order lock alone would still pass if a future
        # edit swapped a `&&` joiner for `;` or `||` between install and the
        # gates — which would let a gate FAILURE (rc 87/88) NOT abort the target
        # (`;`) or be masked (`||`). Lock the joiners too: from `airuleset.py
        # install` through the Playwright call the (comment-stripped) source must
        # carry no `;` and no `||`. The `|| true` in `(gh auth setup-git…)` sits
        # BEFORE install, so the install-onward span excludes it.
        src = self._deploy_src()
        i_install = src.index("python3 airuleset.py install")
        i_pw_end = (src.index("_playwright_chromium_postcheck()", i_install)
                    + len("_playwright_chromium_postcheck()"))
        span = "\n".join(ln for ln in src[i_install:i_pw_end].splitlines()
                         if not ln.strip().startswith("#"))
        self.assertNotIn(";", span,
                         "install→gates must be &&-chained, never `;` "
                         "(a `;` would stop a gate rc 87/88 from aborting the target)")
        self.assertNotIn("||", span,
                         "install→gates must be &&-chained, never `||` "
                         "(an `||` would mask a gate failure)")

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


# --------------------------------------------------------------------------- #
# (f) L2 DELETION: the whole callback-compact delivery + store + cooldown +
#     queued + submit-verify + boundary/live-bg machinery is GONE from
#     watchdog.compact — nothing surviving references any of it.
# --------------------------------------------------------------------------- #
DELETED_COMPACT_SYMBOLS = (
    # delivery + submit-verify + sync attempt
    "deliver_compact", "_compact_sync_attempt", "_compact_submit_verified",
    "_compact_still_in_box", "_compact_post_send_classify",
    "_COMPACT_TERMINAL_WORDS", "_COMPACT_HOLD_EXTEND_WORDS",
    "_COMPACT_HOLD_HINT_WORDS", "COMPACT_TEXT", "COMPACT_BOUNDARY_HOLD_CMD",
    "COMPACT_SYNC_ATTEMPT_MARGIN_S",
    # request store
    "compact_requests_path", "load_compact_requests", "_save_compact_requests",
    "record_compact_request", "clear_compact_request", "has_pending_request",
    "_touch_compact_request_ts", "actionable_compact_requests",
    "compact_ignore_reason", "COMPACT_PENDING_HOLD_S", "COMPACT_REQUEST_STALE_S",
    # delivered / cooldown store
    "compact_delivered_path", "mark_compact_delivery_ts",
    "compact_delivery_in_cooldown", "compact_recently_compacted",
    "COMPACT_MIN_DELIVERY_INTERVAL_S", "COMPACT_RECENTLY_COMPACTED_VETO_S",
    "_compact_min_delivery_interval",
    # queued store
    "compact_queued_path", "mark_compact_queued_ts", "clear_compact_queued_ts",
    "compact_queued_since", "compact_queued_in_pane",
    # boundary / liveness / age / sync-log writer
    "_compact_not_at_boundary", "_compact_session_unresumed",
    "_compact_boundary_already_compacted", "_compact_duplicate_consume_reason",
    "_session_has_live_bg_tasks", "_live_bg_tasks_detail",
    "_safe_age", "_compact_min_request_age", "_compact_request_too_young",
    "COMPACT_MIN_REQUEST_AGE_S", "COMPACT_LIVE_WORKER_FRESHNESS_S",
    "_log_compact_sync", "COMPACT_SYNC_LOG_LINES_MAX",
)


class TestCompactMachineryDeleted(unittest.TestCase):
    def test_every_dead_symbol_is_gone(self):
        still = [s for s in DELETED_COMPACT_SYMBOLS if hasattr(compact, s)]
        self.assertEqual(still, [],
                         "L2 (#1084) must delete every dead compact symbol; "
                         "still present: %r" % still)

    def test_the_hooks_are_gone(self):
        # the two notify-compact hooks stay deleted (L1 already removed them).
        for name in ("notify-compact-request.sh",
                     "notify-compact-subagent-boundary.sh"):
            self.assertFalse((ROOT / "hooks" / name).exists(),
                             "%s must not exist (#1084)" % name)


# --------------------------------------------------------------------------- #
# (g) L2 KEPT: the pane-resolution + observation helpers surviving jobs read
#     stay defined and intact; `pending_compact_hold` is now unconditionally
#     False (no machine compact is ever pending — its store is gone).
# --------------------------------------------------------------------------- #
KEPT_COMPACT_SYMBOLS = (
    "compact_sweep", "pending_compact_hold", "_find_pane_for_session",
    "resolve_self_pane", "resolve_declared_window_pane",
    "_COMPACT_COMPLETION_HEADING_RX", "_compact_recent_human_activity",
    "compact_sync_log_path",
)


class TestObservationAndPaneHelpersKept(unittest.TestCase):
    def test_every_kept_symbol_is_present(self):
        missing = [s for s in KEPT_COMPACT_SYMBOLS if not hasattr(compact, s)]
        self.assertEqual(missing, [],
                         "L2 (#1084) must KEEP the helpers surviving jobs read; "
                         "missing: %r" % missing)

    def test_pending_compact_hold_is_unconditionally_false(self):
        # Even with a well-formed fresh request written directly to a store file
        # and the owner disable flag absent, no compact is ever pending now.
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        reqp = Path(td.name) / "compact-requests.json"
        now = 1_000_000.0
        reqp.write_text(json.dumps(
            {"sid-live": {"cwd": "/repo", "ts": int(now), "bts": int(now),
                          "origin": "self-callback"}}), encoding="utf-8")
        with m.patch.object(wd, "_owner_disabled", return_value=False):
            self.assertFalse(
                compact.pending_compact_hold("sid-live", now=now + 1,
                                             path=str(reqp)),
                "pending_compact_hold must be False in L2 — no machine compact "
                "is ever pending (#1084)")

    def test_observation_helpers_live_in_long_turn_untouched(self):
        # the OBSERVATION helpers the design keeps are re-exported via watchdog.
        self.assertTrue(hasattr(wd, "_pane_compacting"))
        self.assertTrue(hasattr(wd, "COMPACTING_MARKER"))
        self.assertTrue(hasattr(wd, "_QUEUED_COMPACT_RX"))


if __name__ == "__main__":
    unittest.main()
