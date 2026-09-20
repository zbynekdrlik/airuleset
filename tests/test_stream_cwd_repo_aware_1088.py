"""#1088 — stream session start directory must be a git WORK TREE; footer says
`no-repo`.

montalu1's checkout is named `odoo-slovnormal` (not `odoo-erp`), and its
`~/devel/odoo` is a PLAIN folder (screenshots + the checkout). The old chain
`("devel/odoo/odoo-erp", "devel/odoo")` accepted the first EXISTING dir with no
git check, so a montalu1 session started in the non-repo parent `~/devel/odoo`
(wrong project key -> no history/memory, and the footer rendered nothing because
the "gh error renders nothing" branch also hid "no repo here").

Approach 1 (chosen): a repo-aware chain — each candidate accepted ONLY if it is
a git work tree (`<dir>/.git`, work tree or gitfile) — shared by the ssh attach
block (bash) and the tmux bootstrap (Python `_stream_session_cwd`), with a LOUD
last-resort fallback; and a `no-repo` footer marker driven by a
`reason: "no-repo"` cache field the refresh writes for a non-repo cwd.

These tests are RED against the pre-#1088 code.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
import unittest.mock as m
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import airuleset            # noqa: E402
import cli_bashrc_appliers  # noqa: E402
import statusbar            # noqa: E402


# --------------------------------------------------------------------------- #
# 1. resolve_stream_cwd / _stream_session_cwd — the repo-aware chain (Python).
# --------------------------------------------------------------------------- #
class TestResolveStreamCwd(unittest.TestCase):
    def _home(self):
        d = Path(tempfile.mkdtemp(prefix="airuleset-1088-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        return d

    def test_picks_odoo_slovnormal_when_odoo_erp_absent(self):
        # montalu1's real layout: no odoo-erp, odoo-slovnormal IS a work tree,
        # ~/devel/odoo is a plain folder. The checkout must win.
        d = self._home()
        (d / "devel" / "odoo" / "odoo-slovnormal" / ".git").mkdir(parents=True)
        (d / "devel" / "odoo" / "screenshots").mkdir(parents=True)
        chosen, no_repo = cli_bashrc_appliers.resolve_stream_cwd(d)
        self.assertEqual(chosen, d / "devel" / "odoo" / "odoo-slovnormal")
        self.assertFalse(no_repo)

    def test_a_plain_dir_named_like_a_checkout_is_rejected(self):
        # odoo-erp exists but is NOT a git work tree (no .git); the real
        # checkout odoo-slovnormal must be chosen instead.
        d = self._home()
        (d / "devel" / "odoo" / "odoo-erp").mkdir(parents=True)          # plain
        (d / "devel" / "odoo" / "odoo-slovnormal" / ".git").mkdir(parents=True)
        chosen, no_repo = cli_bashrc_appliers.resolve_stream_cwd(d)
        self.assertEqual(chosen, d / "devel" / "odoo" / "odoo-slovnormal")
        self.assertFalse(no_repo)

    def test_odoo_erp_wins_when_it_is_a_work_tree(self):
        # priority preserved: a normal box with the odoo-erp checkout still
        # lands there (byte-identical effect), even if odoo-slovnormal also
        # exists.
        d = self._home()
        (d / "devel" / "odoo" / "odoo-erp" / ".git").mkdir(parents=True)
        (d / "devel" / "odoo" / "odoo-slovnormal" / ".git").mkdir(parents=True)
        chosen, no_repo = cli_bashrc_appliers.resolve_stream_cwd(d)
        self.assertEqual(chosen, d / "devel" / "odoo" / "odoo-erp")
        self.assertFalse(no_repo)

    def test_gitfile_worktree_is_accepted(self):
        # `.git` may be a FILE (a linked worktree / submodule gitfile), not a
        # dir — `[ -e ]` / .exists() accepts both.
        d = self._home()
        (d / "devel" / "odoo" / "odoo-slovnormal").mkdir(parents=True)
        (d / "devel" / "odoo" / "odoo-slovnormal" / ".git").write_text(
            "gitdir: /somewhere/else\n")
        chosen, no_repo = cli_bashrc_appliers.resolve_stream_cwd(d)
        self.assertEqual(chosen, d / "devel" / "odoo" / "odoo-slovnormal")
        self.assertFalse(no_repo)

    def test_no_work_tree_falls_back_loudly_to_devel_odoo(self):
        # ~/devel/odoo exists as a PLAIN folder, no checkout is a work tree ->
        # last resort is devel/odoo, flagged no_repo (loud + footer no-repo).
        d = self._home()
        (d / "devel" / "odoo").mkdir(parents=True)
        chosen, no_repo = cli_bashrc_appliers.resolve_stream_cwd(d)
        self.assertEqual(chosen, d / "devel" / "odoo")
        self.assertTrue(no_repo)

    def test_no_work_tree_no_parent_falls_back_to_home(self):
        d = self._home()
        chosen, no_repo = cli_bashrc_appliers.resolve_stream_cwd(d)
        self.assertEqual(chosen, d)
        self.assertTrue(no_repo)

    def test_stream_session_cwd_uses_resolver(self):
        d = self._home()
        (d / "devel" / "odoo" / "odoo-slovnormal" / ".git").mkdir(parents=True)
        with m.patch.object(Path, "home", return_value=d):
            self.assertEqual(airuleset._stream_session_cwd(),
                             d / "devel" / "odoo" / "odoo-slovnormal")

    def test_stream_session_cwd_rejects_plain_odoo_erp(self):
        # the exact montalu1 regression: a plain odoo-erp must NOT be chosen as
        # a checkout; without a real work tree the bootstrap lands on the loud
        # fallback, never a silent non-repo checkout pick.
        d = self._home()
        (d / "devel" / "odoo" / "odoo-erp").mkdir(parents=True)   # plain
        with m.patch.object(Path, "home", return_value=d):
            # only devel/odoo exists as a plain folder -> fallback there
            self.assertEqual(airuleset._stream_session_cwd(),
                             d / "devel" / "odoo")


# --------------------------------------------------------------------------- #
# 2. chain constant + the shared bash predicate.
# --------------------------------------------------------------------------- #
class TestChainAndAttachBlock(unittest.TestCase):
    def test_chain_includes_odoo_slovnormal(self):
        self.assertIn("devel/odoo/odoo-slovnormal",
                      cli_bashrc_appliers.STREAM_DEV_CWD_CHAIN)
        # order: odoo-erp first (primary), then odoo-slovnormal, then bare parent
        self.assertEqual(cli_bashrc_appliers.STREAM_DEV_CWD_CHAIN,
                         ("devel/odoo/odoo-erp",
                          "devel/odoo/odoo-slovnormal", "devel/odoo"))

    def test_attach_block_uses_git_worktree_predicate(self):
        block = cli_bashrc_appliers.STREAM_SSH_ATTACH_BLOCK
        self.assertIn('[ -e "$HOME/$__airuleset_rel/.git" ]', block)
        # the old plain-existence predicate must be gone
        self.assertNotIn('[ -d "$HOME/$__airuleset_rel" ]', block)

    def test_attach_block_renders_the_shared_chain(self):
        block = cli_bashrc_appliers.STREAM_SSH_ATTACH_BLOCK
        self.assertIn(
            "for __airuleset_rel in " + " ".join(
                cli_bashrc_appliers.STREAM_DEV_CWD_CHAIN), block)

    def test_attach_block_has_loud_no_repo_fallback_line(self):
        block = cli_bashrc_appliers.STREAM_SSH_ATTACH_BLOCK
        self.assertIn("no git checkout under ~/devel/odoo", block)
        self.assertIn("tickets footer will show no-repo", block)


# --------------------------------------------------------------------------- #
# 3. behavioral: drive the REAL block through bash against a fake tmux/whoami,
#    and check the cwd (-c) the session is created with.
# --------------------------------------------------------------------------- #
_FAKE_TMUX_SRC = """#!/usr/bin/env bash
if [ -n "${FAKE_TMUX_LOG:-}" ]; then
  printf '[%d] %s\\n' "$#" "$*" >> "$FAKE_TMUX_LOG"
fi
case "$1" in
  has-session) exit 1 ;;
  list-sessions) exit 0 ;;
  *) exit 0 ;;
esac
"""


class TestAttachBlockCwdResolution(unittest.TestCase):
    def _run(self, make_dirs, whoami="montalu1"):
        d = Path(tempfile.mkdtemp(prefix="airuleset-1088-bash-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        bin_dir = d / "bin"
        bin_dir.mkdir()
        (bin_dir / "tmux").write_text(_FAKE_TMUX_SRC)
        (bin_dir / "tmux").chmod(0o755)
        (bin_dir / "whoami").write_text(
            "#!/usr/bin/env bash\necho '%s'\n" % whoami)
        (bin_dir / "whoami").chmod(0o755)
        home_dir = d / "home"
        home_dir.mkdir()
        for rel, is_repo in make_dirs:
            p = home_dir / rel
            p.mkdir(parents=True, exist_ok=True)
            if is_repo:
                (p / ".git").mkdir()
        log_path = d / "tmux.log"
        env = {
            "PATH": "%s:/usr/bin:/bin" % bin_dir,
            "HOME": str(home_dir),
            "SSH_TTY": "/dev/pts/0",
            "FAKE_TMUX_LOG": str(log_path),
        }
        r = subprocess.run(
            ["bash", "--norc", "-i", "-c",
             airuleset.STREAM_SSH_ATTACH_BLOCK],
            env=env, capture_output=True, text=True, timeout=15,
            stdin=subprocess.DEVNULL)
        log = log_path.read_text() if log_path.exists() else ""
        return home_dir, log, r

    def test_lands_in_the_checkout_when_only_slovnormal_is_a_repo(self):
        home, log, r = self._run(
            [("devel/odoo/odoo-slovnormal", True),
             ("devel/odoo/screenshots", False)])
        self.assertIn(
            "new-session -A -s montalu1 -c %s/devel/odoo/odoo-slovnormal"
            % home, log)

    def test_lands_in_odoo_erp_when_it_is_a_repo(self):
        home, log, r = self._run([("devel/odoo/odoo-erp", True)])
        self.assertIn(
            "new-session -A -s montalu1 -c %s/devel/odoo/odoo-erp" % home, log)

    def test_loud_fallback_to_devel_odoo_when_no_repo(self):
        home, log, r = self._run([("devel/odoo", False)])
        self.assertIn(
            "new-session -A -s montalu1 -c %s/devel/odoo" % home, log)
        self.assertIn("no git checkout under ~/devel/odoo", r.stderr)
        self.assertIn("tickets footer will show no-repo", r.stderr)


# --------------------------------------------------------------------------- #
# 4. tickets-status --refresh records reason:"no-repo" for a non-repo cwd.
# --------------------------------------------------------------------------- #
class TestTicketsStatusNoRepoReason(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-1088-ts-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, True))
        self._orig = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self._restore)
        statusbar.cache_dir().mkdir(parents=True, exist_ok=True)
        # a genuinely repo-less cwd (a fresh /tmp dir is not inside any repo)
        self.nonrepo = Path(tempfile.mkdtemp(prefix="airuleset-1088-nonrepo-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.nonrepo, True))

    def _restore(self):
        if self._orig is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig

    def test_refresh_writes_no_repo_reason(self):
        args = types.SimpleNamespace(refresh=True, cwd=str(self.nonrepo))
        airuleset.cmd_tickets_status(args)
        key = statusbar.cwd_key(str(self.nonrepo))
        cache = json.loads(
            (statusbar.cache_dir() / (key + ".json")).read_text())
        self.assertEqual(cache.get("reason"), "no-repo")
        self.assertEqual(cache.get("root"), "")
        self.assertIsNone(cache.get("open"))


# --------------------------------------------------------------------------- #
# 5. statusbar renders `no-repo` for a non-repo cache, nothing for gh-failure.
# --------------------------------------------------------------------------- #
class TestStatusbarNoRepoMarker(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="airuleset-1088-sb-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, True))
        self.cwd = "/home/montalu1/devel/odoo"

    def _seed(self, **entry):
        d = statusbar.cache_dir(self.home)
        d.mkdir(parents=True, exist_ok=True)
        base = {"ts": int(time.time())}
        base.update(entry)
        (d / (statusbar.cwd_key(self.cwd) + ".json")).write_text(
            json.dumps(base))

    def _seg(self):
        return statusbar.tickets_segment(self.cwd, home=self.home, spawn=False)

    def test_no_repo_reason_renders_marker(self):
        self._seed(open=None, name="", root="", reason="no-repo")
        self.assertIn("no-repo", self._seg())

    def test_root_empty_legacy_renders_marker(self):
        # a cache written before #1088 (root="" non-repo, no reason field)
        self._seed(open=None, name="", root="")
        self.assertIn("no-repo", self._seg())

    def test_gh_failure_root_known_renders_nothing(self):
        # gh unavailable: root KNOWN, open=None, no reason -> nothing (as before)
        self._seed(open=None, name="", root="/home/montalu1/devel/odoo/x")
        self.assertEqual(self._seg(), "")
        self.assertNotIn("no-repo", self._seg())

    def test_no_repo_with_pending_question_shows_both(self):
        self._seed(open=None, name="", root="", reason="no-repo",
                   user_waiting=2)
        seg = self._seg()
        self.assertIn("no-repo", seg)
        self.assertIn("U 2", seg)

    def test_cold_cache_no_marker(self):
        # no cache at all -> unknown, not a no-repo claim
        self.assertNotIn("no-repo", self._seg())

    def test_normal_repo_cache_unaffected(self):
        self._seed(open=5, name="demo", root="/home/montalu1/devel/odoo/x")
        seg = self._seg()
        self.assertIn("I 5", seg)
        self.assertNotIn("no-repo", seg)


if __name__ == "__main__":
    unittest.main()
