"""#1137 — no machinery files a ticket on its own.

Owner directive on #1137: airuleset generates far too many tickets. Every
piece of machinery that filed one by itself stops; its signal moves to a
surface that already exists — a journal line, the footer, or the CURRENT
ticket (the #1259 rule: one owner request = one ticket).
"""
import importlib.util
import inspect
import json
import re
import subprocess
import sys
import tempfile
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402
from test_scope_gate import body_cmd, run as run_filing_gate  # noqa: E402

import airuleset  # noqa: E402
import cli_onboard as ob  # noqa: E402
import model_fallback as mf  # noqa: E402
import watchdog as wd  # noqa: E402
import watchdog.disk_guard as dg  # noqa: E402
import watchdog.disk_guard_escalation as esc  # noqa: E402
from watchdog import idle_pane, mdreview_cadence as mc, model_restore as mr  # noqa: E402
from watchdog import resource_guard as rg  # noqa: E402
from watchdog.send_outcome import SendOutcome  # noqa: E402


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _norm(text):
    return re.sub(r"\s+", " ", text)


class _FilerSpy:
    """Records every gh-issue / gk-request subprocess and refuses it."""

    def __init__(self):
        self.calls = []
        self._real = subprocess.run

    def __call__(self, argv, *a, **kw):
        toks = [str(x) for x in argv] if isinstance(argv, (list, tuple)) else [str(argv)]
        if "gk-request" in toks or toks[:2] == ["gh", "issue"]:
            self.calls.append(toks)
            return subprocess.CompletedProcess(argv, 1, "", "blocked by test")
        return self._real(argv, *a, **kw)


def _statvfs(used_pct):
    blocks = 1_000_000
    free = int(blocks * (100 - used_pct) / 100)
    return lambda _m: types.SimpleNamespace(
        f_frsize=4096, f_bsize=4096, f_blocks=blocks, f_bfree=free,
        f_bavail=free, f_files=1_000_000, f_ffree=500_000, f_favail=500_000)


# --------------------------------------------------------------------------- #
# 1. Job 40 disk guard: a SEVERE journal line, never a ticket
# --------------------------------------------------------------------------- #
class DiskGuardSevere(unittest.TestCase):

    def test_96_pct_journals_severe_and_files_nothing(self):
        spy = _FilerSpy()
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.object(subprocess, "run", spy):
            logs = dg.run_disk_guard(
                now=5000.0, home=home, dry_run=False, statvfs_fn=_statvfs(96),
                dev_fn=lambda _p: 1, geteuid_fn=lambda: 1000, mounts=("/",),
                planners_fn=lambda _h, _n: [("noop", lambda: [])],
                scratch_discover_fn=lambda _now, _home: [],
                top_consumers_fn=lambda *a, **k: [("/big/consumer", 700_000_000)])
        self.assertEqual(spy.calls, [])
        severe = [ln for ln in logs if " SEVERE " in ln]
        self.assertEqual(len(severe), 1, logs)
        self.assertIn("/big/consumer", severe[0])
        self.assertFalse(any("TICKET" in ln or "gk-request" in ln for ln in logs), logs)

    def test_no_filer_surface_is_left(self):
        self.assertNotIn("severe_run_fn", inspect.signature(dg.run_disk_guard).parameters)
        for name in ("file_severe_ticket", "SEVERE_TICKET_STATE_NAME",
                     "_severe_ticket_recently_filed"):
            self.assertFalse(hasattr(dg, name), name)
        for name in ("file_infra", "airuleset_filer_argv", "still_open", "resolve_target"):
            self.assertFalse(hasattr(esc, name), name)
        src = inspect.getsource(esc)
        self.assertNotIn("gk-request\"", src)
        self.assertNotIn('"create"', src)

    def test_severe_line_names_what_the_drain_left(self):
        with tempfile.TemporaryDirectory() as home:
            lls = {"path": "/tmp/claude-1000/-home-x/90bc51f3", "bytes": 8_000_000_000}
            logs = dg.severe_journal(
                {"worst_pct": 96, "dim": "bytes", "largest_live_scratch": lls,
                 "drain_skipped_rungs": [{"rung": "scratch", "path": "/s", "reason": "live"}]},
                home, 5000.0, [("/big/consumer", 700_000_000)], box_class="workstation",
                critical_pct=90)
        self.assertEqual(len(logs), 1, logs)
        self.assertIn(" SEVERE ", logs[0])
        for want in ("/big/consumer", lls["path"], "scratch", "live"):
            self.assertIn(want, logs[0])

    def test_trigger_bands(self):
        with tempfile.TemporaryDirectory() as home:
            def sev(status, box_class="workstation"):
                return dg.severe_journal(status, home, 5000.0, [], dry_run=True,
                                         box_class=box_class, critical_pct=90)
            self.assertEqual(sev({"worst_pct": 93, "dim": "bytes"}), [])
            self.assertTrue(sev({"worst_pct": 91, "dim": "bytes", "drain_exhausted": True}))
            self.assertEqual(sev({"worst_pct": 91, "dim": "bytes", "drain_exhausted": True},
                                 box_class="shared-stream"), [])
            self.assertEqual(sev({"worst_pct": 89, "dim": "bytes", "drain_exhausted": True}), [])
            self.assertFalse((Path(home) / ".claude").exists(), "dry-run writes nothing")


# --------------------------------------------------------------------------- #
# 2. Job 54 model restore: the confirmed restore is a journal line
# --------------------------------------------------------------------------- #
MANAGED = "claude-opus-5-5[1m]"
NOW = 1_800_000_000


def _assistant(model, content=None, uid="a", ts="2026-09-28T14:51:50Z"):
    return {"type": "assistant", "uuid": uid, "timestamp": ts,
            "message": {"model": model, "content": content or [{"type": "text", "text": "x"}]}}


def _marker():
    return _assistant("claude-opus-4-8", [{"type": "fallback",
                                           "from": {"model": "claude-opus-5-5"},
                                           "to": {"model": "claude-opus-4-8"}}], "m1")


def _model_cmd():
    return {"type": "user", "message": {"content": "<command-name>/model</command-name>\n"
            "<command-message>model</command-message>\n<command-args>%s</command-args>"
            % MANAGED}}


class ModelRestoreNoTicket(unittest.TestCase):

    def test_no_filer_surface_is_left(self):
        self.assertNotIn("file_ticket", inspect.signature(mr.restore_job).parameters)
        self.assertNotIn("sub_run", inspect.signature(mr.run_job).parameters)
        for name in ("file_ticket", "compose_ticket", "FILE_MAX_TRIES"):
            self.assertFalse(hasattr(mr, name), name)

    def test_a_restored_fallback_episode_is_done(self):
        self.assertTrue(mr._done({"restored_at": NOW, "proven": True}))

    def test_confirmed_restore_journals_the_window(self):
        with tempfile.TemporaryDirectory() as d:
            tpath = Path(d) / "sid1.jsonl"
            cwd = Path(d) / "proj"
            cwd.mkdir()

            def write(entries, mode="w"):
                with open(tpath, mode, encoding="utf-8") as fh:
                    for e in entries:
                        fh.write(json.dumps(e) + "\n")

            def sweep(state, now):
                return mr.restore_job(
                    now, state, [("%1", str(cwd))], managed=MANAGED,
                    find_transcript=lambda c, ex=None: str(tpath),
                    read_state=mf.tail_state, verdict=mf.verdict, short_name=mf.short_name,
                    ready=lambda pid, c, live_ok: (True, "", "sid1", str(tpath)),
                    deliver=lambda pid, t, text: SendOutcome("submitted"),
                    outcome_kind=idle_pane.outcome_kind, delivered=idle_pane.DELIVERED,
                    typed_not_delivered=idle_pane.TYPED_NOT_DELIVERED, handled=set())

            spy = _FilerSpy()
            state = {}
            with mock.patch.object(subprocess, "run", spy):
                write([_marker()])
                sweep(state, NOW)
                write([_model_cmd(), _assistant("claude-opus-5-5", uid="new")], "a")
                logs = sweep(state, NOW + 300)
        restored = [ln for ln in logs if "RESTORED" in ln]
        self.assertEqual(len(restored), 1, logs)
        self.assertIn("2026-09-28T14:51:50Z", restored[0])
        self.assertIn("re-review", restored[0])
        self.assertEqual(spy.calls, [])


# --------------------------------------------------------------------------- #
# 3. Job 43 mdreview cadence: never creates, never reopens
# --------------------------------------------------------------------------- #
class MdreviewNoCreate(unittest.TestCase):

    def _job(self, state, view):
        calls = []

        def gh(argv):
            calls.append([str(x) for x in argv])
            if "view" in argv:
                return json.dumps(view), 0
            if "create" in argv:
                return "https://github.com/zbynekdrlik/airuleset/issues/900\n", 0
            return "", 0

        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "state.json"
            if state is not None:
                sp.write_text(json.dumps(state), encoding="utf-8")
            with mock.patch("socket.gethostname", return_value="dev1"), \
                    mock.patch("cli_mdreview_audit.save_artifact", return_value="/x/a.json"):
                logs = mc.mdreview_cadence_job(
                    time.time(), {}, state_path=str(sp), gh_runner=gh,
                    audit_fn=lambda fleet=True: {"schema": 1, "date": "2026-10-26",
                                                 "boxes": [], "failed": [], "skipped": []})
            after = json.loads(sp.read_text()) if sp.exists() else {}
        return logs, calls, after

    def test_no_create_or_reopen_surface(self):
        for name in ("bootstrap_ticket", "act_on_due", "BOOTSTRAP_TITLE"):
            self.assertFalse(hasattr(mc, name), name)

    def test_missing_ticket_is_a_journal_line(self):
        logs, calls, after = self._job(None, {})
        self.assertFalse(any("create" in c for c in calls), calls)
        self.assertNotIn("ticket", after)
        self.assertTrue(any("no tracking ticket" in ln for ln in logs), logs)

    def test_closed_ticket_is_never_reopened(self):
        logs, calls, _after = self._job(
            {"ticket": 999, "last_eval_ts": 0},
            {"state": "CLOSED", "closedAt": "2026-01-01T00:00:00Z"})
        self.assertFalse(any("reopen" in c or "comment" in c for c in calls), calls)
        self.assertTrue(any("#999" in ln and "CLOSED" in ln for ln in logs), logs)

    def test_open_ticket_keeps_the_daily_delta(self):
        logs, calls, after = self._job({"ticket": 999, "last_eval_ts": 0}, {"state": "OPEN"})
        self.assertFalse(any("reopen" in c or "create" in c for c in calls), calls)
        self.assertTrue(any("target-governance" in ln for ln in logs), logs)
        self.assertIn("target_governance", after)


# --------------------------------------------------------------------------- #
# 4. Job 39 resource guard: journal line only
# --------------------------------------------------------------------------- #
class ResourceGuardJournal(unittest.TestCase):

    def test_no_gk_request_surface(self):
        self.assertNotIn("gk_request_fn", inspect.signature(rg.resource_guard_verify).parameters)
        self.assertFalse(hasattr(airuleset, "_watchdog_resource_guard_gk_request"))
        params = inspect.signature(wd.run_once).parameters
        self.assertNotIn("resource_guard_gk_request", params)
        self.assertIn("resource_guard_enabled", params)

    def test_unlimited_is_one_journal_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = rg.resource_guard_verify(
                uid=1234, cgroup_read=lambda uid: "max",
                box_class_fn=lambda: "shared-stream",
                marker_path=str(Path(tmp) / "stamp"), now=1000)
        self.assertEqual(len(logs), 1, logs)
        self.assertIn("UNLIMITED", logs[0])
        self.assertFalse(any("gk" in ln for ln in logs), logs)


# --------------------------------------------------------------------------- #
# 5. legacy newlevel: no filing step, no injected notice
# --------------------------------------------------------------------------- #
class LegacyNewlevel(unittest.TestCase):

    def test_recipe_files_nothing(self):
        text = _read("skills/onboard-project/legacy-newlevel.md")
        self.assertNotIn("airuleset ticket", text)
        self.assertNotIn("gh issue create", text)
        self.assertIn("accounts transfer-session", text)

    def test_no_injected_migration_notice(self):
        conf = _read("hooks/situational-triggers.conf")
        self.assertNotIn("legacy-newlevel", conf)
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.dumps({
                "hook_event_name": "PreToolUse", "tool_name": "Edit",
                "tool_input": {"file_path": "/home/newlevel/devel/camera-box/src/x.rs",
                               "old_string": "a", "new_string": "b"},
                "session_id": "t1137-" + uuid.uuid4().hex[:10]})
            r = subprocess.run(["bash", str(ROOT / "hooks" / "inject-situational-rule.sh")],
                               input=payload, capture_output=True, text=True,
                               env=hermetic_hook_env(self, TMPDIR=tmp))
        self.assertNotIn("legacy newlevel project", r.stdout)


# --------------------------------------------------------------------------- #
# 6. onboarding: no notice ticket (foundation-gap tickets stay)
# --------------------------------------------------------------------------- #
class OnboardingNoNotice(unittest.TestCase):

    def test_no_notification_step(self):
        self.assertNotIn("notification_ticket", ob.STEP_ORDER)
        self.assertIn("foundation_tickets", ob.STEP_ORDER)
        for name in ("step_notification_ticket", "ONBOARD_TICKET_TITLE"):
            self.assertFalse(hasattr(ob, name), name)
        self.assertNotIn("notification", inspect.getsource(ob.onboard_project))

    def test_skill_lists_no_notice_ticket(self):
        text = _read("skills/onboard-project/SKILL.md")
        self.assertNotIn("onboarding notification ticket", text)
        self.assertIn("foundation-gap tickets", text)


# --------------------------------------------------------------------------- #
# 7. install guard: ask the owner / declare the tool, never file a ticket
# --------------------------------------------------------------------------- #
class InstallGuardMessage(unittest.TestCase):

    def test_message_never_says_file_a_ticket(self):
        spec = importlib.util.spec_from_file_location(
            "pa_guard_1137", ROOT / "hooks" / "project_account_install_guard.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertNotIn("gh issue create", mod.MESSAGE)
        self.assertNotIn("tiket", mod.MESSAGE)
        self.assertIn('SERVICE_ACCOUNTS["{user}"]["tools"]', mod.MESSAGE)
        self.assertIn("owner", mod.MESSAGE)


# --------------------------------------------------------------------------- #
# 8. architecture REWORK: recorded on the current ticket, no cap exemption
# --------------------------------------------------------------------------- #
class ReworkOnCurrentTicket(unittest.TestCase):

    def test_doctrine_never_auto_files(self):
        arch = _read("modules/quality/architecture-first.md")
        self.assertNotIn("architecture-rework", arch)
        self.assertIn("No patchwork — area-reviewed at integration", arch)
        self.assertIn("current ticket", arch)
        auto = _norm(_read("skills/autopilot/SKILL.md"))
        self.assertNotIn("Scope-gate: architecture-rework", auto)
        self.assertNotIn("files an `architecture-rework` ticket", auto)
        deep = _norm(_read("skills/completion-report-deep/DEEP.md"))
        self.assertNotIn("files the `architecture-rework` ticket", deep)

    def test_no_cap_exemption_left(self):
        from gates.filing import caps, parse, render
        self.assertEqual(parse.EXEMPT_FROM_CAP, {"user-request"})
        self.assertFalse(hasattr(caps, "UNCAPPED_ALWAYS"))
        self.assertNotIn("architecture-rework", parse.ALLOWED)
        self.assertNotIn("architecture-rework", render.BLOCK_MSG)

    def test_retired_criterion_names_its_own_reason(self):
        home = tempfile.mkdtemp(prefix="t1137-")
        cwd = tempfile.mkdtemp(prefix="t1137-cwd-")
        r = run_filing_gate(body_cmd("rework x", "Area: watchdog\nPatchwork signs: x.",
                                     scope_gate="architecture-rework",
                                     labels=["architecture-rework"]),
                            home=home, cwd=cwd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("-> architecture-rework-retired", r.stderr)


# --------------------------------------------------------------------------- #
# 9. routine-filing doctrine: record it on the current ticket
# --------------------------------------------------------------------------- #
class DoctrineRecordsOnTicket(unittest.TestCase):

    def test_modules(self):
        dd = _read("modules/quality/durable-decisions-to-tickets.md")
        self.assertNotIn("`gh issue create` one to carry it", dd)
        self.assertIn("create it only if none exists", dd)
        cpw = _read("modules/core/complete-planned-work.md")
        self.assertNotIn("cite the filed issue", cpw)
        self.assertIn("current ticket", cpw)

    def test_skills(self):
        banned = {
            "skills/complete-planned-work-deep/DEEP.md": [
                "create a GitHub issue for it BEFORE", 'gh issue create --title "TODO:',
                "first running `gh issue create`"],
            "skills/no-dropped-work-deep/DEEP.md": ["or `gh issue create` describing"],
            "skills/completion-report-deep/DEEP.md": ["file a GitHub issue with a clear title"],
            "skills/ci-push-discipline/SKILL.md": ["file an issue to add", "file the issue"],
            "skills/version-on-dashboard/SKILL.md": ["gh issue create", "File it as the FIRST issue",
                                                     "File the foundation issue"],
            "skills/autopilot/SKILL.md": ["foundation issue is the FIRST work item"],
            "skills/ask-before-assuming-deep/DEEP.md": ["NEVER needs approval",
                                                        "work to be filed immediately"],
        }
        for rel, phrases in banned.items():
            text = _norm(_read(rel))
            for p in phrases:
                self.assertNotIn(p, text, rel)

    def _hook(self, text):
        sid = "t1137-ndw-" + uuid.uuid4().hex[:10]
        self.addCleanup(lambda: Path("/tmp/airuleset-untracked-work-block-" + sid).unlink(
            missing_ok=True))
        p = subprocess.run(["bash", str(ROOT / "hooks" / "stop-check-untracked-work.sh")],
                           input=json.dumps({"session_id": sid, "last_assistant_message": text}),
                           capture_output=True, text=True, env=hermetic_hook_env(self))
        self.assertIn('"block"', p.stdout)
        return json.loads(p.stdout)["reason"]

    def test_hook_dismissal_says_record_on_current_ticket(self):
        reason = self._hook("The failing lint test is pre-existing, so I left it.")
        self.assertNotIn("gh issue create", reason)
        self.assertIn("current ticket", reason)

    def test_hook_ask_to_file_drops_the_permission_wording(self):
        reason = self._hook("I drafted 3 issues. Should I create the issues or hold off?")
        self.assertNotIn("NEVER needs approval", reason)
        self.assertNotIn("NON-DESTRUCTIVE", reason)
        self.assertIn("current ticket", reason)


if __name__ == "__main__":
    unittest.main()
