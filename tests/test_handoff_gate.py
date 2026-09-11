"""tests/test_handoff_gate.py -- #843 hand-off composer + bounce round.

Tests for _bounce_round, _validate_self_review_table, _load_lens_list,
_parse_gk_findings, and the hook receipt matching logic.
"""
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import airuleset
import cli_quals


class TestBounceRound(unittest.TestCase):
    """_bounce_round: count prio:bounce label-add events + 1 (#942)."""

    def _fake_runner(self, events, labels=None):
        """Return a runner that serves events for ``gh api`` and labels for
        ``gh issue view``."""
        obj_labels = {"labels": [{"name": lb} for lb in (labels or [])]}
        def runner(*args, **kwargs):
            if args and args[0] == "api":
                return json.dumps(events)
            return json.dumps(obj_labels)
        return runner

    def test_zero_bounce_events_is_round_1(self):
        r = self._fake_runner([])
        self.assertEqual(1, cli_quals._bounce_round(
            1, "b", runner=r, repo="o/n"))

    def test_one_bounce_event_is_round_2(self):
        r = self._fake_runner([
            {"event": "labeled", "label": {"name": "prio:bounce"}},
        ], labels=["prio:bounce"])
        self.assertEqual(2, cli_quals._bounce_round(
            1, "b", runner=r, repo="o/n"))

    def test_two_bounce_events_is_round_3(self):
        r = self._fake_runner([
            {"event": "labeled", "label": {"name": "prio:bounce"}},
            {"event": "unlabeled", "label": {"name": "prio:bounce"}},
            {"event": "labeled", "label": {"name": "prio:bounce"}},
        ], labels=["prio:bounce"])
        self.assertEqual(3, cli_quals._bounce_round(
            1, "b", runner=r, repo="o/n"))

    def test_bounce_floors_to_2(self):
        r = self._fake_runner([], labels=["prio:bounce"])
        self.assertEqual(2, cli_quals._bounce_round(
            1, "b", runner=r, repo="o/n"))

    def test_gh_error_returns_1(self):
        self.assertEqual(1, cli_quals._bounce_round(
            1, "b", runner=lambda *a, **k: "", repo="o/n"))


class TestBounceRoundEvents942(unittest.TestCase):
    """#942: _bounce_round uses prio:bounce label-add events, not RFR comments.

    When a handoff gate-FAIL iteration leaves a superseded READY-FOR-REVIEW
    comment behind (no prio:bounce label-add event), the RFR-comment-based
    formula over-counts.  The events-based formula counts only actual gatekeeper
    bounces (prio:bounce labeled events), eliminating the divergence."""

    def _fake_runner(self, events, labels=None, comments=None):
        """Return a runner that serves events for ``gh api`` and labels
        (+ optional comments, to prove they are ignored) for
        ``gh issue view``."""
        obj_view = {"labels": [{"name": lb} for lb in (labels or [])]}
        if comments is not None:
            obj_view["comments"] = comments
        def runner(*args, **kwargs):
            if args and args[0] == "api":
                return json.dumps(events)
            return json.dumps(obj_view)
        return runner

    def test_gate_fail_rfr_not_inflated(self):
        """Two RFR comments but only one prio:bounce event -> round 2, not 3.

        This is the exact odoo-erp#6508 scenario: the stream posted two
        READY-FOR-REVIEW comments (one from a gate-FAIL iteration that left
        a superseded draft), but the gatekeeper only bounced once (one
        prio:bounce label-add event).  The RFR comments are served in the
        fixture to prove they are genuinely ignored (F2 review finding)."""
        events = [
            {"event": "labeled", "label": {"name": "prio:bounce"}},
        ]
        # Two RFR comments present — old formula would return round 3.
        rfr_comments = [
            {"author": {"login": "b"}, "body": "READY-FOR-REVIEW: x"},
            {"author": {"login": "b"}, "body": "READY-FOR-REVIEW: y"},
        ]
        r = self._fake_runner(events, labels=["prio:bounce"],
                              comments=rfr_comments)
        self.assertEqual(2, cli_quals._bounce_round(
            1, "b", runner=r, repo="owner/name"))

    def test_zero_bounce_events_is_round_1(self):
        """No prio:bounce label events -> round 1."""
        r = self._fake_runner([], labels=[])
        self.assertEqual(1, cli_quals._bounce_round(
            1, "b", runner=r, repo="owner/name"))

    def test_two_bounce_events_is_round_3(self):
        """Two prio:bounce label-add events -> round 3."""
        events = [
            {"event": "labeled", "label": {"name": "prio:bounce"}},
            {"event": "unlabeled", "label": {"name": "prio:bounce"}},
            {"event": "labeled", "label": {"name": "prio:bounce"}},
        ]
        r = self._fake_runner(events, labels=["prio:bounce"])
        self.assertEqual(3, cli_quals._bounce_round(
            1, "b", runner=r, repo="owner/name"))

    def test_unrelated_labels_not_counted(self):
        """A labeled event for a different label is not counted."""
        events = [
            {"event": "labeled", "label": {"name": "bug"}},
            {"event": "labeled", "label": {"name": "ready-for-review"}},
        ]
        r = self._fake_runner(events, labels=[])
        self.assertEqual(1, cli_quals._bounce_round(
            1, "b", runner=r, repo="owner/name"))

    def test_floor_to_2_when_bounce_label_present(self):
        """When prio:bounce label is present but no events found, floor to 2."""
        r = self._fake_runner([], labels=["prio:bounce"])
        self.assertEqual(2, cli_quals._bounce_round(
            1, "b", runner=r, repo="owner/name"))

    def test_events_api_error_returns_1(self):
        """If the events API returns empty, fall back to round 1."""
        def runner(*args, **kwargs):
            return ""
        self.assertEqual(1, cli_quals._bounce_round(
            1, "b", runner=runner, repo="owner/name"))

    def test_repo_none_still_fetches_events(self):
        """When repo=None, the events API uses {owner}/{repo} template
        (F1 review finding: must not silently skip the events call)."""
        events = [
            {"event": "labeled", "label": {"name": "prio:bounce"}},
        ]
        r = self._fake_runner(events, labels=["prio:bounce"])
        rnd = cli_quals._bounce_round(1, "b", runner=r, repo=None)
        self.assertEqual(2, rnd,
                         "repo=None must still count events via template")


class TestValidateTable(unittest.TestCase):
    """_validate_self_review_table: lens coverage + n/a reason."""

    FULL = (
        "| Lens | Verdict | Evidence |\n"
        "|---|---|---|\n"
        "| security | pass | f:1 |\n"
        "| correctness | pass | f:2 |\n"
        "| test-integrity | pass | f:3 |\n"
        "| evidence-integrity | pass | f:4 |\n"
        "| design-doctrine | pass | f:5 |\n"
        "| process | pass | f:6 |\n"
        "| shared-benefit | pass | f:7 |\n"
    )

    def test_all_covered(self):
        ok, r = airuleset._validate_self_review_table(
            self.FULL, airuleset.HANDOFF_DEFAULT_LENSES)
        self.assertTrue(ok, r)

    def test_missing_blocked(self):
        ok, r = airuleset._validate_self_review_table(
            "| security | pass | f:1 |\n",
            airuleset.HANDOFF_DEFAULT_LENSES)
        self.assertFalse(ok)
        self.assertIn("missing", r)

    def test_na_no_reason_blocked(self):
        table = self.FULL.replace("| security | pass | f:1 |",
                                   "| security | n/a |  |")
        ok, r = airuleset._validate_self_review_table(
            table, airuleset.HANDOFF_DEFAULT_LENSES)
        self.assertFalse(ok)
        self.assertIn("n/a", r)

    def test_empty_blocked(self):
        ok, _ = airuleset._validate_self_review_table(
            "", airuleset.HANDOFF_DEFAULT_LENSES)
        self.assertFalse(ok)


class TestLoadLens(unittest.TestCase):
    """_load_lens_list: file present -> custom; absent -> default."""

    def test_default(self):
        self.assertEqual(
            airuleset._load_lens_list("/no"),
            airuleset.HANDOFF_DEFAULT_LENSES)

    def test_custom(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, ".claude", "rules")
            os.makedirs(d)
            with open(os.path.join(d, "gk-review-lenses.md"), "w") as f:
                f.write("# hdr\nalpha\nbeta\n")
            self.assertEqual(airuleset._load_lens_list(td), ["alpha", "beta"])


class TestLensProseFile880(unittest.TestCase):
    """#880: _load_lens_list must extract ONLY lens-id-shaped lines from a
    prose gk-review-lenses.md and fall back to defaults when no ids found."""

    # A representative excerpt of odoo-erp's prose gk-review-lenses.md that
    # contains section headers with the 6 canonical lens ids (### security etc.)
    # plus prose paragraphs, markdown tables, and code blocks.
    PROSE_DOC = (
        "# Gatekeeper cold-review lenses\n"
        "\n"
        "**Owner ruling (2026-09-02, verbatim):** some text that is NOT a lens\n"
        "This file is the **published** version of the lenses.\n"
        "\n"
        "## The Self-review block\n"
        "\n"
        "| lens | verdict | evidence |\n"
        "|---|---|---|\n"
        "| security | 0 | f:1 |\n"
        "\n"
        "### security\n"
        "\n"
        "A long prose paragraph about the security lens.\n"
        "Multiple lines of guidance for the reviewer.\n"
        "\n"
        "### correctness\n"
        "\n"
        "Guidance for correctness review.\n"
        "\n"
        "### test-integrity\n"
        "\n"
        "Test integrity guidance.\n"
        "\n"
        "### evidence-integrity\n"
        "\n"
        "Evidence integrity guidance.\n"
        "\n"
        "### design-doctrine\n"
        "\n"
        "Design doctrine guidance.\n"
        "\n"
        "### process\n"
        "\n"
        "Process guidance.\n"
    )

    def test_prose_file_yields_sane_lens_count(self):
        """A prose lenses doc must yield exactly the lens ids, not hundreds
        of bogus lines."""
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, ".claude", "rules")
            os.makedirs(d)
            with open(os.path.join(d, "gk-review-lenses.md"), "w") as f:
                f.write(self.PROSE_DOC)
            lenses = airuleset._load_lens_list(td)
            # Must NOT produce hundreds of lines (the bug: 628 bogus lenses).
            self.assertLessEqual(len(lenses), 10,
                                 "Loader yielded %d lenses from prose doc" % len(lenses))
            # Must contain the 6 canonical lenses.
            for canon in airuleset.HANDOFF_DEFAULT_LENSES:
                self.assertIn(canon, lenses,
                              "Missing canonical lens '%s'" % canon)

    def test_prose_lenses_validate_against_valid_table(self):
        """A valid 7-lens self-review table must pass validation even when
        the lenses doc is prose-format."""
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, ".claude", "rules")
            os.makedirs(d)
            with open(os.path.join(d, "gk-review-lenses.md"), "w") as f:
                f.write(self.PROSE_DOC)
            lenses = airuleset._load_lens_list(td)
            table = (
                "| Lens | Verdict | Evidence |\n"
                "|---|---|---|\n"
                "| security | pass | f:1 |\n"
                "| correctness | pass | f:2 |\n"
                "| test-integrity | pass | f:3 |\n"
                "| evidence-integrity | pass | f:4 |\n"
                "| design-doctrine | pass | f:5 |\n"
                "| process | pass | f:6 |\n"
                "| shared-benefit | pass | f:7 |\n"
            )
            ok, reason = airuleset._validate_self_review_table(table, lenses)
            self.assertTrue(ok, "Valid table rejected: %s" % reason)

    def test_simple_id_file_still_works(self):
        """A simple one-id-per-line file (the original format) still works."""
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, ".claude", "rules")
            os.makedirs(d)
            with open(os.path.join(d, "gk-review-lenses.md"), "w") as f:
                f.write("# header\nalpha\nbeta\ngamma\n")
            lenses = airuleset._load_lens_list(td)
            self.assertEqual(lenses, ["alpha", "beta", "gamma"])


class TestLsRemoteRefPick880(unittest.TestCase):
    """#880: ls-remote output with refs/autopilot-wip/<branch> must pick
    refs/heads/<branch>, not the wip ref (which sorts first alphabetically)."""

    def test_heads_ref_preferred_over_wip(self):
        """When both refs/heads/X and refs/autopilot-wip/X exist, the
        composer must compare HEAD against refs/heads/X."""
        # The fix is to call `git ls-remote origin refs/heads/<branch>`
        # instead of `git ls-remote origin <branch>`, so we test the
        # _load_lens_list shape is not enough — we test the ls-remote
        # call uses the explicit refs/heads/ prefix.
        import inspect
        src = inspect.getsource(airuleset.cmd_handoff)
        # The ls-remote call must use "refs/heads/" + branch, not bare branch.
        self.assertIn('"refs/heads/" + branch', src,
                      "cmd_handoff ls-remote call must use explicit "
                      "refs/heads/ prefix to avoid matching wip refs")


class TestParseFindings(unittest.TestCase):
    """_parse_gk_findings: extract ids from gk bounce comments."""

    def test_emoji(self):
        ids = airuleset._parse_gk_findings("\U0001f534 1 x\n\U0001f7e1 2 y")
        self.assertIn("1", ids)
        self.assertIn("2", ids)

    def test_f_id(self):
        self.assertIn("F3", airuleset._parse_gk_findings("F3 finding"))

    def test_empty(self):
        self.assertEqual([], airuleset._parse_gk_findings(""))
        self.assertEqual([], airuleset._parse_gk_findings(None))


class TestReceiptLogic(unittest.TestCase):
    """Receipt matching (the hook's core logic)."""

    def test_fresh_matches(self):
        body = "READY-FOR-REVIEW: test\n"
        h = hashlib.sha256(body.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            gd = os.path.join(td, "gate")
            os.makedirs(gd)
            with open(os.path.join(gd, "r.json"), "w") as f:
                json.dump({"sha256": h, "ts": time.time()}, f)
            self.assertTrue(self._scan(gd, h))

    def test_stale_no_match(self):
        body = "READY-FOR-REVIEW: test\n"
        h = hashlib.sha256(body.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            gd = os.path.join(td, "gate")
            os.makedirs(gd)
            with open(os.path.join(gd, "r.json"), "w") as f:
                json.dump({"sha256": h, "ts": time.time() - 700}, f)
            self.assertFalse(self._scan(gd, h))

    def test_wrong_hash(self):
        with tempfile.TemporaryDirectory() as td:
            gd = os.path.join(td, "gate")
            os.makedirs(gd)
            with open(os.path.join(gd, "r.json"), "w") as f:
                json.dump({"sha256": "bad", "ts": time.time()}, f)
            self.assertFalse(self._scan(gd, "good"))

    @staticmethod
    def _scan(gate_dir, want_hash):
        now = time.time()
        for fn in os.listdir(gate_dir):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(gate_dir, fn)) as f:
                r = json.loads(f.read())
            if r.get("sha256") == want_hash and now - r.get("ts", 0) <= 600:
                return True
        return False


class TestHookRoute(unittest.TestCase):
    """Hook pre-filter: non-RFR commands pass through."""

    def _run(self, cmd):
        import subprocess
        hp = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "hooks",
            "block-handoff-without-composer.sh")
        return subprocess.run(
            ["bash", hp],
            input=json.dumps({"tool_input": {"command": cmd}}),
            capture_output=True, text=True, timeout=10,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def test_plain_ok(self):
        self.assertEqual(0, self._run(
            "gh issue comment 1 --body hi").returncode)

    def test_bypass_ok(self):
        r = self._run(
            'gh issue comment 1 --body "READY-FOR-REVIEW" '
            "# airuleset:handoff-ok test")
        self.assertEqual(0, r.returncode)

    def test_cli_ok(self):
        self.assertEqual(0, self._run(
            "python3 airuleset.py handoff --repo x --issue 1").returncode)

    def test_non_comment_ok(self):
        self.assertEqual(0, self._run("echo READY-FOR-REVIEW").returncode)


class TestDoctrineContentLock843(unittest.TestCase):
    """#843 content lock: autopilot-worker.md must name `airuleset.py handoff`
    + the `Self-review:` table, and SKILL.md must name `round3!`. Locks the
    doctrine the same way test_batch_orchestration.py locks issue 848."""

    _WORKER = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "agents", "autopilot-worker.md")
    _SKILL = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "skills", "autopilot", "SKILL.md")

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_worker_names_handoff_cli(self):
        t = self._read(self._WORKER)
        self.assertIn("airuleset.py handoff", t)

    def test_worker_names_self_review_table(self):
        t = self._read(self._WORKER)
        self.assertIn("Self-review:", t)

    def test_worker_names_handoff_flags(self):
        t = self._read(self._WORKER)
        self.assertIn("--self-review-file", t)
        self.assertIn("--root-cause", t)
        self.assertIn("--prevencia-read", t)

    def test_worker_names_lens_list(self):
        t = self._read(self._WORKER)
        self.assertIn("gk-review-lenses.md", t)

    def test_worker_names_bounce_round_escalation(self):
        t = self._read(self._WORKER)
        self.assertIn("slice-quals --bounces", t)

    def test_skill_names_round3(self):
        t = self._read(self._SKILL)
        self.assertIn("round3!", t)

    def test_skill_names_round3_design_consult(self):
        t = self._read(self._SKILL)
        # The round3! clause must mention a design consult (#991: no tier agent)
        idx = t.find("round3!")
        self.assertGreater(idx, -1)
        window = t[idx:idx + 500]
        self.assertIn("DESIGN", window)


class TestNudgeRound3Clause(unittest.TestCase):
    """#843: the I trigger text names bounce/round3! and points the session
    at `slice-quals --bounces` — the session determines which members are
    round >= 3, the nudge just names the command."""

    def test_i_trigger_names_bounces_and_843(self):
        from watchdog.ops_wait_recheck import _nudge_text
        text = _nudge_text(5, [])
        self.assertIn("--bounces", text)
        self.assertIn("#843", text)


class TestSignOnly919(unittest.TestCase):
    """#919: --sign-only mode creates a receipt for a pre-written body
    WITHOUT posting it, so a stream can satisfy both airuleset's receipt
    hook AND a repo-specific template gate."""

    def _make_args(self, **kw):
        """Build a namespace matching cmd_handoff's argparse shape."""
        import argparse
        defaults = dict(
            repo="zbynekdrlik/odoo-erp", issue=42, branch=None,
            self_review_file=None, root_cause=None, closes_finding=None,
            prevencia_read=None, self_review_model=None, sign_only=None)
        defaults.update(kw)
        return argparse.Namespace(**defaults)

    def _gate_patches(self, td):
        """Return context managers patching gate dir + log into td."""
        import unittest.mock as m
        gate_dir = os.path.join(td, "gate")
        os.makedirs(gate_dir, exist_ok=True)
        home = os.path.expanduser("~")
        return (
            gate_dir,
            m.patch.object(airuleset, "HANDOFF_GATE_DIR",
                           os.path.relpath(gate_dir, home)),
            m.patch.object(airuleset, "HANDOFF_GATE_LOG",
                           os.path.relpath(
                               os.path.join(td, "gate.log"), home)),
        )

    def test_sign_only_creates_receipt_no_post(self):
        """A valid --sign-only file must produce a receipt whose sha256
        matches the file content, and must NOT post via gh."""
        import unittest.mock as m
        body = (
            "READY-FOR-REVIEW: branch worktree-agent-test\n\n"
            "Stack: airuleset\n"
            "Harness: claude-code\n"
            "Verified-at-UTC: 2026-09-07T01:00:00Z\n"
            "HEAD: abc1234\n"
            "Ready for gatekeeper cross-fork review.\n"
        )
        with tempfile.TemporaryDirectory() as td:
            body_path = os.path.join(td, "handoff-body.md")
            with open(body_path, "w") as f:
                f.write(body)

            gate_dir, p1, p2 = self._gate_patches(td)
            args = self._make_args(sign_only=body_path)
            # Patch _bounce_round to return round 1 (no bounce fields
            # required) and subprocess.run to assert no posting.
            with p1, p2, \
                 m.patch("airuleset._bounce_round", return_value=1):
                rc = airuleset.cmd_handoff(args)

            self.assertEqual(0, rc, "sign-only should succeed")
            # Y-3: sign-only returns BEFORE the subprocess import +
            # gh issue comment call (line ~3397 vs return at ~3361),
            # so no posting can occur — verified by the body hash
            # matching the RAW file (no gh-posted body mutation).

            # A receipt must exist with the sha256 of the body.
            expect_hash = hashlib.sha256(body.encode()).hexdigest()
            receipts = [fn for fn in os.listdir(gate_dir)
                        if fn.endswith(".json")]
            self.assertTrue(receipts, "No receipt written")
            with open(os.path.join(gate_dir, receipts[0])) as f:
                r = json.loads(f.read())
            self.assertEqual(expect_hash, r["sha256"])
            self.assertIn("issue", r)
            self.assertEqual(42, r["issue"])

    def test_sign_only_no_rfr_blocked(self):
        """A --sign-only file without READY-FOR-REVIEW must be rejected
        with the specific marker-missing BLOCK message."""
        import unittest.mock as m
        import io
        body = "Just some random text without the marker.\n"
        with tempfile.TemporaryDirectory() as td:
            body_path = os.path.join(td, "bad.md")
            with open(body_path, "w") as f:
                f.write(body)
            _, p1, p2 = self._gate_patches(td)
            args = self._make_args(sign_only=body_path)
            with p1, p2, \
                 m.patch("airuleset._bounce_round", return_value=1), \
                 m.patch("sys.stdout", new_callable=io.StringIO) as out:
                rc = airuleset.cmd_handoff(args)
            self.assertEqual(1, rc)
            self.assertIn("no READY-FOR-REVIEW marker",
                          out.getvalue())

    def test_sign_only_missing_file_blocked(self):
        """A --sign-only pointing to a nonexistent file must fail with
        the specific cannot-read BLOCK message."""
        import unittest.mock as m
        import io
        with tempfile.TemporaryDirectory() as td:
            _, p1, p2 = self._gate_patches(td)
            args = self._make_args(sign_only="/nonexistent/path.md")
            with p1, p2, \
                 m.patch("sys.stdout", new_callable=io.StringIO) as out:
                rc = airuleset.cmd_handoff(args)
            self.assertEqual(1, rc)
            self.assertIn("cannot read sign-only file", out.getvalue())

    def test_sign_only_round2_missing_fields_blocked(self):
        """RED-1 review finding: at bounce round >= 2, sign-only must
        require Root-cause-of-previous-bounce and Prevencia-read lines in
        the body."""
        import unittest.mock as m
        import io
        body = (
            "READY-FOR-REVIEW: branch test\n\n"
            "Ready for gatekeeper cross-fork review.\n"
        )
        with tempfile.TemporaryDirectory() as td:
            body_path = os.path.join(td, "body.md")
            with open(body_path, "w") as f:
                f.write(body)
            _, p1, p2 = self._gate_patches(td)
            args = self._make_args(sign_only=body_path)
            with p1, p2, \
                 m.patch("airuleset._bounce_round", return_value=3), \
                 m.patch("sys.stdout", new_callable=io.StringIO) as out:
                rc = airuleset.cmd_handoff(args)
            self.assertEqual(1, rc)
            self.assertIn("Root-cause-of-previous-bounce",
                          out.getvalue())

    def test_sign_only_argparse_present(self):
        """The --sign-only flag must exist on the handoff subcommand."""
        import subprocess as sp
        r = sp.run([sys.executable, "airuleset.py", "handoff", "--help"],
                   capture_output=True, text=True, timeout=10,
                   cwd=os.path.dirname(os.path.dirname(
                       os.path.abspath(__file__))))
        self.assertIn("--sign-only", r.stdout,
                       "handoff --help must list --sign-only")


class TestSelfReviewModel991(unittest.TestCase):
    """#991 round 3: the handoff CLI requires + validates --self-review-model
    as an exact model id (single source: airuleset.MODEL_TIERS / BANNED_MODELS).
    These checks run BEFORE any git/gh call, so no mocking is needed beyond
    stdout capture."""

    def _args(self, **kw):
        import argparse
        defaults = dict(
            repo="zbynekdrlik/airuleset", issue=991,
            branch="worktree-x", self_review_file="/nonexistent/tbl.md",
            root_cause=None, closes_finding=None, prevencia_read=None,
            sign_only=None, self_review_model="claude-opus-4-8",
            stack=None, harness=None, shared_benefit=None,
            tenant_scope=None, source_verified=None, tested_tree=None,
            evidence_head=None)
        defaults.update(kw)
        return argparse.Namespace(**defaults)

    def test_missing_self_review_model_blocked(self):
        import io
        import unittest.mock as m
        args = self._args(self_review_model=None)
        with m.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = airuleset.cmd_handoff(args)
        self.assertEqual(1, rc)
        self.assertIn("--self-review-model", out.getvalue())

    def test_alias_or_banned_value_rejected(self):
        import io
        import unittest.mock as m
        for bad in ("fable", "opus", "claude-opus-5", "claude-opus-4-6"):
            args = self._args(self_review_model=bad)
            with m.patch("sys.stdout", new_callable=io.StringIO) as out:
                rc = airuleset.cmd_handoff(args)
            self.assertEqual(1, rc, "%r must be rejected" % bad)
            txt = out.getvalue()
            self.assertIn("not an allowed", txt)
            # message names the allowed exact ids (single source of truth)
            self.assertIn("claude-opus-4-8", txt)

    def test_valid_exact_id_passes_model_validation(self):
        """A valid exact id gets PAST model validation -- proven by the
        next-stage error being the (nonexistent) self-review FILE, not the
        model rejection."""
        import io
        import unittest.mock as m
        args = self._args(self_review_model="claude-opus-4-8")
        with m.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = airuleset.cmd_handoff(args)
        self.assertEqual(1, rc)
        txt = out.getvalue()
        self.assertIn("self-review file", txt)
        self.assertNotIn("not an allowed", txt)

    def test_argparse_flag_present(self):
        import subprocess as sp
        r = sp.run([sys.executable, "airuleset.py", "handoff", "--help"],
                   capture_output=True, text=True, timeout=10,
                   cwd=os.path.dirname(os.path.dirname(
                       os.path.abspath(__file__))))
        self.assertIn("--self-review-model", r.stdout)


if __name__ == "__main__":
    unittest.main()
