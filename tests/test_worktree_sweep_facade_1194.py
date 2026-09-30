"""#1194 — `cli_worktree_sweep` stays a complete facade after the reclaimer split.

The module was split (#1194) from one 2002-line file into stdlib-only
leaves, one per reclaimer. Every caller keeps importing through
`cli_worktree_sweep`:
- `airuleset.py`'s re-export block;
- `watchdog/disk_guard.py`'s deferred `from cli_worktree_sweep import
  discover_reclaimable_worktrees`;
- `watchdog/lane_reconcile.py`'s `ws._worktree_is_clean` /
  `ws._worktree_in_live_use`;
- every test suite's `cli_worktree_sweep.X`.

`PRE_SPLIT_SURFACE` below is a FROZEN snapshot of every top-level name the
pre-split module defined, taken by AST from origin/main a62ab26c. It is
deliberately a literal list, never re-derived from the live module: a name
silently dropped from the facade must fail here, not quietly shrink the
expectation.
"""

import ast
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_worktree_sweep  # noqa: E402

PRE_SPLIT_SURFACE = (
    "CLAUDE_DIR", "STALE_WORKTREE_LOG_PATH", "STALE_WORKTREE_STATE_PATH",
    "STALE_WORKTREE_MIN_INTERVAL_S", "STALE_WORKTREE_REMOVE_TIMEOUT_S",
    "_STALE_WORKTREE_PROTECTED_BRANCHES", "STALE_ORPHAN_BRANCH_MIN_AGE_S",
    "STALE_LOCKED_DEAD_MIN_AGE_S", "STALE_WORKTREE_IDLE_MIN_AGE_S",
    "_worktree_env_age_s", "_worktree_git", "_worktree_porcelain_entries",
    "_worktree_sweep_base_branch", "_WORKTREE_LOCK_PID_RX", "_worktree_lock_pid",
    "_proc_stat_text", "_pid_is_dead", "_worktree_admin_dir",
    "_worktree_lock_age_s", "_worktree_is_clean", "_worktree_recency_age_s",
    "_worktree_in_live_use", "_classify_locked_worktree",
    "_worktree_branch_ref_age_s", "discover_orphaned_worktree_branches",
    "discover_stale_worktrees", "discover_salvage_worktrees",
    "_PRECIOUS_IGNORED_PATTERNS", "_PRECIOUS_PATHSPECS", "_fs_walk_has_precious",
    "_worktree_has_precious_ignored", "_is_orphan_gitdir",
    "_worktree_head_reachable_from_origin", "_worktree_reclaimable",
    "discover_reclaimable_worktrees", "_log_stale_worktree_results",
    "sweep_stale_worktrees", "cmd_sweep_worktrees", "LANE_TARGET_LOG_PATH",
    "LANE_TARGET_STATE_PATH", "LANE_TARGET_MIN_INTERVAL_S",
    "LANE_TARGET_MERGED_MIN_IDLE_S", "TIER0_FLIP_ISO", "_tier0_flip_epoch",
    "LANE_TARGET_TIER0_BYPASS_STATE_PATH", "LANE_TARGET_TIER0_BYPASS_REFILE_S",
    "LANE_TARGET_TIER0_BYPASS_TITLE", "_lane_human_size",
    "_branch_reflog_has_authored_commit", "_iter_lane_target_dirs",
    "_log_lane_target_results", "_lane_repo_slug", "_sample_fresh_target_files",
    "_tier0_bypass_issue_body", "_default_tier0_bypass_filer",
    "_record_tier0_bypass", "purge_merged_lane_targets", "cmd_purge_lane_targets",
)


class TestFacadeSurface(unittest.TestCase):

    def test_snapshot_has_every_pre_split_name_once(self):
        self.assertEqual(len(PRE_SPLIT_SURFACE), 58)
        self.assertEqual(len(set(PRE_SPLIT_SURFACE)), 58)

    def test_every_pre_split_name_imports_from_the_facade(self):
        missing = [n for n in PRE_SPLIT_SURFACE if not hasattr(cli_worktree_sweep, n)]
        self.assertEqual(missing, [], "names dropped from the cli_worktree_sweep facade")
        for name in PRE_SPLIT_SURFACE:
            with self.subTest(name=name):
                ns = {}
                exec(f"from cli_worktree_sweep import {name}", ns)  # the caller's import shape
                self.assertIs(ns[name], getattr(cli_worktree_sweep, name))

    def test_airuleset_reexports_are_the_facade_objects(self):
        airuleset = importlib.import_module("airuleset")
        # airuleset.py defines its OWN `CLAUDE_DIR` (equal value, a separate
        # object); every other shared name comes from its re-export block (38).
        shared = [n for n in PRE_SPLIT_SURFACE
                  if n != "CLAUDE_DIR" and hasattr(airuleset, n)]
        self.assertEqual(len(shared), 38)
        for name in shared:
            with self.subTest(name=name):
                self.assertIs(getattr(airuleset, name), getattr(cli_worktree_sweep, name))


LEAVES = ("cli_worktree_common", "cli_worktree_stale", "cli_worktree_orphans",
          "cli_worktree_reclaim", "cli_lane_target_reclaim")
REPO = Path(__file__).resolve().parent.parent


def _module_level_imports(path):
    """Top-level (module-body) imported module names of a source file."""
    tree = ast.parse(path.read_text())
    out = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module or "").split(".")[0])
    return out


class TestLeafStructure(unittest.TestCase):

    def test_every_facade_name_is_the_defining_leafs_object(self):
        owners = {}
        for leaf in LEAVES:
            mod = importlib.import_module(leaf)
            for node in ast.parse((REPO / f"{leaf}.py").read_text()).body:
                names = []
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    names = [node.name]
                elif isinstance(node, ast.Assign):
                    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                for n in names:
                    self.assertNotIn(n, owners, f"{n} defined in two leaves")
                    owners[n] = mod
        self.assertEqual(sorted(owners), sorted(PRE_SPLIT_SURFACE))
        for name, mod in owners.items():
            with self.subTest(name=name):
                self.assertIs(getattr(cli_worktree_sweep, name), getattr(mod, name))

    def test_leaves_are_stdlib_only_at_module_level_and_never_import_the_facade(self):
        allowed = {"json", "os", "re", "shutil", "sys", "pathlib", *LEAVES}
        for leaf in LEAVES + ("cli_worktree_sweep",):
            with self.subTest(leaf=leaf):
                imports = _module_level_imports(REPO / f"{leaf}.py")
                self.assertNotIn("cli_worktree_sweep", imports)
                self.assertEqual(sorted(set(imports) - allowed), [])

    def test_facade_defines_nothing_itself(self):
        body = ast.parse((REPO / "cli_worktree_sweep.py").read_text()).body
        kinds = {type(n).__name__ for n in body}
        self.assertLessEqual(kinds, {"Expr", "ImportFrom"})


class TestNoFacadePatchSeams(unittest.TestCase):
    """The facade owns no code, so a patch on it cannot intercept a call made
    inside a leaf (#1194 split: seven tests went silent that way, one of them
    running the REAL sweep). Tests must patch the leaf that makes the call."""

    def test_no_test_patches_an_attribute_of_the_facade(self):
        offenders = []
        for path in sorted((REPO / "tests").glob("test_*.py")):
            tree = ast.parse(path.read_text())
            aliases = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    aliases |= {a.asname or a.name for a in node.names
                                if a.name == "cli_worktree_sweep"}
                elif (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                        and getattr(node.value.func, "attr", "") == "import_module"
                        and node.value.args
                        and isinstance(node.value.args[0], ast.Constant)
                        and node.value.args[0].value == "cli_worktree_sweep"):
                    aliases |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            def _is_alias(n):
                return isinstance(n, ast.Name) and n.id in aliases

            def _facade_string(n):
                if isinstance(n, ast.Constant) and isinstance(n.value, str):
                    return n.value.startswith("cli_worktree_sweep.")
                if isinstance(n, ast.JoinedStr) and n.values:
                    head = n.values[0]
                    return (isinstance(head, ast.Constant)
                            and str(head.value).startswith("cli_worktree_sweep."))
                return False

            for node in ast.walk(tree):
                # `ws.X = ...` / `ws.X += ...` rebinds a facade attribute directly.
                if isinstance(node, (ast.Assign, ast.AugAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(isinstance(t, ast.Attribute) and _is_alias(t.value) for t in targets):
                        offenders.append(f"{path.name}:{node.lineno}")
                    continue
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                kw = {k.arg: k.value for k in node.keywords if k.arg}
                first = node.args[0] if node.args else kw.get("target")
                if first is None:
                    continue
                if fname in ("object", "setattr", "multiple") and _is_alias(first):
                    offenders.append(f"{path.name}:{node.lineno}")
                elif fname in ("patch", "setattr") and _facade_string(first):
                    offenders.append(f"{path.name}:{node.lineno}")
                elif (fname == "dict" and isinstance(first, ast.Attribute)
                        and first.attr == "__dict__" and _is_alias(first.value)):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [], "patch the defining leaf, not the facade")


if __name__ == "__main__":
    unittest.main()
