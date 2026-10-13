"""#1224: dev1 froze three times in three weeks because one session's Bash tool
child grew without bound (2.6-2.9 GB) and nothing capped a session's memory.

Every Claude launch now goes through `_airuleset_exec_claude`:
- On a declared workstation (dev1, dev2) it execs claude inside its own
  `systemd-run --user --scope`, with `MemoryMax` at 40 % of MemTotal (floor
  2 GiB) and `MemorySwapMax` at 10 %.
- Elsewhere, or when the user manager is unreachable, it falls back to a
  plain exec, so a launch is never blocked.
- Every session exports `BASH_ENV` = the tool-OOM file. Each non-interactive
  bash Claude Code starts (its Bash tool calls) raises its own
  `oom_score_adj` to 800, so the kernel kills the runaway tool before Claude.
"""
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_claude_scripts  # noqa: E402

DEV1_KB = 7865772          # dev1 MemTotal, read live 2026-10-13
SMALL_KB = 3911564         # the controller's: the 2 GiB floor applies


def _scope():
    import cli_session_scope
    return cli_session_scope


def _exe(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class _Launch:
    """Run a RENDERED launcher with fake claude / systemd-run / hostname on the
    launcher's own `$HOME/.local/bin` PATH seam."""

    def __init__(self, base, *, host="dev1", mem_kb=DEV1_KB, sr_fail=False,
                 script=None):
        self.home = Path(base)
        b = self.home / ".local" / "bin"
        b.mkdir(parents=True)
        _exe(b / "claude", "#!/bin/sh\necho \"CLAUDE BASH_ENV=$BASH_ENV\"\n")
        _exe(b / "hostname", "#!/bin/sh\necho %s\n" % host)
        _exe(b / "systemd-run", "#!/bin/bash\n"
             "echo \"$*\" >> \"$HOME/sr.log\"\n"
             + ("exit 1\n" if sr_fail else "")
             + "while [ $# -gt 0 ] && [ \"$1\" != -- ]; do shift; done; shift\n"
             "exec \"$@\"\n")
        (self.home / "meminfo").write_text("MemTotal:       %d kB\n" % mem_kb)
        (self.home / ".claude").mkdir()      # as `airuleset.py install` leaves it
        _scope().write_tool_oom_env(self.home / ".claude")
        self.script = self.home / "launch.sh"
        _exe(self.script, script or cli_claude_scripts.render_claude_launch_script())

    def run(self, mode="plain"):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home),
               "AIRULESET_MEMINFO": str(self.home / "meminfo")}
        return subprocess.run(["bash", str(self.script), mode], env=env,
                              capture_output=True, text=True, timeout=30,
                              cwd=str(self.home))

    def sr_calls(self):
        log = self.home / "sr.log"
        return log.read_text().splitlines() if log.exists() else []


class TestRender(unittest.TestCase):

    def test_every_launch_goes_through_the_helper(self):
        for name, text in (
                ("launch", cli_claude_scripts.render_claude_launch_script()),
                ("impl", cli_claude_scripts.render_claude_impl_launch_script())):
            helper_end = text.index("\n}\n", text.index("_airuleset_exec_claude() {"))
            self.assertNotIn("exec claude", text[helper_end:], name)
            self.assertIn("_airuleset_exec_claude ", text[helper_end:], name)

    def test_the_declared_hosts_are_dev1_and_dev2(self):
        self.assertEqual(tuple(_scope().SCOPE_HOSTS), ("dev1", "dev2"))


class TestLaunch(unittest.TestCase):

    def test_a_declared_workstation_runs_claude_in_a_capped_scope(self):
        with tempfile.TemporaryDirectory() as d:
            ln = _Launch(d)
            r = ln.run()
            self.assertEqual(r.returncode, 0, r.stderr)
            calls = ln.sr_calls()
            self.assertEqual(len(calls), 2, calls)          # the probe, then the exec
            self.assertIn("--user --scope", calls[1])
            self.assertIn("MemoryMax=%dK" % (DEV1_KB * 40 // 100), calls[1])
            self.assertIn("MemorySwapMax=%dK" % (DEV1_KB * 10 // 100), calls[1])
            self.assertIn("-- claude", calls[1])
            self.assertIn("CLAUDE BASH_ENV=%s/.claude/airuleset-tool-oom.sh" % d,
                          r.stdout)

    def test_a_small_box_gets_the_2_gib_floor(self):
        with tempfile.TemporaryDirectory() as d:
            ln = _Launch(d, mem_kb=SMALL_KB)
            ln.run()
            self.assertIn("MemoryMax=2097152K", ln.sr_calls()[1])

    def test_an_undeclared_host_runs_claude_uncapped(self):
        with tempfile.TemporaryDirectory() as d:
            ln = _Launch(d, host="laptop")
            r = ln.run()
            self.assertEqual(ln.sr_calls(), [])
            self.assertIn("CLAUDE BASH_ENV=", r.stdout)

    def test_an_unreachable_user_manager_never_blocks_the_launch(self):
        with tempfile.TemporaryDirectory() as d:
            ln = _Launch(d, sr_fail=True)
            r = ln.run()
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("CLAUDE BASH_ENV=", r.stdout)
            self.assertIn("uncapped", r.stderr)
            self.assertEqual(len(ln.sr_calls()), 1)         # the probe only

    def test_the_impl_launcher_shares_the_helper(self):
        text = cli_claude_scripts.render_claude_impl_launch_script()
        self.assertIn("MemorySwapMax=", text)
        self.assertIn("BASH_ENV=", text)


class TestToolOom(unittest.TestCase):

    def _env_file(self, d, prev=None):
        p = Path(d, "airuleset-tool-oom.sh")
        p.write_text(_scope().render_tool_oom_env())
        env = {"PATH": "/usr/bin:/bin", "BASH_ENV": str(p)}
        if prev:
            env["AIRULESET_PREV_BASH_ENV"] = prev
        return env

    def test_a_tool_call_raises_its_own_oom_score(self):
        with tempfile.TemporaryDirectory() as d:
            out = subprocess.run(["/bin/bash", "-c", "cat /proc/self/oom_score_adj"],
                                 env=self._env_file(d), capture_output=True, text=True)
            self.assertEqual(out.stdout.strip(), "800", out.stderr)

    def test_an_earlier_bash_env_still_runs(self):
        with tempfile.TemporaryDirectory() as d:
            prev = Path(d, "prev.sh")
            prev.write_text("echo prev-ran\n")
            out = subprocess.run(["/bin/bash", "-c", ":"], capture_output=True,
                                 text=True, env=self._env_file(d, str(prev)))
            self.assertEqual(out.stdout.strip(), "prev-ran")

    def test_the_installer_writes_the_file(self):
        import cli_bashrc_appliers as appliers
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            appliers.apply_ultracode_launcher(
                bashrc_path=p / ".bashrc", script_path=p / "launch.sh",
                history_script_path=p / "h.py", popup_script_path=p / "pop.sh",
                impl_script_path=p / "impl.sh", impl_prompt_path=p / "impl.md",
                user="nobody")
            self.assertEqual((p / "airuleset-tool-oom.sh").read_text(),
                             _scope().render_tool_oom_env())


if __name__ == "__main__":
    unittest.main()
