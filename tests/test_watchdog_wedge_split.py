"""#433 item G step 12 — `watchdog/wedge.py` split.

Job 10's queued-prompt-wedge detection (`prompt_wedge_check`) and its
ping-eligibility gate (`_session_is_waiting`) were moved VERBATIM out of
`watchdog/__init__.py` into `watchdog.wedge`, then re-exported IN PLACE by a
positional facade import. `wedge.py` is a back-reference module
(`import watchdog`, nothing imported by name); the two functions never call
each other (`_session_is_waiting` is passed IN as `prompt_wedge_check`'s
`waiting` argument, never invoked by it), so there are NO co-moved intra-module
cross-calls — every free name they read lives OUTSIDE this module and is reached
call-time as `watchdog.<name>`, so every `patch.object(watchdog, "<name>", …)`
seam still resolves. These tests lock the invariants that keep the move safe:

  1. `import watchdog` stays clean in a FRESH subprocess, and importing the
     submodule FIRST initializes the package cleanly — no circular-import
     breakage from `wedge.py`'s `import watchdog` back-reference.
  2. Both moved names are the SAME object at `watchdog.<name>` and
     `watchdog.wedge.<name>` — the C2 re-export contract. A drift to a private
     copy breaks the identity assertion (a dead `patch.object(watchdog, …)`
     seam is exactly this bug).
  3. `MOVED_NAMES` is self-validating against BOTH authoritative sources
     (wedge.py's own top-level defs + `__init__.py`'s facade ImportFrom), so a
     moved-but-omitted name cannot silently reduce coverage.
  4. `wedge.py` imports ONLY `import watchdog` — no `from watchdog import …`
     (neither function def-defaults a constant; every parameter default is a
     literal), so the one banned import shape can never arise.
  5. Every back-reference is genuinely `watchdog.`-prefixed: patching each at
     the PACKAGE level is OBSERVED by the moved caller. A silent bare-revert
     (the design's #1 hazard) NameErrors in wedge.py's own globals — these teeth
     go red. All 13 free names are covered directly here; the two paste-end
     escalation constants (`PWEDGE_SUBMIT_UNSTICK_AFTER` /
     `PWEDGE_SUBMIT_GIVEUP_AFTER`) are driven through the give-up path below.
"""

import ast
import inspect
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.wedge as wedge  # noqa: E402

# Every name moved into wedge.py (2 functions, NO constants), in definition
# order. Hand-maintained on purpose — it is the checklist the reviewer diffs
# against the facade import block in `__init__.py`; the self-validation test
# below proves it stays in sync with the real sources.
MOVED_NAMES = [
    "_session_is_waiting",
    "prompt_wedge_check",
]

# ---- fixtures (mirrored from tests/test_prompt_wedge.py) ------------------- #
DRAFT_PANE = ("✳ hotovo — súhrn turnu\n"
              "──── ultracode ─\n"
              "❯\xa0nechať ako je\n"
              "────\n"
              "  ctx ██░░  caveman\n")
MACHINE_PANE = ("✻ Waiting for 1 background agent to finish\n"
                "──── ultracode ─\n"
                "❯\xa0Priorita: prio:bounce #1896 - posledny blocker release\n"
                "────\n"
                "  ctx ██░░  caveman\n")


class FakeSend:
    def __init__(self):
        self.calls = []

    def __call__(self, body, **kw):
        self.calls.append((body, kw))
        return "sent"


def _run_recorder():
    calls = []

    def run(argv, timeout=8):
        calls.append(argv)
        if "pane_in_mode" in " ".join(argv):
            return "0"          # not in copy-mode -> submit proceeds
        return ""
    run.calls = calls
    return run


def _enters(run):
    return [a for a in run.calls if a and a[-1] == "Enter"]


class FreshSubprocessImportIsClean(unittest.TestCase):
    def test_import_watchdog_in_fresh_process(self):
        r = subprocess.run(
            [sys.executable, "-c", "import watchdog; print('ok')"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stdout.strip(), "ok")
        self.assertEqual(r.stderr.strip(), "")

    def test_import_wedge_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly (the back-reference `import watchdog` gets a partially-init
        # module, and no `watchdog.<attr>` is touched at import time).
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.wedge as w; print(w.watchdog.PWEDGE_SWEEPS)"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stderr.strip(), "")
        self.assertEqual(r.stdout.strip(), "2")


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_is_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(wedge, name),
                                f"{name} missing from watchdog.wedge")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(getattr(watchdog, name), getattr(wedge, name),
                              f"watchdog.{name} is not watchdog.wedge.{name}")

    def test_wedge_lives_in_its_own_module_file(self):
        self.assertTrue(wedge.__file__.endswith("watchdog/wedge.py"),
                        wedge.__file__)


class MovedNamesChecklistIsSelfValidating(unittest.TestCase):
    """MOVED_NAMES is a hand-maintained checklist every other test trusts. Prove
    it equals BOTH authoritative sources, so a name added to (or dropped from)
    the real move cannot silently escape coverage (the step-6 NIT)."""

    def _wedge_toplevel_names(self):
        src = Path(wedge.__file__).read_text()
        tree = ast.parse(src)
        names = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.append(t.id)
        return names

    def _facade_imported_names(self):
        src = (REPO / "watchdog" / "__init__.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom)
                    and node.module == "watchdog.wedge"):
                return [a.name for a in node.names]
        return []

    def test_checklist_matches_module_source(self):
        self.assertEqual(sorted(MOVED_NAMES),
                         sorted(self._wedge_toplevel_names()))

    def test_checklist_matches_facade_import(self):
        self.assertEqual(sorted(MOVED_NAMES),
                         sorted(self._facade_imported_names()))


class WedgeImportsOnlyThePackage(unittest.TestCase):
    """wedge.py is a pure back-reference module: `import watchdog` and nothing
    else at top level. No constant moves and neither function def-defaults one,
    so there is no `from watchdog import …` (which for a below-position function
    would be the one banned import shape). Locks that structural invariant."""

    def test_module_top_imports_are_exactly_import_watchdog(self):
        tree = ast.parse(Path(wedge.__file__).read_text())
        plain = [n for n in tree.body if isinstance(n, ast.Import)]
        fromimports = [n for n in tree.body if isinstance(n, ast.ImportFrom)]
        self.assertEqual(len(plain), 1)
        self.assertEqual([a.name for a in plain[0].names], ["watchdog"])
        self.assertEqual(fromimports, [],
                         "wedge.py must not `from watchdog import` anything")

    def test_no_moved_function_def_defaults_a_package_constant(self):
        consts = {"MACHINE_NUDGE_PREFIX", "PWEDGE_MIN_IDLE_S", "PWEDGE_SWEEPS",
                  "PWEDGE_SUBMIT_UNSTICK_AFTER", "PWEDGE_SUBMIT_GIVEUP_AFTER",
                  "PWEDGE_PING_COOLDOWN_S"}
        const_ids = {id(getattr(watchdog, c)) for c in consts}
        for fn_name in MOVED_NAMES:
            sig = inspect.signature(getattr(watchdog, fn_name))
            for p in sig.parameters.values():
                if p.default is not inspect.Parameter.empty:
                    self.assertNotIn(id(p.default), const_ids,
                                     f"{fn_name} def-defaults a moved constant")


class BackReferenceSeamsGoThroughThePackage(unittest.TestCase):
    """Every free name the two functions read is reached call-time as
    `watchdog.<name>`. Patch each at the PACKAGE level and prove the moved
    caller observes it — a silent bare-revert NameErrors in wedge.py's own
    globals (nothing is imported by name), so these assertions go red."""

    STALE = 30 * 60 + 60  # comfortably >= PWEDGE_MIN_IDLE_S

    # --- _session_is_waiting: transcript readers -------------------------- #
    def test_session_is_waiting_reads_iter_jsonl_tail_and_entry_text(self):
        entries = [{"type": "assistant"}]
        with mock.patch.object(watchdog, "_iter_jsonl_tail", return_value=entries) as it, \
                mock.patch.object(watchdog, "_entry_text",
                                  return_value="… ❓ NEEDS YOU: rozhodni") as et:
            self.assertTrue(watchdog._session_is_waiting("/any/path"))
        it.assert_called_once()
        et.assert_called_once()
        with mock.patch.object(watchdog, "_iter_jsonl_tail", return_value=entries), \
                mock.patch.object(watchdog, "_entry_text", return_value="len bežný text"):
            self.assertFalse(watchdog._session_is_waiting("/any/path"))

    # --- prompt_wedge_check: box reader ----------------------------------- #
    def test_find_input_box_backref_is_observed(self):
        s = FakeSend()
        with mock.patch.object(watchdog, "_find_input_box", return_value=None) as m:
            logs = watchdog.prompt_wedge_check(time.time(), {}, "%1", "whatever",
                                               0, "zbynek", "odoo", s)
        m.assert_called_once()
        self.assertEqual(logs, [])
        self.assertFalse(s.calls)

    # --- constant seams --------------------------------------------------- #
    def test_pwedge_sweeps_constant_seam_is_live(self):
        now = time.time()
        tm = now - self.STALE
        st, s = {}, FakeSend()
        with mock.patch.object(watchdog, "PWEDGE_SWEEPS", 4):
            watchdog.prompt_wedge_check(now, st, "%1", DRAFT_PANE, tm, "z", "odoo", s)
            watchdog.prompt_wedge_check(now + 70, st, "%1", DRAFT_PANE, tm, "z", "odoo", s)
        self.assertFalse(s.calls, "patched PWEDGE_SWEEPS=4 must suppress the 2-sweep ping")
        # control: the default threshold (2) pings on the 2nd sweep
        st2, s2 = {}, FakeSend()
        watchdog.prompt_wedge_check(now, st2, "%1", DRAFT_PANE, tm, "z", "odoo", s2)
        watchdog.prompt_wedge_check(now + 70, st2, "%1", DRAFT_PANE, tm, "z", "odoo", s2)
        self.assertEqual(len(s2.calls), 1)

    def test_pwedge_min_idle_constant_seam_is_live(self):
        now = time.time()
        tm = now - 60  # only 60s stale
        st, s = {}, FakeSend()
        with mock.patch.object(watchdog, "PWEDGE_MIN_IDLE_S", 10 ** 9):
            watchdog.prompt_wedge_check(now, st, "%1", DRAFT_PANE, tm, "z", "odoo", s)
            watchdog.prompt_wedge_check(now + 70, st, "%1", DRAFT_PANE, tm, "z", "odoo", s)
        self.assertFalse(s.calls)
        self.assertNotIn("pwedge:%1", st)  # treated as fresh -> key popped

    def test_machine_nudge_prefix_constant_seam_is_live(self):
        now = time.time()
        run, s, st = _run_recorder(), FakeSend(), {}
        with mock.patch.object(watchdog, "MACHINE_NUDGE_PREFIX", ("nechať",)):
            watchdog.prompt_wedge_check(now, st, "%1", DRAFT_PANE, now, "z", "odoo", s, run=run)
            logs = watchdog.prompt_wedge_check(now + 70, st, "%1", DRAFT_PANE, now,
                                               "z", "odoo", s, run=run)
        self.assertFalse(s.calls, "prefix-matched draft submits, never pings")
        self.assertTrue(any("machine-nudge" in ln for ln in logs), logs)
        self.assertTrue(_enters(run))

    def test_pwedge_ping_cooldown_constant_seam_is_live(self):
        # A ping fires, then the SAME pane re-wraps (fresh hash) within the
        # cooldown -> watchdog.PWEDGE_PING_COOLDOWN_S suppresses the re-ping.
        now = time.time()
        tm = now - self.STALE
        st, s = {}, FakeSend()
        watchdog.prompt_wedge_check(now, st, "%1", DRAFT_PANE, tm, "z", "odoo", s)
        watchdog.prompt_wedge_check(now + 70, st, "%1", DRAFT_PANE, tm, "z", "odoo", s)
        self.assertEqual(len(s.calls), 1)          # first ping landed
        rewrap = DRAFT_PANE.replace("nechať ako je", "nechať ako je tak")
        with mock.patch.object(watchdog, "PWEDGE_PING_COOLDOWN_S", 10 ** 9):
            watchdog.prompt_wedge_check(now + 140, st, "%1", rewrap, tm, "z", "odoo", s)
            logs = watchdog.prompt_wedge_check(now + 210, st, "%1", rewrap, tm, "z", "odoo", s)
        self.assertEqual(len(s.calls), 1, "cooldown must suppress the re-ping")
        self.assertTrue(any("cooldown" in ln for ln in logs), logs)

    # --- function seams: pane / backlog / dreply -------------------------- #
    def test_pane_in_mode_backref_is_observed(self):
        now = time.time()
        run, s, st = _run_recorder(), FakeSend(), {}
        with mock.patch.object(watchdog, "pane_in_mode", return_value=True) as m:
            watchdog.prompt_wedge_check(now, st, "%1", MACHINE_PANE, now, "z", "odoo", s, run=run)
            watchdog.prompt_wedge_check(now + 70, st, "%1", MACHINE_PANE, now,
                                        "z", "odoo", s, run=run)
        m.assert_called()
        self.assertFalse(_enters(run), "pane_in_mode=True must skip the submit keystrokes")

    def test_pane_location_backref_is_observed(self):
        now = time.time()
        tm = now - self.STALE
        run, s, st = _run_recorder(), FakeSend(), {}
        with mock.patch.object(watchdog, "_pane_location", return_value="LOC-SENTINEL") as m:
            watchdog.prompt_wedge_check(now, st, "%1", DRAFT_PANE, tm, "z", "odoo", s, run=run)
            watchdog.prompt_wedge_check(now + 70, st, "%1", DRAFT_PANE, tm, "z", "odoo", s, run=run)
        m.assert_called()
        self.assertTrue(any("LOC-SENTINEL" in body for body, _ in s.calls), s.calls)

    def test_cached_backlog_open_backref_is_observed(self):
        now = time.time()
        tm = now - self.STALE
        st, s = {}, FakeSend()
        with mock.patch.object(watchdog, "_cached_backlog_open", return_value=True) as m:
            watchdog.prompt_wedge_check(now, st, "%1", DRAFT_PANE, tm, "z", "odoo", s,
                                        waiting=False, cwd="/x", backlog_fetch=lambda *a: [])
            logs = watchdog.prompt_wedge_check(now + 70, st, "%1", DRAFT_PANE, tm, "z", "odoo",
                                               s, waiting=False, cwd="/x",
                                               backlog_fetch=lambda *a: [])
        m.assert_called()
        self.assertEqual(len(s.calls), 1, "backlog-open must fall through to the ping")
        self.assertTrue(any("backlog" in ln for ln in logs), logs)

    def test_is_dreply_machine_text_backref_is_observed(self):
        now = time.time()
        run, s, st = _run_recorder(), FakeSend(), {}
        with mock.patch.object(watchdog, "_is_dreply_machine_text", return_value=True) as m:
            watchdog.prompt_wedge_check(now, st, "%1", DRAFT_PANE, now, "z", "odoo", s, run=run)
            logs = watchdog.prompt_wedge_check(now + 70, st, "%1", DRAFT_PANE, now,
                                               "z", "odoo", s, run=run)
        m.assert_called()
        self.assertFalse(s.calls, "dreply-machine text submits, never pings")
        self.assertTrue(any("machine-nudge" in ln for ln in logs), logs)

    # --- paste-end escalation constants ----------------------------------- #
    def test_submit_unstick_and_giveup_constant_seams_are_live(self):
        # Both escalation thresholds patched to 0: the first machine-submit
        # attempt already exceeds them -> paste-end unstick sequence + give-up
        # ping, proving watchdog.PWEDGE_SUBMIT_UNSTICK_AFTER /
        # watchdog.PWEDGE_SUBMIT_GIVEUP_AFTER are read call-time.
        now = time.time()
        run, s, st = _run_recorder(), FakeSend(), {}
        with mock.patch.object(watchdog, "PWEDGE_SUBMIT_UNSTICK_AFTER", 0), \
                mock.patch.object(watchdog, "PWEDGE_SUBMIT_GIVEUP_AFTER", 0):
            watchdog.prompt_wedge_check(now, st, "%1", MACHINE_PANE, now, "z", "odoo", s, run=run)
            logs = watchdog.prompt_wedge_check(now + 70, st, "%1", MACHINE_PANE, now,
                                               "z", "odoo", s, run=run)
        # paste-end unstick sequence went to run (the -H hex byte form)
        self.assertTrue(any("1b" in a and "7e" in a for a in run.calls), run.calls)
        # give-up ping reached send_fn
        self.assertEqual(len(s.calls), 1, s.calls)
        self.assertTrue(any("give-up" in ln for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
