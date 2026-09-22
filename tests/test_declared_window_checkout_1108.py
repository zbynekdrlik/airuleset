"""#1108: a DECLARED managed window's checkout is provisioned by the install
(clone when absent, never touch an existing one) and a window is NEVER opened
into a missing cwd (a loud line instead of a `$HOME` fallback session).

Owner incident 21.9.2026: the v0.1.375 install created gk's declared
`gk-quality` window with `-c "$HOME/devel/odoo/odoo-erp-quality"` while that
checkout did not exist; tmux fell back to `$HOME`, a Claude started in
`/home/gatekeeper` with no repo / CLAUDE.md / role, and the owner talked to a
role-less session ("dalsia tvoja blbost - to uz je akoze hotove?"). The
supervisor cloned the checkout by hand.

Fix (Approach 1): a declared window MAY carry `repo`/`branch`;
`cli_fleet.validate_windows` accepts the safe shapes and rejects a bad `repo`
(`owner/name` only) / an unsafe `branch`. A new
`cli_tmux_provisioning.ensure_declared_checkouts(windows, home, run)` clones an
absent checkout at install (present -> untouched, failure -> a LOUD non-fatal
line). The two window-command renderers guard the `new-window` with
`[ ! -d <cwd> ]` so a missing cwd becomes a `>&2` line, never a `$HOME`
session. `airuleset.py status` prints one `present|MISSING` line per declared
window.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_fleet  # noqa: E402
import cli_tmux_provisioning as ctp  # noqa: E402


# The exact gk 3-window declaration, non-primary windows carrying repo+branch.
_GK3 = [
    {"name": "gk", "cwd": "~/devel/odoo/odoo-erp",
     "role": "review", "mode": "parallel"},
    {"name": "gk-infra", "cwd": "~/devel/odoo/odoo-erp-infra",
     "repo": "zbynekdrlik/odoo-erp", "branch": "develop",
     "role": "infra", "mode": "sequential"},
    {"name": "gk-quality", "cwd": "~/devel/odoo/odoo-erp-quality",
     "repo": "zbynekdrlik/odoo-erp", "branch": "develop",
     "role": "quality", "mode": "sequential"},
]

_MARKER = {"base_url": "http://gw", "key_file": "~/.secrets/k",
           "main": "m", "sub": "s", "fast": "f",
           "cwd": "/home/miva1/devel/odoo/odoo-erp"}


class _CP:
    """Minimal CompletedProcess stand-in for a fake runner."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeRun:
    """A fake `run(argv, cwd=None)` runner that records every call and answers
    a git clone with a configurable return code / stderr."""

    def __init__(self, clone_rc=0, clone_stderr=""):
        self.calls = []            # list of (argv, cwd)
        self.clone_rc = clone_rc
        self.clone_stderr = clone_stderr

    def __call__(self, argv, cwd=None):
        self.calls.append((list(argv), cwd))
        if argv[:2] == ["git", "clone"]:
            return _CP(returncode=self.clone_rc, stderr=self.clone_stderr)
        return _CP(returncode=0)


# --------------------------------------------------------------------------- #
# validate_windows accepts repo/branch and rejects unsafe shapes.
# --------------------------------------------------------------------------- #
class TestValidateWindowsRepoBranch(unittest.TestCase):
    def test_accepts_safe_repo_and_branch(self):
        w = [{"name": "x", "cwd": "a", "repo": "owner/name",
              "branch": "develop"}]
        self.assertEqual(cli_fleet.validate_windows(w), [])

    def test_accepts_feature_branch_with_slash(self):
        w = [{"name": "x", "cwd": "a", "repo": "owner.name_1/repo-2",
              "branch": "feature/foo.bar_1"}]
        self.assertEqual(cli_fleet.validate_windows(w), [])

    def test_rejects_bad_repo(self):
        for bad in ("notowner", "a/b/c", "owner/", "/name",
                    "own er/name", "owner/na me", "owner/name;rm"):
            errs = cli_fleet.validate_windows(
                [{"name": "x", "cwd": "a", "repo": bad}])
            self.assertTrue(any("repo" in e for e in errs), (bad, errs))

    def test_rejects_unsafe_branch(self):
        for bad in ("-rf", "../evil", "a b", "foo;bar", "/leading",
                    "trailing/", "br\nanch"):
            errs = cli_fleet.validate_windows(
                [{"name": "x", "cwd": "a", "branch": bad}])
            self.assertTrue(any("branch" in e for e in errs), (bad, errs))

    def test_absent_repo_branch_add_no_error(self):
        # the #998 `test_validate_rejects_bad_shapes` lock: EXACTLY 4 errors for
        # a repo/branch-less window (bad name, absolute cwd, bad role, bad mode).
        # Optional repo/branch must add ZERO errors when absent.
        errs = cli_fleet.validate_windows(
            [{"name": "a b", "cwd": "/abs", "role": "boss", "mode": "q"}])
        self.assertEqual(len(errs), 4, errs)


# --------------------------------------------------------------------------- #
# gk declares repo+branch on its two non-primary windows.
# --------------------------------------------------------------------------- #
class TestGkDeclaresRepoBranch(unittest.TestCase):
    def _gk(self):
        return cli_fleet.box_windows("gatekeeper")

    def test_infra_and_quality_carry_repo_and_branch(self):
        gk = self._gk()
        infra = [w for w in gk if w["name"] == "gk-infra"][0]
        quality = [w for w in gk if w["name"] == "gk-quality"][0]
        self.assertEqual(infra["repo"], "zbynekdrlik/odoo-erp")
        self.assertEqual(infra["branch"], "develop")
        self.assertEqual(quality["repo"], "zbynekdrlik/odoo-erp")
        self.assertEqual(quality["branch"], "develop")

    def test_primary_review_window_has_no_repo(self):
        # the FLOW primary window is the box's own existing checkout — untouched.
        gk = self._gk()
        primary = [w for w in gk if w["name"] == "gk"][0]
        self.assertIsNone(primary.get("repo"))

    def test_gk_declaration_still_validates(self):
        self.assertEqual(cli_fleet.validate_windows(self._gk()), [])


# --------------------------------------------------------------------------- #
# ensure_declared_checkouts — clone absent, leave present, loud on failure.
# --------------------------------------------------------------------------- #
class TestEnsureDeclaredCheckouts(unittest.TestCase):
    def test_absent_cwd_clones_then_sets_default(self):
        home = tempfile.mkdtemp()          # the declared cwd under it is absent
        fake = _FakeRun()
        lines = ctp.ensure_declared_checkouts(_GK3, home=home, run=fake)
        target_infra = os.path.join(home, "devel/odoo/odoo-erp-infra")
        target_quality = os.path.join(home, "devel/odoo/odoo-erp-quality")
        # the primary review window (no repo) is skipped; the two repo windows
        # clone then set-default — exactly the argv the design specifies.
        clones = [c for c in fake.calls if c[0][:2] == ["git", "clone"]]
        setdefaults = [c for c in fake.calls
                       if c[0][:3] == ["gh", "repo", "set-default"]]
        self.assertEqual(len(clones), 2, fake.calls)
        self.assertEqual(len(setdefaults), 2, fake.calls)
        self.assertEqual(
            clones[0][0],
            ["git", "clone", "-q", "-o", "origin", "-b", "develop",
             "https://github.com/zbynekdrlik/odoo-erp.git", target_infra])
        self.assertEqual(
            clones[1][0],
            ["git", "clone", "-q", "-o", "origin", "-b", "develop",
             "https://github.com/zbynekdrlik/odoo-erp.git", target_quality])
        # gh repo set-default runs INSIDE the clone (cwd asserted).
        self.assertEqual(setdefaults[0][0],
                         ["gh", "repo", "set-default", "zbynekdrlik/odoo-erp"])
        self.assertEqual(setdefaults[0][1], target_infra)
        self.assertEqual(setdefaults[1][1], target_quality)
        self.assertEqual(lines, [])        # no loud line on success

    def test_clone_ordering_is_clone_then_setdefault(self):
        home = tempfile.mkdtemp()
        fake = _FakeRun()
        ctp.ensure_declared_checkouts(
            [_GK3[2]], home=home, run=fake)      # only gk-quality
        self.assertEqual(len(fake.calls), 2, fake.calls)
        self.assertEqual(fake.calls[0][0][:2], ["git", "clone"])
        self.assertEqual(fake.calls[1][0][:3], ["gh", "repo", "set-default"])

    def test_present_cwd_is_untouched(self):
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, "devel/odoo/odoo-erp-quality"))
        fake = _FakeRun()
        lines = ctp.ensure_declared_checkouts(
            [_GK3[2]], home=home, run=fake)      # only gk-quality, present
        self.assertEqual(fake.calls, [])         # ZERO runner calls
        self.assertEqual(lines, [])

    def test_clone_failure_is_loud_and_nonfatal(self):
        home = tempfile.mkdtemp()
        fake = _FakeRun(clone_rc=1,
                        clone_stderr="fatal: could not read from remote\n")
        lines = ctp.ensure_declared_checkouts(
            [_GK3[2]], home=home, run=fake)
        # clone attempted, set-default NOT called after a failed clone.
        clones = [c for c in fake.calls if c[0][:2] == ["git", "clone"]]
        setdefaults = [c for c in fake.calls
                       if c[0][:3] == ["gh", "repo", "set-default"]]
        self.assertEqual(len(clones), 1)
        self.assertEqual(len(setdefaults), 0)
        # a LOUD line naming the window + declared cwd, returned non-fatally.
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("gk-quality", lines[0])
        self.assertIn("could not be cloned", lines[0])
        self.assertIn("~/devel/odoo/odoo-erp-quality", lines[0])

    def test_window_without_repo_is_skipped(self):
        home = tempfile.mkdtemp()
        fake = _FakeRun()
        lines = ctp.ensure_declared_checkouts(
            [{"name": "w", "cwd": "checkout"}], home=home, run=fake)
        self.assertEqual(fake.calls, [])
        self.assertEqual(lines, [])

    def test_clone_without_branch_omits_b_flag(self):
        home = tempfile.mkdtemp()
        fake = _FakeRun()
        ctp.ensure_declared_checkouts(
            [{"name": "w", "cwd": "checkout", "repo": "owner/name"}],
            home=home, run=fake)
        target = os.path.join(home, "checkout")
        clones = [c for c in fake.calls if c[0][:2] == ["git", "clone"]]
        self.assertEqual(
            clones[0][0],
            ["git", "clone", "-q", "-o", "origin",
             "https://github.com/owner/name.git", target])

    def test_empty_windows_is_a_noop(self):
        fake = _FakeRun()
        self.assertEqual(
            ctp.ensure_declared_checkouts([], home="/tmp", run=fake), [])
        self.assertEqual(fake.calls, [])


# --------------------------------------------------------------------------- #
# Renderer guard: a window is NEVER opened into a missing cwd.
# --------------------------------------------------------------------------- #
class TestManagedBodyGuardsMissingCwd(unittest.TestCase):
    def setUp(self):
        self.body = ctp._managed_windows_create_body(_GK3)

    def test_guard_present_for_every_non_primary_window(self):
        # one `[ ! -d ]` guard per non-primary declared window, BEFORE new-window.
        self.assertEqual(self.body.count("[ ! -d "), 2, self.body)
        self.assertIn('[ ! -d "$HOME/devel/odoo/odoo-erp-infra" ]', self.body)
        self.assertIn('[ ! -d "$HOME/devel/odoo/odoo-erp-quality" ]', self.body)

    def test_guard_writes_a_loud_line_to_stderr(self):
        self.assertEqual(self.body.count(">&2"), 2, self.body)
        self.assertIn("gk-infra", self.body)
        self.assertIn("gk-quality", self.body)

    def test_guard_comes_before_new_window_in_each_block(self):
        # the guard's echo must precede the block's own new-window.
        for name, tail in (("gk-infra", "odoo-erp-infra"),
                           ("gk-quality", "odoo-erp-quality")):
            guard_at = self.body.index('[ ! -d "$HOME/devel/odoo/%s" ]' % tail)
            nw_at = self.body.index('new-window -d -t "$S" -n %s' % name)
            self.assertLess(guard_at, nw_at, (name, self.body))

    def test_new_window_count_unchanged(self):
        # the guard adds NO new-window (still exactly 2 — the #1037 lock).
        self.assertEqual(self.body.count("new-window"), 2)

    def test_no_single_quote_in_body(self):
        # the create body lives inside the hook's single-quoted run-shell body.
        self.assertNotIn("'", self.body)

    def test_body_is_valid_shell(self):
        d = tempfile.mkdtemp()
        script = Path(d) / "snip.sh"
        script.write_text("S=x\n" + self.body + "\n")
        r = subprocess.run(["bash", "-n", str(script)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestImplSnippetGuardsMissingCwd(unittest.TestCase):
    def setUp(self):
        self.snip = ctp._impl_window_create_snippet(_MARKER)

    def test_guard_present(self):
        self.assertIn("[ ! -d ", self.snip)
        self.assertIn('/home/miva1/devel/odoo/odoo-erp', self.snip)

    def test_guard_writes_to_stderr(self):
        self.assertIn(">&2", self.snip)

    def test_no_single_quote(self):
        self.assertNotIn("'", self.snip)

    def test_still_creates_impl_window(self):
        # #1037 / #1060 locks kept: the dedup, remain-on-exit and marker gate.
        self.assertIn('new-window -d -t "$S" -n impl', self.snip)
        self.assertIn("grep -Fxq impl", self.snip)
        self.assertIn("remain-on-exit on", self.snip)


class TestRenderedHookLineValid(unittest.TestCase):
    def test_hook_line_bash_n_ok(self):
        line = ctp._render_session_created_hook_line("gk", _GK3)
        d = tempfile.mkdtemp()
        script = Path(d) / "hook.sh"
        script.write_text(line)
        r = subprocess.run(["bash", "-n", str(script)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


# --------------------------------------------------------------------------- #
# status surface: one present|MISSING line per declared window.
# --------------------------------------------------------------------------- #
class TestDeclaredWindowStatusLines(unittest.TestCase):
    def test_present_and_missing_lines(self):
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, "devel/odoo/odoo-erp"))
        os.makedirs(os.path.join(home, "devel/odoo/odoo-erp-infra"))
        # gk-quality intentionally absent -> MISSING.
        lines = ctp.declared_window_status_lines(_GK3, home=home)
        self.assertEqual(len(lines), 3, lines)
        self.assertEqual(
            lines[0],
            "window gk: cwd ~/devel/odoo/odoo-erp (present) "
            "role=review mode=parallel")
        self.assertTrue(any("gk-infra" in l and "(present)" in l
                            for l in lines), lines)
        quality = [l for l in lines if "gk-quality" in l][0]
        self.assertIn("(MISSING)", quality)
        self.assertIn("role=quality", quality)
        self.assertIn("mode=sequential", quality)
        self.assertIn("~/devel/odoo/odoo-erp-quality", quality)

    def test_empty_windows_is_empty(self):
        self.assertEqual(ctp.declared_window_status_lines([], home="/tmp"), [])


if __name__ == "__main__":
    unittest.main()
