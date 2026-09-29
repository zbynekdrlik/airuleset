"""#1190: `accounts transfer-session` carries the Claude conversation into the
project account (the #1184 migration used to leave it behind in newlevel).

Every test builds a FAKE home tree (old home, new home, a fake /proc) under a
temp dir; nothing touches a real ~/.claude. The rendered root script is run
with `runuser`/`chown` replaced by PATH stubs (no root needed): the stubs log
their argv and `runuser -u X -- cmd` just runs `cmd`, so the test sees every
ownership step AND the real copy, modes and mtimes.

Covers the design acceptance:
  * a live claude cwd under the old dir (or one of its worktrees) -> refused;
  * an existing target uuid -> refused (and a memory file name collision);
  * the dry run lists the jsonl + session dir + memory, excludes the
    worktree-lane keys and writes nothing;
  * the rendered script copies with the account as owner, 0600/0700 modes and
    preserved mtimes, via a root-owned stage and writes as the account;
  * the jsonl is NOT rewritten (Claude Code resolves a session by the projects
    DIRECTORY, never by the jsonl `cwd` field — evidence on the ticket);
  * the `claude --resume <uuid>` line and the path-move note are printed.
"""
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_account_session as sess  # noqa: E402
import cli_accounts as accounts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

U1 = "11111111-2222-4333-8444-555555555555"
U2 = "22222222-2222-4333-8444-555555555555"
OLD_MTIME = 1_700_000_000   # 2023-11-14, far from "now": a copy that loses it shows


def _line(uuid, cwd, text):
    return json.dumps({"type": "user", "sessionId": uuid, "cwd": cwd,
                       "message": {"role": "user", "content": text}},
                      ensure_ascii=False) + "\n"


class _Tree:
    """old home + new home + fake /proc + stage dir under one temp root."""

    def __init__(self, base):
        self.base = Path(base)
        self.old_home = self.base / "home" / "newlevel"
        self.new_home = self.base / "home" / "fohmixer"
        self.old_dir = str(self.old_home / "devel" / "fohmixer")
        self.new_cwd = str(self.new_home / "devel" / "fohmixer")
        self.proc = self.base / "proc"
        self.stage = self.base / "stage"
        for d in (self.proc, self.stage, Path(self.old_dir), Path(self.new_cwd)):
            d.mkdir(parents=True, exist_ok=True)
        self.src = (self.old_home / ".claude" / "projects"
                    / sess.project_key(self.old_dir))
        self.dst = (self.new_home / ".claude" / "projects"
                    / sess.project_key(self.new_cwd))

    def seed(self):
        """Two sessions (U1 with a subagent dir, U2 bare), memory, plus a
        worktree-lane key that must NOT move."""
        self.src.mkdir(parents=True)
        (self.src / (U1 + ".jsonl")).write_text(
            _line(U1, self.old_dir, "prvá otázka") + '{"type":"last-prompt"}\n',
            encoding="utf-8")
        sub = self.src / U1 / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-a1.jsonl").write_text(_line(U1, self.old_dir, "lane"),
                                            encoding="utf-8")
        (self.src / (U2 + ".jsonl")).write_text(_line(U2, self.old_dir, "druhá"),
                                                encoding="utf-8")
        mem = self.src / "memory"
        mem.mkdir()
        (mem / "MEMORY.md").write_text("- [x](x.md) — pamäť\n", encoding="utf-8")
        (mem / "x.md").write_text("fakt\n", encoding="utf-8")
        wt = self.src.parent / (self.src.name + "--claude-worktrees-agent-zz")
        wt.mkdir()
        (wt / "33333333-2222-4333-8444-555555555555.jsonl").write_text("{}\n")
        for p in [self.src, *self.src.rglob("*")]:
            os.utime(p, (OLD_MTIME, OLD_MTIME))
        # U2 is the NEWEST session (the one `--resume` should name first)
        os.utime(self.src / (U2 + ".jsonl"), (OLD_MTIME + 60, OLD_MTIME + 60))

    def proc_entry(self, pid, comm, cwd, argv0=None):
        d = self.proc / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "cmdline").write_bytes((argv0 or comm).encode() + b"\0--x\0")
        os.symlink(cwd, d / "cwd")

    def plan(self, **kw):
        return sess.build_plan(
            "fohmixer", self.old_dir, from_home=str(self.old_home),
            target_home=str(self.new_home), target_cwd=self.new_cwd,
            proc_root=str(self.proc), stage_base=str(self.stage), **kw)


class TestProjectKey(unittest.TestCase):

    def test_every_non_alnum_char_becomes_a_dash(self):
        # measured on Claude Code 2.1.284: `.../x/a.b_c d` -> `...-x-a-b-c-d`
        self.assertEqual(sess.project_key("/home/newlevel/devel/fohmixer"),
                         "-home-newlevel-devel-fohmixer")
        self.assertEqual(sess.project_key("/tmp/x/a.b_c d"), "-tmp-x-a-b-c-d")
        self.assertEqual(sess.project_key("/h/website-bakerion.ai"),
                         "-h-website-bakerion-ai")

    def test_relative_path_is_refused(self):
        with self.assertRaises(ValueError):
            sess.project_key("devel/fohmixer")


class TestLiveGuard(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = _Tree(self._tmp.name)
        self.t.seed()

    def tearDown(self):
        self._tmp.cleanup()

    def test_claude_in_the_old_checkout_refuses(self):
        self.t.proc_entry(4242, "claude", self.t.old_dir)
        plan = self.t.plan()
        self.assertTrue(any("4242" in r for r in plan["refusals"]), plan["refusals"])

    def test_claude_in_an_old_worktree_refuses(self):
        wt = Path(self.t.old_dir) / ".claude" / "worktrees" / "agent-zz"
        wt.mkdir(parents=True)
        self.t.proc_entry(4243, "node", str(wt),
                          argv0="/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js")
        plan = self.t.plan()
        self.assertTrue(any("4243" in r for r in plan["refusals"]), plan["refusals"])

    def test_a_shell_or_a_foreign_claude_does_not_refuse(self):
        self.t.proc_entry(10, "bash", self.t.old_dir)
        self.t.proc_entry(11, "claude", self.t.new_cwd)
        sibling = self.t.old_dir + "-other"     # prefix, not a subdirectory
        Path(sibling).mkdir()
        self.t.proc_entry(12, "claude", sibling)
        self.assertEqual(self.t.plan()["refusals"], [])


class TestPlanAndDryRun(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = _Tree(self._tmp.name)
        self.t.seed()

    def tearDown(self):
        self._tmp.cleanup()

    def test_plan_lists_sessions_dirs_memory_and_excludes_worktree_keys(self):
        plan = self.t.plan()
        self.assertEqual([s["uuid"] for s in plan["sessions"]], [U2, U1])
        by = {s["uuid"]: s for s in plan["sessions"]}
        self.assertTrue(by[U1]["has_dir"])
        self.assertFalse(by[U2]["has_dir"])
        self.assertGreater(by[U1]["dir_bytes"], 0)
        self.assertEqual(sorted(plan["memory"]), ["MEMORY.md", "x.md"])
        self.assertEqual(plan["excluded_keys"],
                         [self.t.src.name + "--claude-worktrees-agent-zz"])
        self.assertEqual(plan["src_dir"], str(self.t.src))
        self.assertEqual(plan["dst_dir"], str(self.t.dst))
        self.assertEqual(plan["refusals"], [])

    def test_existing_target_uuid_refuses(self):
        self.t.dst.mkdir(parents=True)
        (self.t.dst / (U1 + ".jsonl")).write_text("{}\n")
        plan = self.t.plan()
        self.assertTrue(any(U1 in r for r in plan["refusals"]), plan["refusals"])

    def test_existing_target_memory_file_refuses(self):
        (self.t.dst / "memory").mkdir(parents=True)
        (self.t.dst / "memory" / "MEMORY.md").write_text("new account memory\n")
        plan = self.t.plan()
        self.assertTrue(any("MEMORY.md" in r for r in plan["refusals"]),
                        plan["refusals"])

    def test_missing_source_refuses(self):
        plan = sess.build_plan(
            "fohmixer", str(self.t.base / "nope"), from_home=str(self.t.old_home),
            target_home=str(self.t.new_home), target_cwd=self.t.new_cwd,
            proc_root=str(self.t.proc), stage_base=str(self.t.stage))
        self.assertTrue(plan["refusals"])

    def _dry_run(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = accounts.transfer_session(
                "fohmixer", self.t.old_dir, from_home=str(self.t.old_home),
                target_home=str(self.t.new_home), target_cwd=self.t.new_cwd,
                proc_root=str(self.t.proc), stage_base=str(self.t.stage), **kw)
        return rc, out.getvalue(), err.getvalue()

    def test_dry_run_prints_the_listing_and_the_resume_line_and_writes_nothing(self):
        rc, out, _ = self._dry_run()
        self.assertEqual(rc, 0)
        for needle in (U1 + ".jsonl", U1 + "/", U2 + ".jsonl", "memory/MEMORY.md",
                       str(self.t.dst), "--claude-worktrees-agent-zz",
                       "claude --resume " + U2, "DRY RUN", self.t.new_cwd):
            self.assertIn(needle, out)
        self.assertFalse(self.t.dst.exists())

    def test_dry_run_with_a_refusal_exits_1(self):
        self.t.proc_entry(4242, "claude", self.t.old_dir)
        rc, out, err = self._dry_run()
        self.assertEqual(rc, 1)
        self.assertIn("4242", err)
        # a refused plan never reads like a go: no DRY RUN banner, no resume line
        self.assertIn("REFUSED", out)
        self.assertNotIn("DRY RUN", out)
        self.assertNotIn("claude --resume", out)

    def test_apply_needs_root(self):
        with mock.patch("os.geteuid", return_value=1000):
            rc, _, err = self._dry_run(apply=True)
        self.assertEqual(rc, 1)
        self.assertIn("root", err)
        self.assertFalse(self.t.dst.exists())


class TestRenderedScript(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = _Tree(self._tmp.name)
        self.t.seed()
        self.bin = self.t.base / "bin"
        self.bin.mkdir()
        self.log = self.t.base / "stub.log"
        (self.bin / "chown").write_text(
            '#!/usr/bin/env bash\necho "chown $*" >> %s\n' % self.log)
        (self.bin / "runuser").write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\n'
            'echo "runuser $*" >> %s\n'
            'while [ "$1" != "--" ]; do shift; done; shift; exec "$@"\n' % self.log)
        for f in ("chown", "runuser"):
            os.chmod(self.bin / f, 0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, script):
        env = dict(os.environ, PATH="%s:%s" % (self.bin, os.environ["PATH"]))
        return subprocess.run(["bash", "-s"], input=script, env=env,
                              capture_output=True, text=True, timeout=60)

    def _log(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_copy_owner_modes_mtimes_and_resume_line(self):
        src_bytes = (self.t.src / (U1 + ".jsonl")).read_bytes()
        r = self._run(sess.render_script(self.t.plan()))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        dst = self.t.dst
        # copied, byte-identical: the jsonl `cwd` field is NOT rewritten
        self.assertEqual((dst / (U1 + ".jsonl")).read_bytes(), src_bytes)
        self.assertTrue((dst / U1 / "subagents" / "agent-a1.jsonl").is_file())
        self.assertTrue((dst / (U2 + ".jsonl")).is_file())
        self.assertEqual((dst / "memory" / "MEMORY.md").read_text(encoding="utf-8"),
                         "- [x](x.md) — pamäť\n")
        self.assertFalse(any("worktrees" in p.name for p in dst.parent.iterdir()))
        # modes 0600 files / 0700 dirs, mtimes preserved
        for p in [dst / (U1 + ".jsonl"), dst / U1 / "subagents" / "agent-a1.jsonl",
                  dst / "memory" / "x.md"]:
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600, p)
            self.assertEqual(int(p.stat().st_mtime), OLD_MTIME, p)
        self.assertEqual(int((dst / (U2 + ".jsonl")).stat().st_mtime), OLD_MTIME + 60)
        for p in [dst / U1, dst / U1 / "subagents"]:
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700, p)
        # ownership: the staged copy is chowned to the account, and every
        # write into the account's home runs AS the account
        log = self._log()
        self.assertIn("chown -R fohmixer:fohmixer", log)
        self.assertIn("runuser -u fohmixer --", log)
        self.assertEqual(list(self.t.stage.iterdir()), [], "stage not cleaned")
        # the source is untouched
        self.assertTrue((self.t.src / (U1 + ".jsonl")).is_file())
        self.assertIn("claude --resume " + U2, r.stdout)
        self.assertIn(self.t.new_cwd, r.stdout)

    def test_script_rechecks_a_live_claude_at_run_time(self):
        script = sess.render_script(self.t.plan())      # rendered while clean
        self.t.proc_entry(4242, "claude", self.t.old_dir)
        r = self._run(script)
        self.assertEqual(r.returncode, 1)
        self.assertIn("4242", r.stderr)
        self.assertFalse(self.t.dst.exists())

    def test_script_rechecks_an_existing_target_uuid_at_run_time(self):
        script = sess.render_script(self.t.plan())
        self.t.dst.mkdir(parents=True)
        (self.t.dst / (U1 + ".jsonl")).write_text("{}\n")
        r = self._run(script)
        self.assertEqual(r.returncode, 1)
        self.assertIn(U1, r.stderr)
        self.assertFalse((self.t.dst / (U2 + ".jsonl")).exists())

    def test_a_refused_plan_does_not_render(self):
        self.t.proc_entry(4242, "claude", self.t.old_dir)
        with self.assertRaises(ValueError):
            sess.render_script(self.t.plan())


class TestCliWiring(unittest.TestCase):

    def test_parser_accepts_transfer_session(self):
        import argparse
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="cmd")
        accounts.register_parser(sub)
        a = p.parse_args(["accounts", "transfer-session", "fohmixer",
                          "--from-dir", "/home/newlevel/devel/fohmixer"])
        self.assertEqual((a.action, a.account, a.from_dir, a.apply, a.render),
                         ("transfer-session", "fohmixer",
                          "/home/newlevel/devel/fohmixer", False, False))
        self.assertEqual(p.parse_args(["accounts"]).action, "status")

    def test_undeclared_account_is_refused(self):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rc = accounts.transfer_session("newlevel", "/home/newlevel/devel/x")
        self.assertEqual(rc, 1)
        self.assertIn("newlevel", err.getvalue())

    def test_from_home_defaults_to_the_home_of_the_old_checkout(self):
        self.assertEqual(sess.default_from_home("/home/newlevel/devel/fohmixer"),
                         "/home/newlevel")
        self.assertIsNone(sess.default_from_home("/srv/fohmixer"))

    def test_playbook_names_the_step(self):
        text = (ROOT / ".claude" / "rules" / "internals-accounts.md").read_text(
            encoding="utf-8")
        self.assertIn("accounts transfer-session", text)
        recipe = (ROOT / "skills" / "onboard-project" / "legacy-newlevel.md").read_text(
            encoding="utf-8")
        self.assertIn("accounts transfer-session", recipe)


if __name__ == "__main__":
    unittest.main()
