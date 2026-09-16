"""#1053 — `airuleset.py labels --ensure` idempotently creates the gk
state-machine labels (gk-processing / verify-on-copy) on a repo, check-then-
create (never --force), driven by a fake gh (no network)."""
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_labels  # noqa: E402


class FakeGh:
    """A fake gh that tracks which labels 'exist' and records create calls."""

    def __init__(self, existing=(), list_rc=0, create_rc=0):
        self.existing = set(existing)
        self.list_rc = list_rc
        self.create_rc = create_rc
        self.create_calls = []
        self.list_calls = []

    def __call__(self, argv):
        if argv[:2] == ["gh", "label"] and argv[2] == "list":
            name = argv[argv.index("--search") + 1]
            self.list_calls.append(name)
            out = name + "\n" if name in self.existing else ""
            return types.SimpleNamespace(returncode=self.list_rc, stdout=out,
                                         stderr="")
        if argv[:2] == ["gh", "label"] and argv[2] == "create":
            name = argv[3]
            self.create_calls.append(name)
            if self.create_rc == 0:
                self.existing.add(name)
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            return types.SimpleNamespace(returncode=self.create_rc, stdout="",
                                         stderr="denied")
        raise AssertionError("unexpected gh call: %r" % (argv,))


REPO_NAME = "zbynekdrlik/odoo-erp"


class LabelsEnsure1053(unittest.TestCase):
    def test_creates_missing_labels(self):
        gh = FakeGh(existing=())
        result = cli_labels.ensure_labels(REPO_NAME, gh_run=gh)
        self.assertEqual(result["gk-processing"], "created")
        self.assertEqual(result["verify-on-copy"], "created")
        self.assertCountEqual(gh.create_calls,
                              ["gk-processing", "verify-on-copy"])

    def test_idempotent_second_run_creates_nothing(self):
        gh = FakeGh(existing=())
        cli_labels.ensure_labels(REPO_NAME, gh_run=gh)
        gh.create_calls.clear()
        result = cli_labels.ensure_labels(REPO_NAME, gh_run=gh)
        self.assertEqual(result["gk-processing"], "exists")
        self.assertEqual(result["verify-on-copy"], "exists")
        self.assertEqual(gh.create_calls, [],
                         "a second run must create nothing (idempotent)")

    def test_never_uses_force(self):
        """Existing labels are never touched — no --force overwrite (#191 M1)."""
        gh = FakeGh(existing=("gk-processing", "verify-on-copy"))
        cli_labels.ensure_labels(REPO_NAME, gh_run=gh)
        self.assertEqual(gh.create_calls, [])

    def test_partial_existing_creates_only_missing(self):
        gh = FakeGh(existing=("gk-processing",))
        result = cli_labels.ensure_labels(REPO_NAME, gh_run=gh)
        self.assertEqual(result["gk-processing"], "exists")
        self.assertEqual(result["verify-on-copy"], "created")
        self.assertEqual(gh.create_calls, ["verify-on-copy"])

    def test_create_failure_reported(self):
        gh = FakeGh(existing=(), create_rc=1)
        result = cli_labels.ensure_labels(REPO_NAME, gh_run=gh)
        self.assertTrue(result["gk-processing"].startswith("failed"))
        self.assertTrue(result["verify-on-copy"].startswith("failed"))

    def test_list_read_failure_does_not_blind_create(self):
        """A gh error on the existence read must NOT blindly create — it fails
        loud (safe direction: never overwrite/duplicate on a broken read)."""
        gh = FakeGh(existing=(), list_rc=1)
        result = cli_labels.ensure_labels(REPO_NAME, gh_run=gh)
        self.assertTrue(result["gk-processing"].startswith("failed"))
        self.assertEqual(gh.create_calls, [],
                         "a failed existence read must not create")

    def test_repo_flags_passed(self):
        """The repo is passed via -R so it targets the named repo, not cwd."""
        seen = []

        def gh(argv):
            seen.append(argv)
            if argv[2] == "list":
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        cli_labels.ensure_labels(REPO_NAME, gh_run=gh)
        self.assertTrue(all("-R" in a and REPO_NAME in a for a in seen),
                        "every gh call must carry -R <repo>")

    def test_label_specs(self):
        """The two #1053 labels are the ones under management."""
        self.assertEqual(set(cli_labels.LABELS_1053),
                         {"gk-processing", "verify-on-copy"})


class CmdLabels1053(unittest.TestCase):
    def test_cmd_without_ensure_is_noop(self):
        rc = cli_labels.cmd_labels(types.SimpleNamespace(ensure=False,
                                                         repo=REPO_NAME))
        self.assertEqual(rc, 0)

    def test_cmd_ensure_wired(self):
        import unittest.mock as m
        gh = FakeGh(existing=())
        with m.patch.object(cli_labels, "_default_gh_run", side_effect=gh):
            rc = cli_labels.cmd_labels(
                types.SimpleNamespace(ensure=True, repo=REPO_NAME))
        self.assertEqual(rc, 0)
        self.assertCountEqual(gh.create_calls,
                              ["gk-processing", "verify-on-copy"])


if __name__ == "__main__":
    unittest.main()
