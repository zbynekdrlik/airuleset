"""#1036 — watchdog Job 49 (task_hygiene_job) unit tests.

The job computes A/B/C (injected compute — NEVER a real Odoo call), persists the
status, and — while A ∪ B is non-empty — delivers ONE gated `task-hygiene` nudge
into each eligible idle Claude pane. Fully dependency-injected (the parked_wake /
model_float_audit template): no tmux, no network.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from watchdog import task_hygiene as th  # noqa: E402
import cli_odoo_ro as ro  # noqa: E402

NOW = 1_000_000.0
CFG = {"instance_url": "https://erp.montalu.cloud", "project_ids": [1]}


def _result(a=0, b=0, c=0):
    return {
        "A": [{"task_id": 500 + i, "task_name": "t", "stage": "V", "ts": 1.0}
              for i in range(a)],
        "B": [{"task_id": 800 + i, "task_name": "t", "stage": "Verifikácia"}
              for i in range(b)],
        "C": [{"task_id": 900 + i} for i in range(c)],
        "summary": "task-hygiene: A=%d B=%d C=%d" % (a, b, c),
    }


class _Deps:
    """Recording fakes for the injected job dependencies."""

    def __init__(self, result=None, compute_raises=None, at_idle=True,
                 in_mode=False, recent_human=False, gate_ok=True,
                 deliver_ok=True):
        self._result = result if result is not None else _result()
        self._compute_raises = compute_raises
        self._at_idle = at_idle
        self._in_mode = in_mode
        self._recent_human = recent_human
        self._gate_ok = gate_ok
        self._deliver_ok = deliver_ok
        self.persisted = []
        self.delivered = []
        self.marked = []

    def compute(self, cfg):
        if self._compute_raises:
            raise self._compute_raises
        return self._result

    def persist(self, result):
        self.persisted.append(result)

    def find_transcript(self, projects_dir, cwd):
        return ("/t/%s.jsonl" % cwd.strip("/").replace("/", "_"),)

    def capture(self, pid):
        return "❯ "

    def in_mode(self, pid):
        return self._in_mode

    def at_idle(self, captured):
        return self._at_idle

    def recent_human(self, sid, cwd, tpath, pid):
        return self._recent_human

    def gate_ok(self, state, sid, kind, now):
        return self._gate_ok

    def mark_sent(self, state, sid, kind, now):
        self.marked.append((sid, kind))

    def deliver(self, pid, tpath, text):
        self.delivered.append((pid, text))
        return self._deliver_ok


def _run(deps, panes=None, state=None, dry_run=False):
    panes = panes if panes is not None else [("%1", "/home/x/devel/demo")]
    state = state if state is not None else {}
    return th.task_hygiene_job(
        NOW, state, panes, "/proj", cfg=CFG,
        compute=deps.compute, persist=deps.persist,
        deliver=deps.deliver, gate_ok=deps.gate_ok, mark_sent=deps.mark_sent,
        find_transcript=deps.find_transcript, capture=deps.capture,
        in_mode=deps.in_mode, at_idle=deps.at_idle,
        recent_human=deps.recent_human, dry_run=dry_run)


class TaskHygieneJob(unittest.TestCase):
    def test_persists_even_when_clean(self):
        deps = _Deps(result=_result(a=0, b=0))
        _run(deps)
        self.assertEqual(len(deps.persisted), 1)
        self.assertEqual(deps.delivered, [])   # nothing to nudge

    def test_nudges_when_A_nonempty(self):
        deps = _Deps(result=_result(a=2))
        logs = _run(deps)
        self.assertEqual(len(deps.persisted), 1)
        self.assertEqual(len(deps.delivered), 1)
        self.assertEqual(len(deps.marked), 1)
        self.assertTrue(any("nudged" in ln for ln in logs))

    def test_nudges_when_B_nonempty(self):
        deps = _Deps(result=_result(b=1))
        _run(deps)
        self.assertEqual(len(deps.delivered), 1)

    def test_C_only_does_not_nudge(self):
        deps = _Deps(result=_result(c=3))
        _run(deps)
        self.assertEqual(deps.delivered, [])

    def test_compute_error_no_crash_no_deliver(self):
        deps = _Deps(compute_raises=ro.OdooError("boom"))
        logs = _run(deps)
        self.assertEqual(deps.persisted, [])
        self.assertEqual(deps.delivered, [])
        self.assertTrue(any("Odoo read failed" in ln for ln in logs))

    def test_cadence_gate_blocks_deliver(self):
        deps = _Deps(result=_result(a=1), gate_ok=False)
        _run(deps)
        self.assertEqual(deps.delivered, [])   # per-kind floor not elapsed
        self.assertEqual(deps.marked, [])

    def test_recent_human_vetoes_deliver(self):
        deps = _Deps(result=_result(a=1), recent_human=True)
        _run(deps)
        self.assertEqual(deps.delivered, [])

    def test_busy_pane_not_nudged(self):
        deps = _Deps(result=_result(a=1), at_idle=False)
        _run(deps)
        self.assertEqual(deps.delivered, [])

    def test_in_mode_pane_skipped(self):
        deps = _Deps(result=_result(a=1), in_mode=True)
        _run(deps)
        self.assertEqual(deps.delivered, [])

    def test_unverified_submit_not_marked(self):
        deps = _Deps(result=_result(a=1), deliver_ok=False)
        _run(deps)
        self.assertEqual(len(deps.delivered), 1)
        self.assertEqual(deps.marked, [])       # a swallowed submit is not a send

    def test_dry_run_persists_but_does_not_deliver(self):
        deps = _Deps(result=_result(a=1))
        _run(deps, dry_run=True)
        self.assertEqual(len(deps.persisted), 1)
        self.assertEqual(deps.delivered, [])

    def test_ambiguous_shared_cwd_skipped(self):
        # two panes share ONE cwd → one transcript/sid → never guess
        deps = _Deps(result=_result(a=1))
        _run(deps, panes=[("%1", "/home/x/devel/demo"),
                          ("%2", "/home/x/devel/demo")])
        self.assertEqual(deps.delivered, [])


class Cadence(unittest.TestCase):
    def test_cadence_due_when_never_run(self):
        self.assertTrue(th.cadence_due(NOW, {}, 7200))

    def test_cadence_not_due_within_window(self):
        st = {"task_hygiene_last_ts": NOW - 100}
        self.assertFalse(th.cadence_due(NOW, st, 7200))

    def test_cadence_due_after_window(self):
        st = {"task_hygiene_last_ts": NOW - 8000}
        self.assertTrue(th.cadence_due(NOW, st, 7200))

    def test_mark_run_records_ts(self):
        st = {}
        th.mark_run(st, NOW)
        self.assertEqual(st["task_hygiene_last_ts"], NOW)


class NudgeKindRegistered(unittest.TestCase):
    def test_task_hygiene_is_a_machine_nudge_kind(self):
        import watchdog as wd
        self.assertIn("task-hygiene", wd.MACHINE_NUDGE_KINDS)

    def test_off_by_default(self):
        import os
        import tempfile
        import unittest.mock as m
        import watchdog as wd
        # the suite sets AIRULESET_TEST_IGNORE_DISABLE (conftest autouse) so a
        # real box's staged state never fails the suite; pop it to exercise the
        # real per-kind predicate (the #1023 _no_bypass pattern).
        with m.patch.dict(os.environ):
            os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
            with tempfile.TemporaryDirectory() as home:
                self.assertFalse(wd.nudges_enabled("task-hygiene", home=home))


if __name__ == "__main__":
    unittest.main()
