"""#1012 — the exec-marker STATE-dir seam (`AIRULESET_MAIN_EXEC_STATE_DIR`).

Root cause: `cleanup_stale_exec_markers` (Job 22) defaulted `tmp_dir="/tmp"` and
BOTH hooks hardcoded `/tmp`, so 36 test files driving `run_once` with a synthetic
far-future clock swept the LIVE `/tmp` marker family — deleting fresh test markers
(`2 != 0`) and live sessions' granted one-shots. The fix routes the marker
directory through ONE env seam (mirroring #732's `AIRULESET_MAIN_EXEC_LOG_DIR`).

RED (fails before the fix):
  * the sweeper's default (no `tmp_dir`) still lists `/tmp`, ignoring the env;
  * both hooks read `/tmp`, so a marker under the seam dir is not honoured;
  * conftest / the push-gate `test_env` do not set the seam yet;
  * test files still hardcode `/tmp/...exec...` marker paths.
"""
import ast
import json
import os
import sys
import time
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import watchdog as wd                       # noqa: E402
import _exec_marker_helpers as em           # noqa: E402


BIG = "x" * 40000     # over the edit threshold — a goal-armed main would block


def _no_panes(*_a, **_k):
    return ""          # tmux list-panes returns no panes -> no live session


def _goal_armed_transcript(model="claude-opus-4-8"):
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "/autopilot"}}),
        json.dumps({"type": "user", "message": {"role": "user",
            "content": "<local-command-stdout>Goal set: do the backlog"
                       "</local-command-stdout>"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant",
            "model": model, "content": [{"type": "text", "text": "working"}]}}),
    ]
    return "\n".join(lines) + "\n"


class SweeperHonoursStateDir1012(unittest.TestCase):
    def test_default_tmp_dir_reads_the_state_dir_env(self):
        # RED: the default (no tmp_dir=) lists /tmp and ignores the env seam.
        with TemporaryDirectory() as dA:
            sid = "t-1012-sweep-" + uuid.uuid4().hex[:8]
            with mock.patch.dict(os.environ,
                                 {"AIRULESET_MAIN_EXEC_STATE_DIR": dA}):
                m = em.marker_ok(sid)
                m.write_text("reason: this call must run here")
                logs = wd.cleanup_stale_exec_markers(
                    time.time() + 10 * 86400, run=_no_panes, dry_run=True)
            named = [ln for ln in logs if sid in ln]
            self.assertTrue(
                named,
                "the default (no tmp_dir) must sweep AIRULESET_MAIN_EXEC_STATE_DIR, "
                "not the literal /tmp; logs=%r" % logs)

    def test_662_style_run_never_sweeps_the_live_tmp(self):
        # The incident probe as a REAL test: with the suite's seam active
        # (conftest / push-gate test_env point it at a per-run dir), a
        # synthetic-far-future `run_once`-style sweep must target the seam dir,
        # NOT the LIVE /tmp — so a marker sitting in the real /tmp (a live
        # session's granted one-shot) SURVIVES. RED before the fix: the default
        # /tmp sweep deletes it. Safe: the non-dry sweep only touches the
        # isolated seam dir; the /tmp probe is fresh (age 0) so the real 60s
        # watchdog never reaps it either, and we remove it ourselves.
        self.assertNotEqual(
            em.exec_state_dir().rstrip("/"), "/tmp",
            "the suite must isolate the marker dir off the live /tmp first")
        probe = Path("/tmp") / ("airuleset-main-exec-ok-t-1012-probe-%s"
                                % uuid.uuid4().hex[:10])
        probe.write_text("a live session's deliberately granted one-shot")
        self.addCleanup(lambda: probe.unlink(missing_ok=True))
        wd.cleanup_stale_exec_markers(
            time.time() + 10 * 86400, run=_no_panes, dry_run=False)
        self.assertTrue(
            probe.exists(),
            "a synthetic-clock run_once sweep must NOT reach the live /tmp "
            "marker family (the #1012 incident)")

    def test_explicit_tmp_dir_still_wins(self):
        # an explicit tmp_dir= caller (the Job 22 watchdog tests) is unaffected.
        with TemporaryDirectory() as dA, TemporaryDirectory() as dEnv:
            sid = "t-1012-explicit-" + uuid.uuid4().hex[:8]
            (Path(dA) / ("airuleset-main-exec-ok-%s" % sid)).write_text("r")
            with mock.patch.dict(os.environ,
                                 {"AIRULESET_MAIN_EXEC_STATE_DIR": dEnv}):
                logs = wd.cleanup_stale_exec_markers(
                    time.time() + 10 * 86400, run=_no_panes, dry_run=True,
                    tmp_dir=dA)
            self.assertTrue([ln for ln in logs if sid in ln], logs)


def em_pending_exists(sid, state_dir):
    return (Path(state_dir) / ("airuleset-main-exec-pending-%s" % sid)).exists()


class HarnessSeamLock1012(unittest.TestCase):
    def test_state_dir_env_is_set_for_the_test_suite(self):
        # RED: conftest (pytest) / the push-gate test_env (discover) must set the
        # seam to a per-run dir != /tmp so no test reaches the live /tmp family.
        val = os.environ.get("AIRULESET_MAIN_EXEC_STATE_DIR")
        self.assertTrue(val, "AIRULESET_MAIN_EXEC_STATE_DIR must be set for the suite")
        self.assertNotEqual(val.rstrip("/"), "/tmp",
                            "the suite must NOT point the seam at the live /tmp")

    def test_push_gate_test_env_carries_the_seam(self):
        # RED: cmd_push's test_env must set the seam (unittest discover never
        # reads conftest.py — the #385 dual-coverage rule).
        src = (REPO / "cli_remote.py").read_text(encoding="utf-8")
        self.assertIn('test_env["AIRULESET_MAIN_EXEC_STATE_DIR"]', src,
                      "cmd_push's test_env must set AIRULESET_MAIN_EXEC_STATE_DIR")

    def test_no_hardcoded_tmp_marker_path_under_tests(self):
        # RED: no test file may hardcode a /tmp exec-marker STATE path — all go
        # through _exec_marker_helpers.py (the seam). Docstrings + comments are
        # exempt (ast never sees comments; docstrings are skipped).
        forbidden = ("/tmp/airuleset-main-exec-ok-",
                     "/tmp/airuleset-fable-exec-ok-",
                     "/tmp/airuleset-main-exec-pending-")
        allowed = {"_exec_marker_helpers.py", Path(__file__).name}
        offenders = {}
        for py in sorted((REPO / "tests").glob("*.py")):
            if py.name in allowed:
                continue
            hits = _forbidden_marker_literals(py, forbidden)
            if hits:
                offenders[py.name] = hits
        self.assertEqual(
            offenders, {},
            "hardcoded /tmp exec-marker STATE paths must go through "
            "_exec_marker_helpers.py (the AIRULESET_MAIN_EXEC_STATE_DIR seam): %r"
            % offenders)


def _forbidden_marker_literals(pyfile, forbidden):
    """Non-docstring string constants in `pyfile` containing any `forbidden`
    substring. Comments are invisible to ast; docstrings are collected and
    skipped, so only real code-path literals are reported."""
    tree = ast.parse(pyfile.read_text(encoding="utf-8"))
    docstring_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstring_ids.add(id(body[0].value))
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstring_ids):
            for f in forbidden:
                if f in node.value:
                    hits.append((getattr(node, "lineno", 0), f))
    return hits


if __name__ == "__main__":
    unittest.main()
