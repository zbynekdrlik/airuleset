"""#1064 -- the `Design-by:` stamp records the session's CONFIGURED (launch)
model and carries the SERVED (API-returned) model only as an audit suffix, so an
API model FLOAT (a Fable-launched main served another model one turn) never makes
the dispatch gate refuse the Fable main's own design.

`cli_authorship.session_model` = the newest served model in the transcript (kept
for the audit suffix). `cli_authorship.configured_model` = the launch identity:
the pane's claude argv `--model <id>` (READ-ONLY reuse of the watchdog pane
primitive, seamed via `_pane_configured_model`), else the managed default
(`_managed_model`, MANAGED_MODEL normalised), else `unknown`. `stamp_line` shows
the configured id and appends ` (served: <served>)` ONLY when the served model
is known AND differs -- today's stamps stay byte-identical when they match. The
dispatch gate `check_issue` keys on the configured id (accepted set unchanged);
its `_DESIGN_BY_RE` tolerates + captures the served suffix; the anti-spoof gate
and the model-float audit are untouched.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import cli_authorship  # noqa: E402
import cli_design_record as dr  # noqa: E402
from gates import designbypost as dbp  # noqa: E402
from gates import designdispatch as dd  # noqa: E402
from watchdog.transcripts import encode_project_dir  # noqa: E402

MAIN_CWD = "/home/airuleset/devel/airuleset"
FABLE = "claude-fable-5-1"


def _write_transcript(pd, cwd, model):
    d = pd / encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    (d / "s.jsonl").write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "model": model,
                    "content": [{"type": "text", "text": "hi"}]}}) + "\n")


class TestConfiguredModel(unittest.TestCase):
    def test_reads_pane_argv_model_normalised(self):
        # the pane's `--model claude-fable-5-1[1m]` -> normalised tier id.
        with mock.patch("cli_authorship._pane_configured_model",
                        return_value="claude-fable-5-1[1m]"):
            self.assertEqual(cli_authorship.configured_model(MAIN_CWD), FABLE)

    def test_falls_back_to_managed_model_when_no_pane(self):
        # no pane/argv resolves -> the managed default (normalised).
        with mock.patch("cli_authorship._pane_configured_model",
                        return_value=None):
            self.assertEqual(cli_authorship.configured_model(MAIN_CWD), FABLE)

    def test_unknown_only_when_neither_resolves(self):
        with mock.patch("cli_authorship._pane_configured_model",
                        return_value=None), \
             mock.patch("cli_authorship._managed_model", return_value=None):
            self.assertEqual(cli_authorship.configured_model(MAIN_CWD),
                             cli_authorship.UNKNOWN_MODEL)

    def test_implementer_alias_is_the_configured_model(self):
        saved = {k: os.environ.get(k)
                 for k in ("AIRULESET_ROLE", "ANTHROPIC_MODEL")}
        os.environ["AIRULESET_ROLE"] = "implementer"
        os.environ["ANTHROPIC_MODEL"] = "impl-main"
        try:
            self.assertEqual(
                cli_authorship.configured_model(
                    "/home/x/.claude/worktrees/agent-a"),
                "impl-main")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class TestStampSuffix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pd = Path(self.tmp) / "projects"

    def _stamp(self):
        return cli_authorship.stamp_line("Design", MAIN_CWD,
                                         projects_dir=str(self.pd))

    def test_float_adds_served_suffix(self):
        # served (transcript) = opus-5, configured (launch) = fable -> suffix.
        _write_transcript(self.pd, MAIN_CWD, "claude-opus-5")
        with mock.patch("cli_authorship._pane_configured_model",
                        return_value="claude-fable-5-1[1m]"):
            self.assertEqual(
                self._stamp(),
                "Design-by: main claude-fable-5-1 (served: claude-opus-5)")

    def test_no_suffix_when_served_equals_configured(self):
        # today's byte-identical stamp when served == configured.
        _write_transcript(self.pd, MAIN_CWD, FABLE)
        with mock.patch("cli_authorship._pane_configured_model",
                        return_value=FABLE):
            self.assertEqual(self._stamp(), "Design-by: main claude-fable-5-1")

    def test_no_suffix_when_served_unknown(self):
        # no transcript -> served unknown -> stamp the configured id, no suffix.
        with mock.patch("cli_authorship._pane_configured_model",
                        return_value="claude-fable-5-1[1m]"):
            self.assertEqual(self._stamp(), "Design-by: main claude-fable-5-1")

    def test_stamp_unknown_when_neither_resolves(self):
        # configured unresolvable AND no served -> `unknown`, role still labelled.
        with mock.patch("cli_authorship._pane_configured_model",
                        return_value=None), \
             mock.patch("cli_authorship._managed_model", return_value=None):
            self.assertEqual(self._stamp(), "Design-by: main unknown")

    def test_configured_unknown_but_served_known_uses_served(self):
        # degenerate: configured unresolvable, served known -> stamp served.
        _write_transcript(self.pd, MAIN_CWD, "claude-opus-5")
        with mock.patch("cli_authorship._pane_configured_model",
                        return_value=None), \
             mock.patch("cli_authorship._managed_model", return_value=None):
            self.assertEqual(self._stamp(), "Design-by: main claude-opus-5")


class TestDispatchGateSuffix(unittest.TestCase):
    def test_suffixed_main_stamp_accepted(self):
        ok, _ = dd.check_issue(
            1064, "o/r", "/repo",
            fetch=lambda s, n, c: (
                ["Design-by: main claude-fable-5-1 (served: claude-opus-5)"],
                None),
            fable_id=FABLE)
        self.assertTrue(ok)

    def test_bare_pre_fix_float_stamp_refused(self):
        # a pre-fix post carrying only the served id (no configured suffix).
        ok, reason = dd.check_issue(
            1064, "o/r", "/repo",
            fetch=lambda s, n, c: (["Design-by: main claude-opus-5"], None),
            fable_id=FABLE)
        self.assertFalse(ok)
        self.assertIn("claude-opus-5", reason)

    def test_worker_suffixed_stamp_still_refused(self):
        ok, _ = dd.check_issue(
            1064, "o/r", "/repo",
            fetch=lambda s, n, c: (
                ["Design-by: worker claude-opus-4-8 (served: claude-opus-5)"],
                None),
            fable_id=FABLE)
        self.assertFalse(ok)

    def test_refused_suffixed_stamp_reason_names_served(self):
        # a refused suffixed stamp (configured not fable) surfaces the served id.
        ok, reason = dd.check_issue(
            1064, "o/r", "/repo",
            fetch=lambda s, n, c: (
                ["Design-by: main claude-opus-4-8 (served: claude-opus-5)"],
                None),
            fable_id=FABLE)
        self.assertFalse(ok)
        self.assertIn("claude-opus-4-8", reason)
        self.assertIn("claude-opus-5", reason)

    def test_regex_captures_role_model_and_served(self):
        m = dd._DESIGN_BY_RE.search(
            "Design-by: main claude-fable-5-1 (served: claude-opus-5)")
        self.assertEqual(m.group("role"), "main")
        self.assertEqual(m.group("model"), "claude-fable-5-1")
        self.assertEqual(m.group("served"), "claude-opus-5")

    def test_newest_design_by_still_two_tuple(self):
        # the (role, model) 2-tuple contract is unchanged; served is separate.
        self.assertEqual(
            dd.newest_design_by(
                ["Design-by: main claude-fable-5-1 (served: claude-opus-5)"]),
            ("main", "claude-fable-5-1"))


class TestAntiSpoofUnchanged(unittest.TestCase):
    def test_lane_author_suffixed_main_stamp_still_blocked(self):
        wt = MAIN_CWD + "/.claude/worktrees/agent-x"
        body = ("gh issue comment 5 --body "
                '"Design-by: main claude-fable-5-1 (served: claude-opus-5)"')
        v, _ = dbp.evaluate(body, wt)
        self.assertEqual(v, "block")


class TestDesignRecordFloat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.pd = Path(self.tmp) / "projects"
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home

    VALID = ("Triage: trivial -- a one-line scoped fix.\n\n"
             "Root cause: traced in reader.py:42 the old key is read. Chosen "
             "approach: read the new key with a fallback. Rejected alternative: "
             "a migration shim -- overkill for one key.\n\n"
             "Shared-benefit: single-client -- only MIVA uses this key.\n")

    def test_compose_body_stamps_configured_with_served_suffix(self):
        _write_transcript(self.pd, MAIN_CWD, "claude-opus-5")
        with mock.patch("cli_authorship._pane_configured_model",
                        return_value="claude-fable-5-1[1m]"):
            out = dr.compose_body("design body", MAIN_CWD,
                                  projects_dir=str(self.pd))
        self.assertIn(
            "Design-by: main claude-fable-5-1 (served: claude-opus-5)", out)

    def test_post_and_record_posts_float_configured_stamp(self):
        _write_transcript(self.pd, MAIN_CWD, "claude-opus-5")
        posted = {}

        def runner(argv, body):
            posted["body"] = body
            return (0, "https://github.com/o/r/issues/1064#c1", "")

        with mock.patch("cli_authorship._pane_configured_model",
                        return_value="claude-fable-5-1[1m]"):
            ok, url, stamp = dr.post_and_record(
                issue=1064, repo="zbynekdrlik/airuleset", raw_body=self.VALID,
                cwd=MAIN_CWD, runner=runner, projects_dir=str(self.pd),
                home=self.home)
        self.assertTrue(ok, (url, stamp))
        self.assertIn(
            "Design-by: main claude-fable-5-1 (served: claude-opus-5)",
            posted["body"])
        # the composed float stamp is ACCEPTED by the dispatch gate.
        okg, _ = dd.check_issue(
            1064, "o/r", "/repo",
            fetch=lambda s, n, c: ([posted["body"]], None), fable_id=FABLE)
        self.assertTrue(okg, "the float stamp must pass the dispatch gate")

    def test_post_and_record_refuses_only_when_neither_resolves(self):
        # no transcript (served unknown) AND configured unresolvable -> refuse.
        called = {}

        def runner(argv, body):
            called["yes"] = True
            return (0, "url", "")

        with mock.patch("cli_authorship._pane_configured_model",
                        return_value=None), \
             mock.patch("cli_authorship._managed_model", return_value=None):
            ok, reason, stamp = dr.post_and_record(
                issue=1064, repo="zbynekdrlik/airuleset", raw_body=self.VALID,
                cwd="/home/airuleset/no-transcript", runner=runner,
                projects_dir=str(self.pd), home=self.home)
        self.assertFalse(ok)
        self.assertIsNone(stamp)
        self.assertIn("unknown", reason.lower())
        self.assertNotIn("yes", called)

    def test_post_and_record_no_longer_refuses_a_main_float(self):
        # the #1061 refusal must NOT fire for a main whose transcript is
        # unreadable but whose configured model resolves (the deploy defect).
        posted = {}

        def runner(argv, body):
            posted["body"] = body
            return (0, "url#c", "")

        with mock.patch("cli_authorship._pane_configured_model",
                        return_value="claude-fable-5-1[1m]"):
            ok, url, stamp = dr.post_and_record(
                issue=1064, repo="zbynekdrlik/airuleset", raw_body=self.VALID,
                cwd="/home/airuleset/no-transcript", runner=runner,
                projects_dir=str(self.pd), home=self.home)
        self.assertTrue(ok, (url, stamp))
        self.assertIn("Design-by: main claude-fable-5-1", posted["body"])


if __name__ == "__main__":
    unittest.main()
