"""#1333 ROZHODNUTÉ 1-2 — each registry project's stack is recorded centrally,
and a checkout resolves to it.

``projects-registry.json`` rows carry ``"stack"`` (``house`` or a platform id
from ``cli_stack_scope.STACK_IDS``), set by airuleset only.
``cli_stack_scope.stack_for`` maps a directory to ``(project, stack)`` through
its git root and ``origin`` remote (a linked worktree reads the main repo's
config), falling back to the row whose path is the git root; an unknown
directory, a row without a stack and any read failure read ``house``.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_onboard as ob  # noqa: E402
import cli_stack_scope as scope  # noqa: E402

sys.path.insert(0, str(REPO / "tests"))
from _stack_testlib import git_repo, git_worktree  # noqa: E402

REGISTRY = REPO / "projects-registry.json"


def _repo(root, url=None):
    return git_repo(root, url, commit=True)


class TestRegistryStacks(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads(REGISTRY.read_text(encoding="utf-8"))

    def test_every_row_has_a_known_stack(self):
        bad = [(r["name"], r.get("stack")) for r in self.rows
               if r.get("stack") not in scope.STACK_IDS]
        self.assertEqual(bad, [])

    def test_the_decided_rows(self):
        by = {r["name"]: r["stack"] for r in self.rows}
        for name, stack in (("odoo-erp", "odoo"), ("odoo-slovnormal", "odoo"),
                            ("fohmixer", "house"), ("wall-bridge", "house"),
                            ("songplayer", "house"), ("wall-songs", "house"),
                            ("montalu-n8n", "n8n"), ("mikrotik-pz", "config")):
            self.assertEqual(by[name], stack, name)

    def test_the_platform_ids_of_the_decision_exist(self):
        for sid in ("house", "odoo", "n8n", "python-tooling", "config", "cuda",
                    "mobile-rn"):
            self.assertIn(sid, scope.STACK_IDS)

    def test_airuleset_itself_is_python_tooling_without_a_row(self):
        self.assertNotIn("airuleset", [r["name"] for r in self.rows])
        self.assertEqual(scope.UNREGISTERED_STACKS["airuleset"], "python-tooling")
        self.assertEqual(scope.stack_for(str(REPO), str(REGISTRY)),
                         ("airuleset", "python-tooling"))


class TestStackFor(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def _registry(self, rows):
        p = self.tmp / "registry.json"
        p.write_text(json.dumps(rows))
        return str(p)

    def test_ssh_and_https_origins_resolve_to_the_row(self):
        for url, want in (("git@github.com:zbynekdrlik/odoo-erp.git", ("odoo-erp", "odoo")),
                          ("https://github.com/zbynekdrlik/fohmixer", ("fohmixer", "house"))):
            with self.subTest(url=url):
                root = _repo(self.tmp / url.rsplit("/", 1)[-1], url)
                self.assertEqual(scope.stack_for(str(root / "web" / "new"), str(REGISTRY)),
                                 want)

    def test_linked_worktree_reads_the_main_repo_config(self):
        main = _repo(self.tmp / "main", "git@github.com:zbynekdrlik/odoo-erp.git")
        wt = git_worktree(main, self.tmp / "elsewhere" / "wt")
        self.assertEqual(scope.stack_for(str(wt / "x"), str(REGISTRY)), ("odoo-erp", "odoo"))

    # --- round 3 (I): worktrees via the main checkout, non-git by prefix --- #
    def test_worktree_of_a_row_whose_origin_name_differs_resolves_by_main_path(self):
        main = _repo(self.tmp / "vyuctovanie", "git@github.com:o/vyuctovanie.git")
        reg = self._registry([{"name": "montalu-vyuctovanie", "path": str(main),
                               "stack": "python-tooling"}])
        wt = git_worktree(main, main / ".claude" / "worktrees" / "agent-1")
        self.assertEqual(scope.stack_for(str(wt / "src"), reg),
                         ("montalu-vyuctovanie", "python-tooling"))

    def test_a_non_git_registry_path_resolves_by_prefix(self):
        reg = self._registry([{"name": "mikrotik", "path": str(self.tmp / "mikrotik"),
                               "stack": "config"},
                              {"name": "deeper", "path": str(self.tmp / "mikrotik" / "pz"),
                               "stack": "n8n"}])
        (self.tmp / "mikrotik" / "pz" / "x").mkdir(parents=True)
        self.assertEqual(scope.stack_for(str(self.tmp / "mikrotik" / "a" / "b.rsc"), reg),
                         ("mikrotik", "config"))
        self.assertEqual(scope.stack_for(str(self.tmp / "mikrotik" / "pz" / "x"), reg),
                         ("deeper", "n8n"))

    # --- round 3 (B): a path outside any repo resolves through the cwd ----- #
    def test_outside_any_repo_falls_back_to_the_session_cwd(self):
        odoo = _repo(self.tmp / "odoo", "git@github.com:zbynekdrlik/odoo-erp.git")
        outside = self.tmp / "scratch" / "x"
        self.assertEqual(scope.stack_for(str(outside), str(REGISTRY), fallback=str(odoo)),
                         ("odoo-erp", "odoo"))
        self.assertEqual(scope.stack_for(str(outside), str(REGISTRY),
                                         fallback=str(self.tmp / "nowhere")),
                         (None, "house"))

    def test_inside_a_repo_the_fallback_is_ignored(self):
        odoo = _repo(self.tmp / "odoo", "git@github.com:zbynekdrlik/odoo-erp.git")
        foh = _repo(self.tmp / "foh", "https://github.com/zbynekdrlik/fohmixer")
        self.assertEqual(scope.stack_for(str(foh / "ui"), str(REGISTRY), fallback=str(odoo)),
                         ("fohmixer", "house"))
        self.assertEqual(scope.repo_root(str(foh / "ui" / "new")), str(foh.resolve()))
        self.assertIsNone(scope.repo_root(str(self.tmp / "scratch")))

    def test_unknown_directory_and_unknown_origin_are_house(self):
        self.assertEqual(scope.stack_for(str(self.tmp / "nothing"), str(REGISTRY)),
                         (None, "house"))
        root = _repo(self.tmp / "brand-new", "git@github.com:zbynekdrlik/brand-new.git")
        self.assertEqual(scope.stack_for(str(root), str(REGISTRY)), (None, "house"))

    def test_row_without_a_stack_reads_house(self):
        reg = self._registry([{"name": "x", "path": "/nowhere"}])
        root = _repo(self.tmp / "x", "git@github.com:o/x.git")
        self.assertEqual(scope.stack_for(str(root), reg), ("x", "house"))

    def test_github_repo_field_and_path_match(self):
        reg = self._registry([
            {"name": "obsytplayer", "github_repo": "o/obs-yt-player", "stack": "obs"},
            {"name": "local", "path": str(self.tmp / "local"), "stack": "config"}])
        a = _repo(self.tmp / "a", "git@github.com:o/obs-yt-player.git")
        self.assertEqual(scope.stack_for(str(a), reg), ("obsytplayer", "obs"))
        b = _repo(self.tmp / "local")  # no origin at all: the main path decides
        self.assertEqual(scope.stack_for(str(b / "sub"), reg), ("local", "config"))

    def test_broken_registry_and_unreadable_config_read_house(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{nope")
        root = _repo(self.tmp / "o", "git@github.com:zbynekdrlik/odoo-erp.git")
        self.assertEqual(scope.stack_for(str(root), str(bad)), (None, "house"))
        (root / ".git" / "config").unlink()
        (root / ".git" / "config").mkdir()      # unreadable even as root
        self.assertEqual(scope.stack_for(str(root), str(REGISTRY)), (None, "house"))


class TestRegistryEntryKeepsTheStack(unittest.TestCase):
    def test_reonboard_keeps_an_existing_stack_and_a_new_row_has_none(self):
        with tempfile.TemporaryDirectory() as d:
            existing = {"name": "x", "stack": "odoo", "host": "dev1"}
            kept = ob.build_registry_entry(d, d, None, "x", [], existing)
            fresh = ob.build_registry_entry(d, d, None, "x", [], None)
        self.assertEqual(kept["stack"], "odoo")
        self.assertIsNone(fresh["stack"])


if __name__ == "__main__":
    unittest.main()
