"""#1260 review round 1 — a thrash-stopped session stays quiet for EVERY machine
nudge path (not only job 1's `continue`), the owner is told once, a thrashing
subagent is not resumed, the hold also reads CC's structured `apiError`, the
settings writer never mutates its input and strips the per-model effort, and
every self-review path reads the gk lens files explicitly.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402
import cli_config  # noqa: E402
from test_autocompact_thrash_1260 import THRASH_ENTRY, _FakeTmux  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SID = "5e55abc0-51d0-4a5e-9f1e-00000000abcd"
REWORDED = {"type": "assistant", "isApiErrorMessage": True,
            "apiError": "autocompact_thrashing", "error": "invalid_request",
            "message": {"role": "assistant", "model": "<synthetic>",
                        "content": [{"type": "text", "text": "Context keeps refilling."}]}}


def _write(path, entries, age_s, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    os.utime(path, (now - age_s, now - age_s))
    return path


class _Watchdog(TestCase):
    def setUp(self):
        import watchdog
        self.w = watchdog
        self.tmp = tempfile.mkdtemp()
        self.projects = Path(self.tmp) / "projects"
        self.projects.mkdir()
        self.state = str(Path(self.tmp) / "state.json")
        self.pings = []
        orig = self.w.usage._USAGE_CACHE_PATH
        self.w.usage._USAGE_CACHE_PATH = str(Path(self.tmp) / "usage-cache.json")
        self.addCleanup(setattr, self.w.usage, "_USAGE_CACHE_PATH", orig)

    def _send(self, body, owner=None, dedup_key=None, dry_run=False):
        self.pings.append((body, dedup_key))
        return "sent"

    def _tpath(self, cwd):
        return self.projects / self.w.encode_project_dir(cwd) / (SID + ".jsonl")


class JobOneTellsTheOwnerOnce(_Watchdog):
    def setUp(self):
        super().setUp()

        def _typing(pid, text, run=None, tpath=None, **_kw):
            run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
            return True
        p = m.patch.object(self.w, "send_verified", _typing)
        p.start()
        self.addCleanup(p.stop)

    def _sweeps(self, entry, cwd):
        now = 1_000_000
        _write(self._tpath(cwd), [entry], 600, now)
        fake = _FakeTmux("%5\tclaude\t" + cwd + "\n")
        logs = []
        for step in (0, 300, 900, 3600):
            logs += self.w.run_once(now=now + step, run=fake, send_fn=self._send,
                                    projects_dir=self.projects, state_path=self.state,
                                    grace=300, interval=300, max_nudges=3)
        return fake, logs

    def test_one_notice_and_one_journal_line_per_episode(self):
        fake, logs = self._sweeps(THRASH_ENTRY, "/devel/held")
        self.assertEqual(fake.continues_sent(), 0)
        self.assertEqual(len([p for p in self.pings if "thrash" in (p[1] or "")]), 1, self.pings)
        self.assertEqual(len([ln for ln in logs if "autocompact-thrash" in ln]), 1, logs)

    def test_a_reworded_banner_is_still_held_by_the_structured_field(self):
        fake, _logs = self._sweeps(REWORDED, "/devel/reworded")
        self.assertEqual(fake.continues_sent(), 0, "apiError autocompact_thrashing = held")


class NoMachineNudgeWakesAHeldSession(_Watchdog):
    def test_send_verified_refuses_a_machine_nudge(self):
        tpath = _write(self._tpath("/devel/held"), [THRASH_ENTRY], 600, 1_000_000)
        fake = _FakeTmux("%5\tclaude\t/devel/held\n")
        logs = []
        out = self.w.send_verified("%5", "nudge: [partition-audit] x", fake, str(tpath),
                                   sleep_fn=lambda s: None, logs=logs, nudge="partition-audit")
        self.assertFalse(out)
        self.assertEqual(fake.sent, [], "nothing typed into a thrash-held pane")
        self.assertTrue(any("thrash" in ln for ln in logs), logs)

    def test_the_owner_s_own_reply_still_goes_through_the_normal_path(self):
        tpath = _write(self._tpath("/devel/held"), [THRASH_ENTRY], 600, 1_000_000)
        calls = []

        def run(argv, timeout=8):
            calls.append(argv)
            return ""
        logs = []
        self.w.send_verified("%5", "áno, pokračuj", run, str(tpath), sleep_fn=lambda s: None,
                             logs=logs, user_authored=True)
        self.assertFalse(any("thrash" in ln for ln in logs), logs)
        self.assertTrue(any(a[:2] == ["tmux", "capture-pane"] for a in calls))

    def test_idle_pane_gate_refuses(self):
        from watchdog import idle_pane
        tpath = _write(self._tpath("/devel/held"), [THRASH_ENTRY], 600, 1_000_000)
        ready, why, _sid, _tp = idle_pane.pane_ready(
            "%5", "/devel/held", "checkout-lag", projects_dir=self.projects,
            find_transcript=lambda pd, cwd: str(tpath), capture=lambda pid: "❯ ",
            in_mode=lambda pid: False, at_idle=lambda c: True, busy_waiting=lambda c: False,
            turn_live=lambda t: False, recent_human=lambda *a: False,
            gate_ok=lambda *a: True, handled=set(), state={}, now=1_000_000)
        self.assertFalse(ready)
        self.assertEqual(why, "thrash-held")

    def test_the_stash_route_refuses(self):
        from watchdog import send_outcome
        cwd = "/devel/held"
        _write(self._tpath(cwd), [THRASH_ENTRY], 600, 1_000_000)

        def run(argv, timeout=8):
            if argv[:2] == ["tmux", "display-message"] and argv[-1] == "#{pane_current_path}":
                return cwd + "\n"
            return ""
        logs = []
        with m.patch.object(self.w, "PROJECTS_DIR", self.projects):
            out = send_outcome.stash_pointer("%5", run, "bounce text", "bounce", logs)
        self.assertIsNone(out)
        self.assertTrue(any("thrash" in ln for ln in logs), logs)


class ThrashingSubagentIsNotResumed(_Watchdog):
    WORKER = "agent-a1b2c3d4"

    def test_no_dying_worker_nudge_for_a_thrash(self):
        import time
        now = time.time()
        cwd = "/devel/supervisor"
        sup = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Bežím ďalej.\n\n⏳ WORKING: čaká na workera."}]}}
        tpath = _write(self._tpath(cwd), [sup], 10, now)
        _write(tpath.parent / SID / "subagents" / (self.WORKER + ".jsonl"),
               [THRASH_ENTRY], 400, now)
        fake = _FakeTmux("%5\tclaude\t" + cwd + "\n")
        logs = self.w.run_once(now=now, run=fake, send_fn=self._send,
                               projects_dir=self.projects, state_path=self.state)
        self.assertFalse(any(ln.startswith("subagent-apierr") for ln in logs), logs)
        self.assertEqual(fake.sent, [])


class SettingsWriterReviewFixes(TestCase):
    def _apply(self, settings, user="gatekeeper"):
        with m.patch.object(airuleset, "_current_user", return_value=user):
            return cli_config.apply_managed_settings_defaults(settings)

    def test_the_input_dict_is_not_mutated(self):
        src = {"modelSettings": {"claude-opus-5-5": {"autoCompactWindow": 400000, "k": 1}}}
        self._apply(src)
        self.assertEqual(src["modelSettings"]["claude-opus-5-5"],
                         {"autoCompactWindow": 400000, "k": 1})

    def test_a_per_model_effort_no_longer_shadows_the_managed_high(self):
        out = self._apply({"modelSettings": {"claude-opus-5-5": {"effortLevel": "xhigh"}}})
        self.assertEqual(out["effortLevel"], "high")
        self.assertNotIn("effortLevel", out.get("modelSettings", {}).get("claude-opus-5-5", {}))


class EverySelfReviewPathReadsTheLenses(TestCase):
    """The exclude glob covers index, prevencia AND quality parts; every review
    surface names the same glob and says to read each file in full."""

    def test_worker_implementer_and_main_review_gate(self):
        for rel in ("agents/autopilot-worker.md", "agents/implementer.md",
                    "skills/autopilot/SKILL.md"):
            text = " ".join((REPO / rel).read_text().split())
            self.assertIn("gk-review-lenses*.md", text, rel)
            self.assertIn("in full", text, rel)
