"""#590 -- `notify --run-card` was a SILENT no-op when --repo (or --issue) was
missing: `_notify_run_card`'s first branch `if not repo or issue is None:
return` exited 0, wrote NO delivery-log line, and printed NOTHING even under
--dry-run. That is the exact #134/#135 hole (a non-delivered card MUST exit
non-zero AND log a reason) at a branch #134/#135 never reached -- present since
9bee24a1 (2026-06-20), and the branch the airuleset supervisor hit by firing
run-cards without --repo (~20 closed tickets #564-#586, zero cards, zero log
after 2026-08-17T17:09).

Every test here asserts the FIXED behaviour and therefore FAILS against the
pre-fix silent `return`:

  (a) a non-delivered run-card (missing --repo/--issue) EXITS NON-ZERO and
      writes a delivery-log `refused ... reason=missing ...` line (#134/#135);
  (b) --dry-run ALWAYS PRINTS its refuse-would decision + the reason (a silent
      dry-run is undiagnosable), exits non-zero, and writes NO durable log
      (the established `_run_card_refuse` dry-run contract);
  (c) the REGRESSION LOCK: `_notify_run_card` contains ZERO bare `return` and
      ZERO `sys.exit(0)` -- every code path terminates in `sent`(logged) or a
      non-zero exit + logged reason, so no future edit can re-open a silent
      branch.
"""
import ast
import contextlib
import inspect
import io
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset                                          # noqa: E402
import notify                                             # noqa: E402


def _run_card_args(**overrides):
    """A `notify --run-card` args Mock with every OTHER `cmd_notify`
    early-return flag pinned False (a `m.Mock` auto-vivifies every attribute
    truthy, which would hijack the dispatch)."""
    base = dict(
        run_card=True, autopilot_done=False, mention_prefix=False, content_dedup_claim=False,
        repo_name=False, newest_card=False, backfill_digest=False,
        provision_question_thread=False, provision_project_thread=False,
        project_label=False, record_question=False, edit_question=False,
        channel_id=False, owner=False, mirror_owners=False, question_ping_off=False, body=None, run=None,
        repo="o/x", issue=586, pr=None,
        achieved="Zmergované 4d840f1d, suita zelená, overené",
        result=None, goal="Oprava tichej vetvy run-card",
        version="0.1.0", merge_sha="4d840f1d", url=None, review="ok",
        handoff=False, dedup_key=None, dry_run=False,
    )
    base.update(overrides)
    return m.Mock(**base)


class _Harness(unittest.TestCase):
    def setUp(self):
        # Isolate the notify delivery-log / marker store off the real ~/.claude.
        self._home = tempfile.mkdtemp(prefix="airuleset-runcard590-")
        self.addCleanup(shutil.rmtree, self._home, True)
        cd = m.patch.object(notify, "_claude_dir",
                            return_value=os.path.join(self._home, ".claude"))
        cd.start()
        self.addCleanup(cd.stop)

    def _run(self, args, expect_gh=False):
        """Run cmd_notify; return (stdout, stderr, exit_code, gh_call_count).

        `notify.send` is stubbed to `sent`. `_gh_out` returns a benign JSON so
        the happy path can compose a card; `expect_gh=False` additionally
        asserts the refusal returned BEFORE spending a gh fetch."""
        out, err = io.StringIO(), io.StringIO()
        code = None
        gh = m.Mock(return_value='{"title": "T", "labels": []}')
        with m.patch.object(airuleset, "_gh_out", gh):
            with m.patch("notify.send", return_value="sent"):
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err):
                    try:
                        airuleset.cmd_notify(args)
                    except SystemExit as exc:
                        code = exc.code
        if not expect_gh:
            self.assertEqual(gh.call_count, 0,
                             "a missing-repo/issue refusal must not spend a gh call")
        return out.getvalue(), err.getvalue(), code, gh.call_count

    def _delivery_lines(self):
        try:
            return Path(notify.delivery_log_path()).read_text(
                encoding="utf-8").splitlines()
        except OSError:
            return []


class TestMissingRepoIsLoud(_Harness):
    def test_missing_repo_real_exits_nonzero_and_logs(self):
        # (a) + (c-repro): the exact issue #1/#2 shape -- no --repo.
        args = _run_card_args(repo=None)
        out, err, code, _ = self._run(args)
        self.assertEqual(code, 1, "missing --repo must exit non-zero (#134)")
        lines = [ln for ln in self._delivery_lines()
                 if "kind=run-card" in ln and "refused" in ln]
        self.assertTrue(lines, "missing --repo must write a refused "
                               "delivery-log line (#135); log=%r" % self._delivery_lines())
        self.assertIn("missing", lines[-1])
        self.assertIn("repo", lines[-1])
        # a reason a human can act on reached stderr too
        self.assertIn("repo", err.lower())

    def test_missing_issue_real_exits_nonzero_and_logs(self):
        args = _run_card_args(issue=None)
        out, err, code, _ = self._run(args)
        self.assertEqual(code, 1)
        lines = [ln for ln in self._delivery_lines()
                 if "kind=run-card" in ln and "refused" in ln]
        self.assertTrue(lines, "missing --issue must log a refused line")
        self.assertIn("missing", lines[-1])
        self.assertIn("issue", lines[-1])

    def test_both_missing_real_exits_nonzero_and_logs(self):
        args = _run_card_args(repo=None, issue=None)
        out, err, code, _ = self._run(args)
        self.assertEqual(code, 1)
        lines = [ln for ln in self._delivery_lines()
                 if "kind=run-card" in ln and "refused" in ln]
        self.assertTrue(lines)
        self.assertIn("repo", lines[-1])
        self.assertIn("issue", lines[-1])


class TestMissingRepoDryRunPrintsDecision(_Harness):
    def test_dry_run_missing_repo_prints_refuse_decision_no_log(self):
        # (b): --dry-run MUST print its decision + reason, exit non-zero, and
        # write NO durable log line (dry-run's "preview, no side effects").
        args = _run_card_args(repo=None, dry_run=True)
        out, err, code, _ = self._run(args)
        self.assertEqual(code, 1, "dry-run refusal still exits non-zero")
        printed = (out + err)
        self.assertIn("REFUSED", printed.upper(),
                      "a --dry-run refusal must PRINT its decision, never be silent")
        self.assertIn("repo", printed.lower())
        self.assertEqual(
            [ln for ln in self._delivery_lines() if "kind=run-card" in ln], [],
            "a --dry-run refusal must NOT write a durable delivery-log line")

    def test_dry_run_missing_issue_prints_refuse_decision(self):
        args = _run_card_args(issue=None, dry_run=True)
        out, err, code, _ = self._run(args)
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", (out + err).upper())
        self.assertIn("issue", (out + err).lower())


class TestHappyPathStillPrintsDecision(_Harness):
    def test_valid_dry_run_prints_send_would_and_exits_zero(self):
        # The fix must not disturb the send-would decision: a VALID card in
        # --dry-run still prints its body + `dry-run` and exits 0.
        args = _run_card_args(dry_run=True)

        def send_previewing(body, **k):
            # mirror send()'s dry-run: print the body, return the status
            sys.stdout.write(body + "\n")
            return "dry-run"
        out = io.StringIO()
        code = None
        with m.patch.object(airuleset, "_gh_out",
                            return_value='{"title":"T","labels":[]}'):
            with m.patch("notify.send", side_effect=send_previewing):
                with contextlib.redirect_stdout(out):
                    try:
                        airuleset.cmd_notify(args)
                    except SystemExit as exc:
                        code = exc.code
        self.assertIsNone(code, out.getvalue())
        self.assertIn("dry-run", out.getvalue())
        self.assertIn("Cieľ", out.getvalue())


class TestNoSilentBranchRegressionLock(_Harness):
    """The structural invariant: the run-card path has NO silent exit-0 branch
    -- no bare `return`, and no exit-0-equivalent call (`sys.exit()` /
    `sys.exit(0)` / `sys.exit(None)` / `os._exit(0)`) -- every non-delivery
    path is loud (non-zero exit + logged reason) or a logged `sent`. Both
    `_notify_run_card` AND its `_run_card_require_repo_and_issue` helper are
    scanned (review 🔵: the helper is the branch #590 restored, so a future
    bare `return` slipped in there would re-open the exact hole). Locks #590
    so no future edit re-opens a silent branch."""

    @staticmethod
    def _silent_exit_lines(fn):
        """Line numbers of any silent exit-0 shape in an AST FunctionDef:
        a bare `return` (value None -> exit 0), or an `.exit(...)` call
        (sys.exit / os._exit) with NO arg, a `0` arg, or a `None` arg."""
        bare = [n.lineno for n in ast.walk(fn)
                if isinstance(n, ast.Return) and n.value is None]
        exit0 = []
        for n in ast.walk(fn):
            if not (isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("exit", "_exit")):
                continue
            if not n.args:                       # sys.exit() -> exit 0
                exit0.append(n.lineno)
            elif (isinstance(n.args[0], ast.Constant)
                  and n.args[0].value in (0, None)):   # exit(0) / exit(None)
                exit0.append(n.lineno)
        return bare, exit0

    def test_run_card_paths_have_no_silent_exit(self):
        for name in ("_notify_run_card", "_run_card_require_repo_and_issue"):
            fn = ast.parse(inspect.getsource(getattr(airuleset, name))).body[0]
            bare, exit0 = self._silent_exit_lines(fn)
            self.assertEqual(bare, [],
                             "%s has a bare `return` (silent exit-0) at "
                             "line(s) %s -- every non-delivery must exit "
                             "non-zero + log (#134/#135)" % (name, bare))
            self.assertEqual(exit0, [],
                             "%s has a silent exit-0 call (sys.exit()/"
                             "sys.exit(0)/os._exit(0)) at %s" % (name, exit0))


if __name__ == "__main__":
    unittest.main()
