"""#433 item G step 15 (first commit) — `run_once` characterization test.

`watchdog/__init__.py::run_once` is a 2012-line sweep that runs a fused
per-transcript PANE LOOP (jobs 1-7,10) and then a fixed sequence of STANDALONE
job invocations (jobs 3/5/7 + their #368/#461 extensions, then jobs 8→29). Each
standalone job has the identical shape::

    if <gate>:
        try:
            logs += <job>(..., dry_run=dry_run, ...)
        except Exception as e:
            logs.append("<label> error: %r" % (e,))

Item G step 16 will REPRESENT that standalone sequence differently — as a
`(job_label, gate_closure, invoke_closure)` registry — WITHOUT changing
behavior. Nothing in the existing suite pins the whole ORCHESTRATION contract
(the 27 run_once-referencing test files each drive ONE job or a narrow path),
so a registry rewrite could silently reorder a job, drop a gate, lose a
try/except isolation boundary, or stop threading `dry_run`, and the suite would
stay green.

This file is that safety net. It drives the REAL `run_once` with recording
stubs at the established `watchdog.<name>` seams (the split-test idiom) and
pins the OBSERVABLE orchestration contract:

  * **order** — the exact sequence in which the jobs are invoked;
  * **gates** — which run_once parameter switches each job on, and that a job
    with its gate absent does NOT fire (only the always-on jobs run);
  * **error isolation** — one job raising must NOT kill the sweep; every later
    job still fires, and a "logged" job's error line is recorded;
  * **dry-run** — `dry_run` is threaded to every seam.

DELIBERATELY it does NOT pin the fused jobs-1-7 internals: item G explicitly
DEFERS their deeper unfusing, the 27 pane-driving tests already cover the loop
body, and step-15's second commit moves the loop VERBATIM. Here the loop is a
clean no-op (`list_claude_panes → []`); only its ENTRY and position (first,
before the standalone registry) are pinned.

Why recording stubs (Approach 2) over the two rejected alternatives — a golden
snapshot of the real fused sweep (brittle, opaque, over-couples to the deferred
jobs-1-7 internals) and an `ast` assertion over run_once's source (pins TEXT
not BEHAVIOR — step 16's `if`-block → closure rewrite would fail it even with
identical behavior): recording stubs are the only strategy that SURVIVES the
representation change step 16 makes while FAILING loudly on any real
order/gate/isolation/dry-run change. The rationale is on issue #433's design
comment.

Robustness to both future commits: after step-15-commit-2 (loop →
`_run_pane_jobs_1_to_7`) `list_claude_panes` is still called first, via the
package namespace, so its recorded position is unchanged; after step 16 the
registry closures still call the SAME seams in the SAME order, so every
assertion below still holds. Green here == high confidence of zero behavior
change.

Uses `unittest.TestCase` + `subTest` (not `pytest.mark.parametrize`) so the
`python -m unittest discover -s tests` push gate — which does not understand
pytest marks — runs every parametrized case, not just the pytest run.
"""

import contextlib
import sys
import unittest
import unittest.mock as mock
from collections import namedtuple
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd            # noqa: E402
import watchdog.compact          # noqa: E402  (binds the wd.compact attribute)
import watchdog.goal             # noqa: E402  (binds the wd.goal attribute)


# --------------------------------------------------------------------------- #
# The canonical sweep spec — the machine-readable (job_label, gate, invoke)
# registry step 16 formalizes. ORDER is load-bearing: it is the literal
# invocation order run_once produces today (verified live against the real
# code before this file was written — see #433's STEP-0 validation comment).
#
# Fields:
#   label      – the seam patched + the name recorded (unique per row).
#   owner      – which module object carries the seam attribute.
#   attr       – the attribute name on `owner`.
#   gate       – the run_once kwarg that switches this job on; None = always-on.
#   open_value – the value passed for `gate` to switch the job ON.
#   ret        – "empty" (loop entry, returns []), "none" (check_usage, returns
#                None), "list" (a `logs += job(...)` seam, returns []).
#   err_prefix – the log-line prefix run_once appends if this job raises
#                ("<x> error"); "" = isolated but SILENT (bare `except: pass`);
#                None = no isolation boundary (the pane-loop entry — an
#                exception there propagates by design, so it is excluded from
#                the isolation test).
# --------------------------------------------------------------------------- #
JobSpec = namedtuple("JobSpec", "label owner attr gate open_value ret err_prefix")


def _stub_fetch(*a, **k):      # non-None sentinel for `is not None` gates
    return []


def _stub_roots():             # repo_roots: a callable returning a list
    return []


_PATH = "/nonexistent/airuleset-characterization-probe"   # truthy path gate


CANONICAL_SWEEP = [
    # pane loop entry (always; must be first)
    JobSpec("list_claude_panes", "wd", "list_claude_panes", None, None, "empty", None),

    # jobs 3 / 5 / 7 + #368 / #461 extensions (post-loop, pre-job-8)
    JobSpec("check_usage", "wd", "check_usage", "usage_fetch", _stub_fetch, "none", ""),
    JobSpec("deliver_pending_done", "wd", "deliver_pending_done", None, None, "list", ""),
    JobSpec("deliver_discord_replies", "wd", "deliver_discord_replies",
            "discord_fetch", _stub_fetch, "list", "discord-reply error"),
    JobSpec("prune_answered_questions", "wd", "prune_answered_questions",
            None, None, "list", "question-prune error"),
    JobSpec("reping_stale_questions", "wd", "reping_stale_questions",
            None, None, "list", "question-reping error"),
    JobSpec("reping_owner_decision_tickets", "wd", "reping_owner_decision_tickets",
            "owner_decision_fetch", _stub_fetch, "list", "owner-decision-digest error"),

    # jobs 8 → 30 (standalone registry)
    JobSpec("bounce_backstop", "wd", "bounce_backstop",
            "bounce_fetch", _stub_fetch, "list", "bounce-backstop error"),
    JobSpec("gk_request_backstop", "wd", "gk_request_backstop",
            "gkreq_fetch", _stub_fetch, "list", "gkreq-backstop error"),
    JobSpec("burn_snapshot_job", "wd", "burn_snapshot_job",
            "burn_snapshot_path", _PATH, "list", "burn-snapshot error"),
    JobSpec("compact_sweep", "wd.compact", "compact_sweep",
            "compact_requests_path", _PATH, "list", "compact-request error"),
    JobSpec("fleet_burn_job", "wd", "fleet_burn_job",
            "fleet_fetch", _stub_fetch, "list", "fleet-burn error"),
    JobSpec("burn_alert_job", "wd", "burn_alert_job",
            "burn_alert_enabled", True, "list", "burn-alert error"),
    JobSpec("goal_sweep", "wd.goal", "goal_sweep",
            "goal_jobs_enabled", True, "list", "goal-sweep error"),
    JobSpec("goal_dark_watch", "wd.goal", "goal_dark_watch",
            "goal_jobs_enabled", True, "list", "goal-dark-watch error"),
    JobSpec("goal_question_repoke_watch", "wd.goal", "goal_question_repoke_watch",
            "goal_jobs_enabled", True, "list", "goal-question-repoke-watch error"),  # (33) #522
    JobSpec("goal_lane_sweep", "wd.goal", "goal_lane_sweep",
            "goal_jobs_enabled", True, "list", "goal-lane-sweep error"),
    JobSpec("long_turn_watch", "wd", "long_turn_watch",
            "long_turn_enabled", True, "list", "long-turn error"),
    JobSpec("delivery_stall_watch", "wd", "delivery_stall_watch",
            "delivery_probe", _stub_fetch, "list", "delivery-stall error"),
    JobSpec("card_reconcile", "wd", "card_reconcile",
            "card_probe", _stub_fetch, "list", "card-reconcile error"),
    JobSpec("net_drift_alarm", "wd", "net_drift_alarm",
            "issue_counts_fetch", _stub_fetch, "list", "net-drift error"),
    JobSpec("stuck_main_sweep", "wd", "stuck_main_sweep",
            "repo_roots", _stub_roots, "list", "stuck-main error"),
    JobSpec("cleanup_stale_exec_markers", "wd", "cleanup_stale_exec_markers",
            None, None, "list", "exec-marker-cleanup error"),
    JobSpec("vault_purge_job", "wd", "vault_purge_job",
            "vault_purge", _stub_fetch, "list", "vault-purge error"),
    JobSpec("wip_ref_sweep", "wd", "sweep_orphaned_wip_refs",
            "repo_roots", _stub_roots, "list", "wip-ref-sweep error"),
    JobSpec("gk_selfservice_bounce", "wd", "gk_selfservice_bounce",
            "gk_selfservice_fetch", _stub_fetch, "list",
            "gk-selfservice-bounce error"),
    JobSpec("reconcile_u_labels", "wd", "reconcile_u_labels",
            "u_reconcile_clear", _stub_fetch, "list",
            "u-label-reconcile error"),
    JobSpec("conformance_check", "wd", "run_conformance_check",
            "conformance_root", _stub_roots, "list",
            "conformance-check error"),
    JobSpec("conformance_heartbeat_check", "wd",
            "run_conformance_heartbeat_check",
            "conformance_hb_enabled", True, "list",
            "conformance-heartbeat error"),
    JobSpec("gk_orphan_marker_sweep", "wd", "gk_orphan_marker_sweep",
            "gkorphan_fetch", _stub_fetch, "list",
            "gk-orphan-marker-sweep error"),
]

EXPECTED_FULL_ORDER = [s.label for s in CANONICAL_SWEEP]
ALWAYS_ON_ORDER = [s.label for s in CANONICAL_SWEEP if s.gate is None]

_OWNERS = {"wd": wd, "wd.compact": wd.compact, "wd.goal": wd.goal}


def _gate_to_labels():
    """gate kwarg -> [labels it switches on], in canonical order. A gate that
    controls several jobs (e.g. `goal_jobs_enabled` → goal_sweep/dark/lane)
    yields all of them."""
    out = {}
    for s in CANONICAL_SWEEP:
        if s.gate is not None:
            out.setdefault(s.gate, []).append(s.label)
    return out


GATE_TO_LABELS = _gate_to_labels()


def _all_open_kwargs():
    kw = {}
    for s in CANONICAL_SWEEP:
        if s.gate is not None:
            kw[s.gate] = s.open_value
    return kw


def _make_recorder(label, calls, ret, raise_exc=None):
    def _fn(*args, **kwargs):
        # dry_run is captured by KEYWORD — every one of the 23 seams receives
        # it as `dry_run=dry_run` (the real call convention), and step 16's
        # registry closures re-express those calls verbatim, so a switch to
        # POSITIONAL dry_run would be a gratuitous call-convention change this
        # test deliberately flags (a false-RED on a non-behavior change, never
        # a false-GREEN) rather than silently tolerates.
        calls.append((label, kwargs.get("dry_run")))
        if raise_exc is not None:
            raise raise_exc
        return ret
    return _fn


def _return_value_for(spec):
    """The value a seam's recorder returns — a UNIQUE sentinel for every
    `logs += job(...)` seam so that a dropped accumulation (`job(...)` with the
    return discarded — exactly the shape step 16's per-closure rewrite could
    regress) is observable in the returned `logs`, not silently swallowed."""
    if spec.ret == "none":
        return "%s::line" % spec.label     # truthy scalar -> `if line: logs.append(line)`
    if spec.ret == "empty":
        return []                          # loop entry: an iterable of (pid, cwd)
    return ["%s::ran" % spec.label]        # "list": `logs += job(...)`


def _drive(gate_kwargs, dry_run=False, raise_on=None, raise_exc=None,
           track_save_state=False, owner_disabled=None):
    """Run the REAL `run_once` with every CANONICAL_SWEEP seam replaced by a
    recorder. Returns `(labels, calls, logs)` where `labels` is the ordered
    invocation list, `calls` is the ordered `(label, dry_run)` list, and
    `logs` is what run_once returned.

    `list_claude_panes → []` makes the fused pane loop a clean no-op, so only
    the loop ENTRY and the standalone registry are exercised. State/projects
    dirs point at a throwaway TemporaryDirectory (hermetic, per the suite's own
    state_path/projects_dir isolation convention).

    `_owner_disabled` is patched explicitly (default: everything ENABLED) so
    the compound-gate outcome is env-independent — it does not rely on the
    conftest `AIRULESET_TEST_IGNORE_DISABLE` bypass or the box having no
    kill-switch flag files. `TestOwnerKillSwitchGates` passes its own stub to
    exercise the disabled path."""
    calls = []
    _od = owner_disabled if owner_disabled is not None else (lambda kind: False)
    with TemporaryDirectory() as d, contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(wd, "_owner_disabled", _od))
        for s in CANONICAL_SWEEP:
            exc = raise_exc if (raise_on == s.label) else None
            fn = _make_recorder(s.label, calls, _return_value_for(s), exc)
            stack.enter_context(mock.patch.object(_OWNERS[s.owner], s.attr, fn))
        if track_save_state:
            stack.enter_context(mock.patch.object(
                wd, "save_state", _make_recorder("save_state", calls, None)))
        logs = wd.run_once(
            now=1000.0, dry_run=dry_run,
            run=lambda *a, **k: "",
            send_fn=lambda *a, **k: None,
            projects_dir=Path(d) / "proj",
            state_path=str(Path(d) / "state.json"),
            **gate_kwargs,
        )
    return [c[0] for c in calls], calls, list(logs)


class TestCanonicalSpecCoherence(unittest.TestCase):
    """The spec is the contract; keep it internally sound."""

    def test_labels_unique(self):
        labels = [s.label for s in CANONICAL_SWEEP]
        self.assertEqual(len(labels), len(set(labels)),
                         "CANONICAL_SWEEP labels must be unique")

    def test_pane_loop_entry_is_first_and_only_special_row(self):
        # exactly one loop-entry row (ret == "empty"), and it is first
        empties = [i for i, s in enumerate(CANONICAL_SWEEP) if s.ret == "empty"]
        self.assertEqual(empties, [0])
        self.assertEqual(CANONICAL_SWEEP[0].label, "list_claude_panes")


class TestJobExecutionOrder(unittest.TestCase):
    """ORDER — the mutation tooth for 'reorder two jobs → RED'."""

    def test_all_gates_open_full_order(self):
        labels, _, _ = _drive(_all_open_kwargs())
        self.assertEqual(labels, EXPECTED_FULL_ORDER)

    def test_pane_loop_runs_before_every_standalone_job(self):
        labels, _, _ = _drive(_all_open_kwargs())
        self.assertEqual(labels[0], "list_claude_panes")
        self.assertNotIn("list_claude_panes", labels[1:])


class TestGateShortCircuit(unittest.TestCase):
    """GATES — the mutation tooth for 'swallow a gate → RED'."""

    def test_all_gates_closed_runs_only_always_on_jobs(self):
        # No gate kwargs at all: only the always-on jobs may fire. If any
        # gated job lost its `if gate:` (became unconditional), it appears
        # here and this fails.
        labels, _, _ = _drive({})
        self.assertEqual(labels, ALWAYS_ON_ORDER)

    def test_each_gate_controls_exactly_its_own_jobs(self):
        # Open ONE gate at a time; the non-always-on jobs that fire must be
        # exactly the ones that gate owns, in canonical order.
        for gate, owned in GATE_TO_LABELS.items():
            with self.subTest(gate=gate):
                s = next(sp for sp in CANONICAL_SWEEP if sp.gate == gate)
                labels, _, _ = _drive({gate: s.open_value})
                extra = [ln for ln in labels if ln not in ALWAYS_ON_ORDER]
                self.assertEqual(
                    extra, owned,
                    "gate %r must switch on exactly %r (got extra %r)"
                    % (gate, owned, extra))
                # and the always-on jobs are still all present
                for always in ALWAYS_ON_ORDER:
                    self.assertIn(always, labels)


class TestJobOutputAccumulation(unittest.TestCase):
    """ACCUMULATION — each job's returned journal lines actually reach the
    swept `logs`. Pins `logs += job(...)` (and check_usage's scalar
    `if line: logs.append(line)`) so a per-closure rewrite that DROPS a job's
    output — a silent regression a mere call-count would miss — goes RED."""

    def test_every_job_return_is_accumulated_into_logs(self):
        _, _, logs = _drive(_all_open_kwargs())
        for s in CANONICAL_SWEEP:
            if s.ret == "list":
                with self.subTest(job=s.label):
                    self.assertIn(
                        "%s::ran" % s.label, logs,
                        "%s output was not accumulated into logs" % s.label)
        # check_usage's scalar return is appended only when truthy
        self.assertIn("check_usage::line", logs)
        # the loop entry (list_claude_panes) returns panes, never journal lines
        self.assertNotIn("list_claude_panes::ran", logs)


class TestOwnerKillSwitchGates(unittest.TestCase):
    """COMPOUND GATES — jobs 14 (compact_sweep) and 9/20/32 (goal_sweep /
    goal_dark_watch / goal_question_repoke_watch / goal_lane_sweep) are gated
    `<param> and not _<x>_jobs_disabled`. Pins the SECOND conjunct: when the owner kill-switch
    is on, those jobs must NOT fire even with their param open. Dropping the
    `and not ...` conjunct goes RED here."""

    def test_owner_disabled_suppresses_only_the_compound_gated_jobs(self):
        suppressed = {"compact_sweep", "goal_sweep",
                      "goal_dark_watch", "goal_question_repoke_watch",
                      "goal_lane_sweep"}
        # Drive with both "goal" and "compact" reporting owner-disabled.
        labels, _, logs = _drive(
            _all_open_kwargs(),
            owner_disabled=lambda kind: kind in ("goal", "compact"))
        for lbl in sorted(suppressed):
            with self.subTest(job=lbl):
                self.assertNotIn(
                    lbl, labels,
                    "%s must be suppressed by the owner kill-switch" % lbl)
        # every OTHER job (its gate has no kill-switch conjunct) still fires
        for s in CANONICAL_SWEEP:
            if s.label not in suppressed:
                with self.subTest(unaffected=s.label):
                    self.assertIn(s.label, labels)
        # and run_once logs the disable notices loudly
        self.assertTrue(any("DISABLED by owner flag" in ln for ln in logs))


class TestErrorIsolation(unittest.TestCase):
    """ISOLATION — the mutation tooth for 'let one job's exception propagate
    → RED'. One job raising must not kill the sweep: the sweep returns, every
    later job still fires, and a 'logged' job's error line is recorded."""

    def test_each_isolated_job_exception_does_not_kill_the_sweep(self):
        boundary = [s for s in CANONICAL_SWEEP if s.err_prefix is not None]
        for s in boundary:
            with self.subTest(job=s.label):
                exc = RuntimeError("characterization-injected-%s" % s.label)
                # If run_once did NOT isolate this job, this call raises and
                # the subTest errors — exactly the RED we want on a mutation
                # that removes the try/except.
                labels, _, logs = _drive(
                    _all_open_kwargs(), raise_on=s.label, raise_exc=exc)
                # the raising job recorded (recorder appends before raising)
                # AND every job still fired in the canonical order
                self.assertEqual(
                    labels, EXPECTED_FULL_ORDER,
                    "a %s exception must not drop any job" % s.label)
                if s.err_prefix:      # logged isolation → an error line exists
                    self.assertTrue(
                        any(ln.startswith(s.err_prefix) for ln in logs),
                        "expected a %r log line, got: %r" % (s.err_prefix, logs))
                else:                 # silent isolation (bare except: pass)
                    self.assertFalse(
                        any(("error" in ln and s.label.replace("_", "-") in ln)
                            for ln in logs),
                        "%s isolation must stay silent" % s.label)


class TestDryRunThreading(unittest.TestCase):
    """DRY-RUN — `dry_run` reaches every seam, both values."""

    def test_dry_run_true_threads_to_every_seam(self):
        _, calls, _ = _drive(_all_open_kwargs(), dry_run=True)
        self.assertEqual(len(calls), len(EXPECTED_FULL_ORDER))
        for label, dr in calls:
            with self.subTest(job=label):
                self.assertIs(dr, True,
                              "%s did not receive dry_run=True" % label)

    def test_dry_run_false_threads_to_every_seam(self):
        _, calls, _ = _drive(_all_open_kwargs(), dry_run=False)
        for label, dr in calls:
            with self.subTest(job=label):
                self.assertIs(dr, False,
                              "%s did not receive dry_run=False" % label)


class TestSweepFinalization(unittest.TestCase):
    """The sweep persists state LAST — a contract step 16 must preserve
    (the registry runs before the final save)."""

    def test_state_saved_once_after_every_job(self):
        labels, _, _ = _drive(_all_open_kwargs(), track_save_state=True)
        self.assertEqual(labels.count("save_state"), 1)
        self.assertEqual(labels[-1], "save_state")
        # every job precedes the persist
        self.assertEqual(labels[:-1], EXPECTED_FULL_ORDER)

    def test_run_once_returns_a_log_list(self):
        _, _, logs = _drive(_all_open_kwargs())
        self.assertIsInstance(logs, list)


if __name__ == "__main__":
    unittest.main()
