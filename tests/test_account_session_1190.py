"""#1190: `accounts transfer-session` carries the Claude conversation into the
project account (the #1184 migration used to leave it behind in newlevel).

Every test builds a FAKE home tree (old home, new home, a fake /proc) under a
temp dir; nothing touches a real ~/.claude. The rendered root script is run
with `runuser` replaced by a PATH stub (no root needed): it logs its argv and
`runuser -u X -- cmd` just runs `cmd`, so the test sees which uid every read
and write runs as, AND the real copy, modes and mtimes.

Covers the design acceptance:
  * a live claude cwd under the old dir (or one of its worktrees) -> refused;
  * an existing target uuid -> refused (and a memory file name collision);
  * the dry run lists the jsonl + session dir + memory, excludes the
    worktree-lane keys and writes nothing;
  * the rendered script reads the source AS its owner and writes the target
    AS the account (so the account owns every file), 0600/0700 modes,
    preserved mtimes, no-clobber placement;
  * the jsonl is NOT rewritten (Claude Code resolves a session by the projects
    DIRECTORY, never by the jsonl `cwd` field — evidence on the ticket);
  * the `claude --resume <uuid>` line and the path-move note are printed.
"""
import io
import json
import os
import pwd
import shutil
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
    """old home + new home + fake /proc under one temp root."""

    def __init__(self, base):
        self.base = Path(base)
        self.old_home = self.base / "home" / "newlevel"
        self.new_home = self.base / "home" / "fohmixer"
        self.old_dir = str(self.old_home / "devel" / "fohmixer")
        self.new_cwd = str(self.new_home / "devel" / "fohmixer")
        self.proc = self.base / "proc"
        for d in (self.proc, Path(self.old_dir), Path(self.new_cwd)):
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
        self.own_as_box(self.old_home)
        for p in [self.src, *self.src.rglob("*")]:
            os.utime(p, (OLD_MTIME, OLD_MTIME))
        # U2 is the NEWEST session (the one `--resume` should name first)
        os.utime(self.src / (U2 + ".jsonl"), (OLD_MTIME + 60, OLD_MTIME + 60))

    @staticmethod
    def own_as_box(root):
        """CI runs as root, so a seeded tree would read as root-owned and the
        transfer rightly refuses it (the source is read as its owner, never
        as root). Give a fake tree a real non-root owner, as on a box (main
        CI 8162623e). A no-op for a non-root run."""
        if os.geteuid() != 0:
            return
        nobody = pwd.getpwnam("nobody")
        for p in [Path(root), *Path(root).rglob("*")]:
            os.lchown(p, nobody.pw_uid, nobody.pw_gid)

    def proc_entry(self, pid, comm, cwd, argv=None, exe=None):
        d = self.proc / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "cmdline").write_bytes(
            b"".join(a.encode() + b"\0" for a in (argv or [comm, "--x"])))
        os.symlink(cwd, d / "cwd")
        if exe:
            os.symlink(exe, d / "exe")

    def plan(self, **kw):
        return sess.build_plan(
            "fohmixer", self.old_dir, from_home=str(self.old_home),
            target_home=str(self.new_home), target_cwd=self.new_cwd,
            proc_root=str(self.proc), **kw)


class TestProjectKey(unittest.TestCase):

    def test_every_non_alnum_char_becomes_a_dash(self):
        # measured on Claude Code 2.1.284: `.../x/a.b_c d` -> `...-x-a-b-c-d`
        self.assertEqual(sess.project_key("/home/newlevel/devel/fohmixer"),
                         "-home-newlevel-devel-fohmixer")
        self.assertEqual(sess.project_key("/tmp/x/a.b_c"), "-tmp-x-a-b-c")
        self.assertEqual(sess.project_key("/h/website-bakerion.ai"),
                         "-h-website-bakerion-ai")

    def test_relative_path_is_refused(self):
        with self.assertRaises(ValueError):
            sess.project_key("devel/fohmixer")

    def test_it_is_the_fleet_encoder(self):
        from watchdog.transcripts import encode_project_dir
        path = "/home/newlevel/devel/website-newlevel.media/a_b"
        self.assertEqual(sess.project_key(path), encode_project_dir(path))

    def test_a_char_the_fleet_encoder_does_not_map_is_refused(self):
        # Claude Code maps a space to '-', encode_project_dir keeps it: refuse
        for bad in ("/tmp/x/a b", "/tmp/x/a+b", "/tmp/x/a@b"):
            with self.assertRaises(ValueError, msg=bad):
                sess.project_key(bad)


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
                          argv=["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"])
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
            proc_root=str(self.t.proc))
        self.assertTrue(plan["refusals"])

    def _dry_run(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = accounts.transfer_session(
                "fohmixer", self.t.old_dir, from_home=str(self.t.old_home),
                target_home=str(self.t.new_home), target_cwd=self.t.new_cwd,
                proc_root=str(self.t.proc), **kw)
        return rc, out.getvalue(), err.getvalue()

    def test_dry_run_prints_the_listing_and_the_resume_line_and_writes_nothing(self):
        rc, out, err = self._dry_run()
        # stdout only ever carries a rendered script: the listing is on stderr
        self.assertEqual((rc, out), (0, ""))
        for needle in (U1 + ".jsonl", U1 + "/", U2 + ".jsonl", "memory/MEMORY.md",
                       str(self.t.dst), "--claude-worktrees-agent-zz",
                       "claude --resume " + U2, "DRY RUN", self.t.new_cwd):
            self.assertIn(needle, err)
        self.assertFalse(self.t.dst.exists())

    def test_dry_run_with_a_refusal_exits_1(self):
        self.t.proc_entry(4242, "claude", self.t.old_dir)
        rc, out, err = self._dry_run()
        self.assertEqual((rc, out), (1, ""))     # a refusal writes stderr only
        self.assertIn("4242", err)
        # a refused plan never reads like a go: no DRY RUN banner, no resume line
        self.assertIn("REFUSED", err)
        self.assertNotIn("DRY RUN", err)
        self.assertNotIn("claude --resume", err)

    def test_refused_render_prints_nothing_on_stdout(self):
        # `--render | sudo bash` must never feed the listing to root bash
        (self.t.src / "memory" / "$(touch PWNED)").write_text("x")
        self.t.proc_entry(4242, "claude", self.t.old_dir)
        rc, out, err = self._dry_run(render=True)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("4242", err)

    def test_unreadable_source_says_so(self):
        # a PermissionError on the listing, not chmod 0: root (CI) reads a
        # mode-0 dir anyway, so the chmod premise only held for a user run
        real = os.listdir

        def denied(p):
            if str(p) == str(self.t.src):
                raise PermissionError(13, "Permission denied", str(p))
            return real(p)
        with mock.patch("os.listdir", side_effect=denied):
            plan = self.t.plan()
        self.assertTrue(any("cannot read" in r for r in plan["refusals"]),
                        plan["refusals"])

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
        (self.bin / "runuser").write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\n'
            'echo "runuser $*" >> %s\n'
            'while [ "$1" != "--" ]; do shift; done; shift; exec "$@"\n' % self.log)
        os.chmod(self.bin / "runuser", 0o755)

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
        # ownership: the source is read AS its owner, every write into the
        # account's home runs AS the account (so the account owns the files),
        # and each of the 5 items is placed by the account's no-clobber rename
        log = self._log().splitlines()
        owner = pwd.getpwuid(os.stat(self.t.src).st_uid).pw_name
        self.assertTrue(any(ln.startswith("runuser -u %s -- tar -C %s -cf -"
                                          % (owner, self.t.src)) for ln in log), log)
        self.assertTrue(any(ln.startswith("runuser -u fohmixer -- tar -C ")
                            and ln.endswith(" -xpf -") for ln in log), log)
        placed = [ln for ln in log if ln.startswith("runuser -u fohmixer -- mv -n -T")]
        self.assertEqual(len(placed), 5, placed)
        # every command that is not the source read runs as the account
        self.assertEqual([ln for ln in log if not ln.startswith(
            ("runuser -u fohmixer -- ", "runuser -u %s -- tar -C %s -cf -"
             % (owner, self.t.src)))], [])
        self.assertEqual([p.name for p in dst.iterdir() if p.name.startswith(".")],
                         [], "temp dir not cleaned")
        # the source is untouched
        self.assertTrue((self.t.src / (U1 + ".jsonl")).is_file())
        self.assertIn("claude --resume " + U2, r.stdout)
        self.assertIn(self.t.new_cwd, r.stdout)

    def test_runs_from_a_cwd_the_account_cannot_enter(self):
        # `sudo bash` keeps the operator's cwd (under the 0700 old home);
        # find must not need to return there
        locked = self.t.base / "locked-cwd"
        locked.mkdir()
        env = dict(os.environ, PATH="%s:%s" % (self.bin, os.environ["PATH"]))
        try:
            r = subprocess.run(
                ["bash", "-c", 'cd "$1" && chmod 000 "$1" && exec bash -s', "_",
                 str(locked)], input=sess.render_script(self.t.plan()), env=env,
                capture_output=True, text=True, timeout=60)
        finally:
            os.chmod(locked, 0o755)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.t.dst / (U1 + ".jsonl")).is_file())

    def test_a_failure_outside_mv_still_names_what_was_placed(self):
        script = sess.render_script(self.t.plan())
        self.t.dst.mkdir(parents=True)
        (self.t.dst / "memory").write_text("a FILE where memory/ must go\n")
        r = self._run(script)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("Placed before the failure: none", r.stderr)

    def test_source_owner_is_fixed_at_render_time(self):
        plan = self.t.plan()
        self.assertEqual(plan["src_owner"],
                         pwd.getpwuid(os.stat(self.t.src).st_uid).pw_name)
        real = os.lstat

        def as_root(p, *a, **k):            # the key dir reads as root-owned
            st = real(p, *a, **k)
            if str(p) == str(self.t.src):
                return os.stat_result((st.st_mode, st.st_ino, st.st_dev,
                                       st.st_nlink, 0, 0) + tuple(st)[6:])
            return st
        with mock.patch("os.lstat", side_effect=as_root):
            refused = self.t.plan()
        self.assertTrue(any("owned by root" in r for r in refused["refusals"]))

    def test_a_signal_mid_placement_still_names_what_was_placed(self):
        # SIGTERM to the script while the 5th item (memory/x.md) is moved:
        # the report must list every item already in the target, x.md included
        (self.bin / "runuser").write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\n'
            'echo "runuser $*" >> %s\n'
            'while [ "$1" != "--" ]; do shift; done; shift\n'
            'if [ "$1" = mv ] && [[ "${@: -1}" == */memory/x.md ]]; then kill -TERM "$PPID"; fi\n'
            'exec "$@"\n' % self.log)
        r = self._run(sess.render_script(self.t.plan()))
        self.assertEqual(r.returncode, 143, r.stderr)
        placed = r.stderr.split("Placed before the failure:", 1)[-1].splitlines()[0]
        for item in (U2 + ".jsonl", U1 + ".jsonl", U1, "memory/MEMORY.md",
                     "memory/x.md"):
            self.assertIn(item, placed.split(), r.stderr)

    def test_script_rechecks_the_source_owner_at_run_time(self):
        script = sess.render_script(self.t.plan())
        lines = [ln if not ln.startswith("SRC_OWNER=") else "SRC_OWNER=nobody-1190"
                 for ln in script.splitlines()]
        r = self._run("\n".join(lines) + "\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("no longer owned by nobody-1190", r.stderr)
        self.assertFalse(self.t.dst.exists())

    def test_new_target_dirs_are_0700(self):
        r = self._run(sess.render_script(self.t.plan()))
        self.assertEqual(r.returncode, 0, r.stderr)
        for p in (self.t.new_home / ".claude", self.t.dst.parent, self.t.dst,
                  self.t.dst / "memory"):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700, p)

    def test_script_rechecks_a_live_claude_at_run_time(self):
        script = sess.render_script(self.t.plan())      # rendered while clean
        self.t.proc_entry(4242, "claude", self.t.old_dir)
        r = self._run(script)
        self.assertEqual(r.returncode, 1)
        self.assertIn("4242", r.stderr)
        self.assertFalse(self.t.dst.exists())

    def _guard_cases(self):
        old = self.t.old_dir
        wt = old + "/.claude/worktrees/agent-zz"
        return [
            ("native claude", dict(comm="claude", cwd=old), True),
            ("npm claude in a worktree",
             dict(comm="node", cwd=wt,
                  argv=["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"]),
             True),
            ("npm bin shim", dict(comm="node", cwd=old, argv=["node", "/usr/bin/claude"]),
             True),
            ("npm relative cli.js", dict(comm="node", cwd=old,
                                         argv=["node", "claude-code/cli.js"]), True),
            ("versioned binary", dict(comm="2.1.284", cwd=old, argv=["2.1.284"],
                                      exe="/home/x/.local/share/claude/versions/2.1.284"),
             True),
            ("deleted cwd", dict(comm="claude", cwd=old + " (deleted)"), True),
            ("sibling prefix", dict(comm="claude", cwd=old + "-other"), False),
            ("a shell", dict(comm="bash", cwd=old), False),
            ("claude-code-log tool", dict(comm="python3", cwd=old,
                                          argv=["/home/x/claude-code-log/run.py"]),
             False),
        ]

    def test_bash_guard_matches_the_python_guard(self):
        for n, (name, kw, refuse) in enumerate(self._guard_cases()):
            with self.subTest(name):
                script = sess.render_script(self.t.plan())      # rendered clean
                pid = 5000 + n
                self.t.proc_entry(pid, **kw)
                try:
                    py = any(str(pid) in r for r in self.t.plan()["refusals"])
                    r = self._run(script)
                    self.assertEqual(py, refuse, "python guard")
                    self.assertEqual(r.returncode == 1 and str(pid) in r.stderr,
                                     refuse, r.stderr)
                finally:
                    shutil.rmtree(self.t.proc / str(pid))
                    if self.t.dst.exists():
                        shutil.rmtree(self.t.dst)

    def test_symlinked_old_checkout_guards_its_real_path(self):
        link = str(self.t.base / "link-fohmixer")
        os.symlink(self.t.old_dir, link)
        shutil.copytree(self.t.src, self.t.src.parent / sess.project_key(link))
        self.t.own_as_box(self.t.src.parent / sess.project_key(link))
        plan = sess.build_plan(
            "fohmixer", link, from_home=str(self.t.old_home),
            target_home=str(self.t.new_home), target_cwd=self.t.new_cwd,
            proc_root=str(self.t.proc))
        script = sess.render_script(plan)
        self.t.proc_entry(4244, "claude", self.t.old_dir)   # /proc shows the real path
        replan = sess.build_plan(
            "fohmixer", link, from_home=str(self.t.old_home),
            target_home=str(self.t.new_home), target_cwd=self.t.new_cwd,
            proc_root=str(self.t.proc))
        self.assertTrue(any("4244" in r for r in replan["refusals"]))
        r = self._run(script)
        self.assertEqual(r.returncode, 1)
        self.assertIn("4244", r.stderr)

    def test_an_item_that_appears_meanwhile_is_never_overwritten(self):
        script = sess.render_script(self.t.plan())
        # a file that appears between step 2 and step 4 (simulated: the
        # account's `mkdir` of memory/ is where it lands) must not be clobbered
        (self.bin / "runuser").write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\n'
            'echo "runuser $*" >> %s\n'
            'while [ "$1" != "--" ]; do shift; done; shift\n'
            'if [ "$1" = mv ] && [ "${@: -1}" = %s ]; then echo mine > "${@: -1}"; fi\n'
            'exec "$@"\n' % (self.log, self.t.dst / "memory" / "x.md"))
        r = self._run(script)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAILED", r.stderr)
        self.assertIn("memory/MEMORY.md", r.stderr)    # named as already placed
        self.assertEqual((self.t.dst / "memory" / "x.md").read_text(), "mine\n")

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

    def test_status_rejects_transfer_arguments(self):
        import argparse
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="cmd")
        accounts.register_parser(sub)
        a = p.parse_args(["accounts", "status", "fohmixer"])
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(accounts.cmd_accounts(a), 2)

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
