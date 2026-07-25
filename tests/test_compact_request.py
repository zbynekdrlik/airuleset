"""Krok 1c — ohraničenie kontextu (#39 follow-up, 2026-07-25).

Originally two pieces of the same context-diet package; the FIRST piece was
REVERTED the same day by a correction batch (see
TestManagedAutoCompactWindowReverted below) — a low auto-compact threshold
cuts big tasks off mid-work and defeats the point of the 1M context window.
Context is bounded at TICKET BOUNDARIES instead (piece 2, kept):

  1. ~~`MANAGED_AUTOCOMPACT_WINDOW`~~ — REVERTED. `apply_managed_settings_defaults`
     now actively STRIPS `autoCompactWindow` from settings.json on every
     deploy instead of setting it, so the 6 managed boxes go back to Claude
     Code's own default.

  2. Ticket-boundary /compact — a completed-ticket report is a SAFE
     compaction boundary (the ticket's durable state already lives in git /
     GitHub / the issue). A Stop hook (notify-compact-request.sh) records a
     request the MOMENT a turn's final message is a completed-ticket report;
     watchdog job 14 (compact_ticket_boundary) types `/compact` into that
     session's pane once it goes genuinely idle, reusing job 12's (model
     reconcile) exact idle guards. Since the 2026-07-25 correction batch, a
     per-BATCH ✅ DONE inside an `/autopilot` loop (not just the whole
     backlog) is a real completion report, so this fires once per batch.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import watchdog as wd


# --------------------------------------------------------------------------- #
# 1. MANAGED_AUTOCOMPACT_WINDOW
# --------------------------------------------------------------------------- #

class TestManagedAutoCompactWindowReverted(unittest.TestCase):
    """MANAGED_AUTOCOMPACT_WINDOW is REVERTED (2026-07-25 correction batch): a
    low auto-compact threshold cuts big tasks off MID-WORK, defeating the
    point of the 1M context window. Context is bounded at TICKET BOUNDARIES
    instead (the per-batch ✅ DONE + ticket-boundary /compact, job 14) — never
    by an artificial window. `apply_managed_settings_defaults` must actively
    STRIP `autoCompactWindow` from settings.json on the next deploy, not just
    stop setting it (an already-deployed 300000 must not silently persist)."""

    def test_constant_no_longer_exists(self):
        self.assertFalse(hasattr(airuleset, "MANAGED_AUTOCOMPACT_WINDOW"))

    def test_key_absent_on_a_fresh_settings_dict(self):
        out = airuleset.apply_managed_settings_defaults({})
        self.assertNotIn("autoCompactWindow", out)

    def test_key_actively_stripped_from_an_already_deployed_settings_file(self):
        # the exact regression this reverts: a PRIOR install already wrote
        # autoCompactWindow=300000 — the NEXT install must remove it, not
        # leave it sitting there because nothing "sets" it anymore.
        out = airuleset.apply_managed_settings_defaults(
            {"autoCompactWindow": 300000})
        self.assertNotIn("autoCompactWindow", out)

    def test_preserves_other_keys(self):
        out = airuleset.apply_managed_settings_defaults(
            {"hooks": {"Stop": []}, "model": "claude-opus-5[1m]",
             "autoCompactWindow": 155000})
        self.assertEqual(out["hooks"], {"Stop": []})
        self.assertEqual(out["model"], "claude-opus-5[1m]")
        self.assertNotIn("autoCompactWindow", out)


# --------------------------------------------------------------------------- #
# 2a. Compact-request state (record / load / clear)
# --------------------------------------------------------------------------- #

class CompactRequestState(unittest.TestCase):
    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "compact-requests.json")

    def test_record_and_load(self):
        p = self._p()
        self.assertTrue(
            wd.record_compact_request("sess-1", "/home/x/proj", now=1000, path=p))
        d = wd.load_compact_requests(p)
        self.assertEqual(d["sess-1"]["cwd"], "/home/x/proj")
        self.assertEqual(d["sess-1"]["ts"], 1000)

    def test_missing_session_is_rejected(self):
        p = self._p()
        self.assertFalse(wd.record_compact_request("", "/x", path=p))
        self.assertEqual(wd.load_compact_requests(p), {})

    def test_later_request_overwrites_earlier_for_the_same_session(self):
        # only the LATEST ticket boundary matters — a session that completes
        # a second ticket before the watchdog picks up the first request
        # should get compacted at the NEWER boundary, not lose the request.
        p = self._p()
        wd.record_compact_request("sess-1", "/x", now=1, path=p)
        wd.record_compact_request("sess-1", "/y", now=2, path=p)
        d = wd.load_compact_requests(p)
        self.assertEqual(len(d), 1)
        self.assertEqual(d["sess-1"]["cwd"], "/y")
        self.assertEqual(d["sess-1"]["ts"], 2)

    def test_clear_removes_one_request_only(self):
        p = self._p()
        wd.record_compact_request("sess-1", "/x", path=p)
        wd.record_compact_request("sess-2", "/y", path=p)
        self.assertTrue(wd.clear_compact_request("sess-1", path=p))
        d = wd.load_compact_requests(p)
        self.assertNotIn("sess-1", d)
        self.assertIn("sess-2", d)

    def test_clear_absent_session_returns_false(self):
        p = self._p()
        self.assertFalse(wd.clear_compact_request("nope", path=p))

    def test_load_bad_file_is_empty(self):
        p = self._p()
        Path(p).write_text("not json")
        self.assertEqual(wd.load_compact_requests(p), {})

    def test_load_missing_file_is_empty(self):
        p = self._p()
        self.assertEqual(wd.load_compact_requests(p), {})

    def test_compact_requests_path_resolved_at_call_time(self):
        # mirrors burn.burn_history_dir()'s documented reasoning: Path.home()
        # must be read when the function is INVOKED, never frozen at import
        # time — otherwise a long-lived watchdog process could never pick up
        # a changed $HOME (and a test patching HOME would silently miss it).
        with m.patch.dict(os.environ, {"HOME": "/tmp/fake-home-x"}):
            self.assertEqual(wd.compact_requests_path(),
                             Path("/tmp/fake-home-x") / ".claude" / "compact-requests.json")


# --------------------------------------------------------------------------- #
# 2b. Job 14 — compact_ticket_boundary
# --------------------------------------------------------------------------- #

CB_IDLE_CAP = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
CB_BUSY_CAP = ("● Baking…\n✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n"
              "  ctx ███░  caveman:lite\n")
CB_DIALOG_CAP = ("● Claude asked:\n  · Ktorá možnosť?\n     1. A\n     2. B\n"
                 "  Tab/Arrow keys to navigate · Enter to select\n")
CB_DRAFT_CAP = "● Hotovo.\n❯ rozpisany draft\n  ctx ███░  caveman:lite\n"


class CompactFakeTmux:
    """Fake `run` for a single pane. Tracks every send-keys call — same shape
    as job 12's RestartFakeTmux (test_watchdog.py) but without the
    list-panes / transcript machinery job 14 doesn't need (it's fed
    panes_by_sid directly, same injection pattern as
    deliver_discord_replies)."""

    def __init__(self, captured, in_mode=False):
        self.captured = captured
        self.in_mode = in_mode
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "display-message" in j:
            if argv[-1] == "#{pane_in_mode}":
                return "1" if self.in_mode else "0"
            return "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            return ""
        if "capture-pane" in j:
            return self.captured
        return ""

    def typed_texts(self):
        return [a[-1] for a in self.sent if "-l" in a]

    def keys(self):
        return [a[-1] for a in self.sent]

    def no_consecutive_escapes(self):
        ks = self.keys()
        return not any(ks[i] == "Escape" and ks[i + 1] == "Escape"
                       for i in range(len(ks) - 1))


class TestCompactTicketBoundary(unittest.TestCase):
    PANE = "%9"
    SID = "sess-abc"

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "compact-requests.json")

    def _go(self, captured, in_mode=False, dry_run=False, seed=True, path=None):
        path = path or self._p()
        if seed:
            wd.record_compact_request(self.SID, "/home/x/proj", path=path)
        tmux = CompactFakeTmux(captured, in_mode=in_mode)
        panes_by_sid = {self.SID: (self.PANE, captured)}
        state = {}
        logs = wd.compact_ticket_boundary(time.time(), tmux, state, panes_by_sid,
                                          dry_run=dry_run, path=path)
        return tmux, logs, path

    def test_idle_pane_gets_compact_typed(self):
        tmux, logs, path = self._go(CB_IDLE_CAP)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)

    def test_success_removes_the_request_dedup(self):
        tmux, logs, path = self._go(CB_IDLE_CAP)
        self.assertEqual(wd.load_compact_requests(path), {})

    def test_busy_pane_is_skipped_and_request_kept_for_retry(self):
        tmux, logs, path = self._go(CB_BUSY_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("busy" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_draft_pane_is_never_typed_over(self):
        tmux, logs, path = self._go(CB_DRAFT_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("draft" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_open_dialog_is_skipped(self):
        tmux, logs, path = self._go(CB_DIALOG_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("dialog" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_in_mode_pane_is_skipped(self):
        tmux, logs, path = self._go(CB_IDLE_CAP, in_mode=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("in-mode" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_dry_run_never_sends_keys_or_consumes_the_request(self):
        tmux, logs, path = self._go(CB_IDLE_CAP, dry_run=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any(ln.startswith("READY") for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_no_pending_requests_is_a_noop(self):
        path = self._p()
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(time.time(), tmux, {}, {}, path=path)
        self.assertEqual(logs, [])
        self.assertEqual(tmux.sent, [])

    def test_session_with_no_live_pane_is_retried_later(self):
        path = self._p()
        wd.record_compact_request(self.SID, "/x", path=path)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(time.time(), tmux, {}, {}, path=path)
        self.assertEqual(tmux.sent, [])
        self.assertIn(self.SID, wd.load_compact_requests(path))
        self.assertTrue(any("no-pane" in ln for ln in logs), logs)

    def test_exact_keystrokes_text_then_enter_only(self):
        tmux, logs, path = self._go(CB_IDLE_CAP)
        self.assertEqual(tmux.keys(), ["/compact", "Enter"])

    def test_no_two_consecutive_escapes_ever_sent(self):
        for cap in (CB_IDLE_CAP, CB_BUSY_CAP, CB_DRAFT_CAP, CB_DIALOG_CAP):
            tmux, _, _ = self._go(cap)
            self.assertTrue(tmux.no_consecutive_escapes())

    def test_multiple_pending_requests_each_handled_independently(self):
        path = self._p()
        wd.record_compact_request("sess-a", "/a", path=path)
        wd.record_compact_request("sess-b", "/b", path=path)
        tmux_a = CompactFakeTmux(CB_IDLE_CAP)
        tmux_b = CompactFakeTmux(CB_BUSY_CAP)
        panes_by_sid = {"sess-a": ("%1", CB_IDLE_CAP), "sess-b": ("%2", CB_BUSY_CAP)}

        # a single shared fake run() dispatches by pane id so both panes'
        # distinct captured text is honoured in one call.
        def run(argv, timeout=8):
            pid = argv[argv.index("-t") + 1] if "-t" in argv else None
            fake = tmux_a if pid == "%1" else tmux_b
            return fake(argv, timeout=timeout)

        wd.compact_ticket_boundary(time.time(), run, {}, panes_by_sid, path=path)
        remaining = wd.load_compact_requests(path)
        self.assertNotIn("sess-a", remaining)   # idle → handled, dedup'd
        self.assertIn("sess-b", remaining)      # busy → kept for retry


# --------------------------------------------------------------------------- #
# 2c. run_once wiring — job 14 fires ONLY when compact_requests_path is given
# --------------------------------------------------------------------------- #

class RunOnceCompactRequestWiring(unittest.TestCase):
    def _tmp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_no_path_never_touches_compact_requests(self):
        tmp = self._tmp()
        proj = tmp / "projects"
        proj.mkdir()
        state_path = tmp / "state.json"

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=time.time(), dry_run=True, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(tmp / "pending-"))
        self.assertFalse(any("compact-request" in ln for ln in logs), logs)

    def test_path_given_is_wired_and_processes_pending_requests(self):
        tmp = self._tmp()
        proj = tmp / "projects"
        proj.mkdir()
        state_path = tmp / "state.json"
        creq_path = tmp / "compact-requests.json"
        # a request for a session with NO live pane this sweep — proves job
        # 14 actually ran (best-effort, "no-pane" skip) without needing the
        # full claude-pane/transcript simulation (the deep keystroke-level
        # behavior is covered directly by TestCompactTicketBoundary above,
        # mirroring how job 12's own run_once wiring test stays this thin).
        wd.record_compact_request("sess-x", "/home/newlevel/devel/demo",
                                  path=creq_path)

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=time.time(), dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(tmp / "pending-"),
                           compact_requests_path=creq_path)
        self.assertTrue(any("compact-request" in ln for ln in logs), logs)
        # never crashed, never consumed the un-actionable request
        self.assertIn("sess-x", wd.load_compact_requests(creq_path))


# --------------------------------------------------------------------------- #
# 2d. airuleset.py compact-request CLI (the Stop hook's write path)
# --------------------------------------------------------------------------- #

class TestCompactRequestCli(unittest.TestCase):
    def test_registered_in_subcommands(self):
        self.assertIn("compact-request", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.SUBCOMMANDS["compact-request"],
                      airuleset.cmd_compact_request)

    def test_record_writes_the_request_file(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        fake_home = Path(d.name)
        (fake_home / ".claude").mkdir()
        with m.patch.dict(os.environ, {"HOME": str(fake_home)}):
            class Args:
                record = True
                session = "sid-cli"
                cwd = "/x"
            airuleset.cmd_compact_request(Args())
        reqfile = fake_home / ".claude" / "compact-requests.json"
        self.assertTrue(reqfile.exists())
        d2 = json.loads(reqfile.read_text())
        self.assertIn("sid-cli", d2)
        self.assertEqual(d2["sid-cli"]["cwd"], "/x")


# --------------------------------------------------------------------------- #
# 2e. notify-compact-request.sh — the Stop hook that records the request
# --------------------------------------------------------------------------- #

class TestCompactRequestHook(unittest.TestCase):
    HOOK = airuleset.REPO_DIR / "hooks" / "notify-compact-request.sh"

    def _run(self, sid, msg, cwd=""):
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg,
                              "cwd": cwd})
        env = {**os.environ, "HOME": home}
        r = subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                           capture_output=True, env=env,
                           cwd=str(airuleset.REPO_DIR))
        return r, Path(home) / ".claude" / "compact-requests.json"

    def test_hook_exists_and_is_executable_bash(self):
        self.assertTrue(self.HOOK.exists())

    def test_work_complete_heading_records_a_request(self):
        r, reqfile = self._run(
            "sid-1", "## ✅ Work Complete\n\nfoo bar\n✅ DONE: hotovo", cwd="/x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(reqfile.exists())
        d = json.loads(reqfile.read_text())
        self.assertIn("sid-1", d)
        self.assertEqual(d["sid-1"]["cwd"], "/x")

    def test_terminal_done_marker_alone_records_a_request(self):
        r, reqfile = self._run("sid-2", "no heading here\n✅ DONE: hotovo", cwd="/y")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(reqfile.exists())
        self.assertIn("sid-2", json.loads(reqfile.read_text()))

    def test_blocked_on_user_never_records_even_with_the_heading(self):
        # manual-merge report: heading present, but the LAST line is ❓ — the
        # decision is still pending, this is NOT a safe compaction boundary.
        r, reqfile = self._run(
            "sid-3", "## ✅ Work Complete\n\n❓ NEEDS YOU: schváliš merge PR #5?")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_still_working_never_records(self):
        r, reqfile = self._run(
            "sid-4", "✅ DONE: #5 merged\n⏳ WORKING: pokračujem na #6")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_no_marker_never_records(self):
        r, reqfile = self._run("sid-5", "just some prose, nothing terminal")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_missing_session_id_never_records(self):
        r, reqfile = self._run("", "## ✅ Work Complete\n✅ DONE: x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_silent_stdout_always_exit_0(self):
        r, reqfile = self._run("sid-6", "## ✅ Work Complete\n✅ DONE: x")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_wired_into_stop_hooks_json(self):
        cfg = airuleset.load_hooks_json()
        cmds = [h.get("command", "")
               for entry in cfg.get("hooks", {}).get("Stop", [])
               for h in entry.get("hooks", [])]
        self.assertTrue(any("notify-compact-request.sh" in c for c in cmds), cmds)


if __name__ == "__main__":
    unittest.main()
