"""#433 item G step 14 — `watchdog/sweep_jobs.py` split.

The job-5 pending done-ping backstop (`deliver_pending_done` + its transcript /
mtime helpers) and the job-22 stale-exec-marker hygiene (`_session_id_is_live`,
`cleanup_stale_exec_markers`) were moved VERBATIM out of `watchdog/__init__.py`
into `watchdog.sweep_jobs`, then re-exported IN PLACE by a positional facade
import. `sweep_jobs.py` is a back-reference module (`import watchdog`); its
cross-module calls (transcript readers, `_default_run` / `list_claude_panes`,
`PROJECTS_DIR`) go through the package namespace at call time so every
`patch.object(watchdog, "<name>", ...)` seam still resolves. These tests lock
the invariants that keep the move safe:

  1. `import watchdog` stays clean in a FRESH subprocess — no circular-import
     breakage from `sweep_jobs.py`'s `import watchdog` back-reference.
  2. Every moved name (8 functions + 5 constants) is the SAME object at
     `watchdog.<name>` and `watchdog.sweep_jobs.<name>` — the C2 re-export
     contract. A drift to a private copy breaks the identity assertion (a dead
     `patch.object(watchdog, "<name>", ...)` seam is exactly this bug).
  3. The `PENDING_*` constants moved WITH the code (def-time defaults of
     `deliver_pending_done` per the step-14 design rule); run_once's own
     signature default still resolves them through the re-export, which lands
     above run_once's def. `deliver_pending_done` remains reachable as the
     re-exported `watchdog.deliver_pending_done` name run_once (job 5) calls.
"""

import inspect
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.sweep_jobs as sweep_jobs  # noqa: E402

# Every name moved into sweep_jobs.py (8 functions + 5 module-level constants).
# Kept explicit — a hand-maintained list is the point: if a name is added to or
# dropped from the move, this list is the checklist the reviewer diffs against
# the facade import block in `__init__.py`.
MOVED_NAMES = [
    # functions (definition order)
    "_transcript_for_sid",
    "_cwd_from_transcript",
    "_bg_monitor_in_cwd",
    "deliver_pending_done",
    "_safe_mtime",
    "_safe_unlink",
    "_session_id_is_live",
    "cleanup_stale_exec_markers",
    # module-level constants (definition order)
    "PENDING_DONE_GRACE",
    "PENDING_DONE_MAX_STALE",
    "PENDING_PREFIX",
    "MAIN_EXEC_MARKER_MAX_AGE_S",
    "_EXEC_MARKER_PREFIXES",
]


class FreshSubprocessImportIsClean(unittest.TestCase):
    def test_import_watchdog_in_fresh_process(self):
        r = subprocess.run(
            [sys.executable, "-c", "import watchdog; print('ok')"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stdout.strip(), "ok")
        self.assertEqual(r.stderr.strip(), "")

    def test_import_sweep_jobs_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly (the back-reference `import watchdog` gets a partially-init
        # module, and no `watchdog.<attr>` is touched at import time).
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.sweep_jobs as s; print(s.PENDING_PREFIX)"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stderr.strip(), "")
        self.assertEqual(r.stdout.strip(), "/tmp/claude-discord-pending-")


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_is_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(sweep_jobs, name),
                                f"{name} missing from watchdog.sweep_jobs")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(getattr(watchdog, name), getattr(sweep_jobs, name),
                              f"watchdog.{name} is not watchdog.sweep_jobs.{name}")

    def test_sweep_jobs_lives_in_its_own_module_file(self):
        self.assertTrue(sweep_jobs.__file__.endswith("watchdog/sweep_jobs.py"),
                        sweep_jobs.__file__)


class DefTimeDefaultsAndDelegate(unittest.TestCase):
    def test_deliver_pending_done_deftime_defaults_are_the_moved_constants(self):
        # The step-14 rule moved PENDING_* WITH the code precisely because they
        # are `deliver_pending_done`'s def-time defaults. They bind at def time
        # to the module-local constants; equality proves the move preserved them.
        params = inspect.signature(watchdog.deliver_pending_done).parameters
        self.assertEqual(params["done_grace"].default, watchdog.PENDING_DONE_GRACE)
        self.assertEqual(params["max_stale"].default, watchdog.PENDING_DONE_MAX_STALE)
        self.assertEqual(params["pending_prefix"].default, watchdog.PENDING_PREFIX)

    def test_run_once_signature_defaults_resolve_the_reexported_constants(self):
        # run_once STAYS in __init__ and its signature def-time-defaults
        # done_grace=PENDING_DONE_GRACE / pending_prefix=PENDING_PREFIX. The
        # facade re-export lands ABOVE run_once's def, so these resolve — this
        # test fails loudly if a future reorder drops the re-export below it.
        params = inspect.signature(watchdog.run_once).parameters
        self.assertEqual(params["done_grace"].default, watchdog.PENDING_DONE_GRACE)
        self.assertEqual(params["pending_prefix"].default, watchdog.PENDING_PREFIX)

    def test_deliver_pending_done_is_the_facade_name_job5_calls(self):
        # run_once (job 5) calls the bare name `deliver_pending_done`, which
        # resolves through the facade re-export in __init__'s globals.
        self.assertIs(watchdog.deliver_pending_done, sweep_jobs.deliver_pending_done)
        self.assertTrue(callable(watchdog.deliver_pending_done))

    def test_cleanup_deftime_default_is_the_moved_max_age(self):
        params = inspect.signature(watchdog.cleanup_stale_exec_markers).parameters
        self.assertEqual(params["max_age_s"].default, watchdog.MAIN_EXEC_MARKER_MAX_AGE_S)


if __name__ == "__main__":
    unittest.main()
