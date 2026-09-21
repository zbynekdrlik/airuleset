"""#1095 — the shared-VPS `/home` account-registration audit (#347) must survive
the gating post-check groups' `exit`.

Root cause: `cli_remote._remote_cmd_with_home_audit` appended the audit tail
`; __ar_rc=$?; echo <MARKER>; ls -1 /home; exit $__ar_rc` DIRECTLY after the
assembled remote command, whose tail (since #1048/#1051) is
`… && { gh-chain } && { playwright }` — two `{ … }` command GROUPS whose every
path ends in `exit` (0 on success/SKIP, 87/88 on failure). `exit` inside a
`{ … }` group terminates the whole remote `sh -c`, so the audit tail was
unreachable on every real box: the marker never reached stdout, `_home_audit`
saw no trustworthy listing and printed `⚠ REGISTRATION AUDIT NOT VERIFIED` once
per push — for two weeks, on subdev (100.118.174.27) + forestshop-dev.

Approach 1 (decided by the main session): run the gated chain in a SUBSHELL
`( … )` so its `exit` ends the subshell only, capture `__ar_rc=$?` from it, then
`echo <MARKER>; ls /home; exit $__ar_rc` — the deploy loop's rc semantics
(87/88 fail the target) are preserved because the captured rc is re-raised.

These locks:
  (a) argv-shape — `_remote_cmd_with_home_audit(X)` wraps X in `( … )` and the
      audit tail follows it; `_deploy_to_all_remotes` still wraps its assembled
      command through `_remote_cmd_with_home_audit`.
  (b) EXECUTION — the REAL assembled `install && {gates}` fragment, wrapped by
      the REAL `_remote_cmd_with_home_audit`, run through `sh -c` with fake
      install/gh/npx on PATH: success + no-npx-SKIP → marker + `ls /home` on
      stdout, rc 0; forced Playwright `exit 88` → marker present AND rc 88;
      forced gh-chain `exit 87` → marker present AND rc 87. RED on the pre-fix
      (unwrapped) assembly: the gates' `exit` swallows the marker.
  (c) `_parse_home_audit_output` reads the real `/home` listing from the new
      output shape (marker last).
"""

import inspect
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_remote  # noqa: E402


# --------------------------------------------------------------------------- #
# (a) argv-shape lock
# --------------------------------------------------------------------------- #
class TestAuditTailWrapsChainInSubshell(unittest.TestCase):
    def test_remote_cmd_wraps_the_chain_in_a_subshell(self):
        wrapped = cli_remote._remote_cmd_with_home_audit("CHAIN_TOKEN")
        # the chain must run inside a `( … )` subshell so a gate's `exit` ends
        # the subshell, not the whole remote `sh -c` (the audit tail after it).
        self.assertTrue(
            wrapped.startswith("( CHAIN_TOKEN )"),
            "the gated chain must be wrapped in a `( … )` subshell so its `exit` "
            "cannot swallow the audit tail (#1095). got=%r" % wrapped)

    def test_audit_tail_follows_the_subshell_and_reraises_rc(self):
        wrapped = cli_remote._remote_cmd_with_home_audit("CHAIN_TOKEN")
        marker = cli_remote._HOME_AUDIT_MARKER
        # the rc capture, marker echo, /home listing and rc re-raise all follow
        # the subshell — in that order.
        i_subshell = wrapped.index("( CHAIN_TOKEN )")
        i_rc = wrapped.index("__ar_rc=$?", i_subshell)
        i_marker = wrapped.index("echo '%s'" % marker, i_rc)
        i_ls = wrapped.index("ls -1 /home", i_marker)
        i_exit = wrapped.index("exit $__ar_rc", i_ls)
        self.assertTrue(i_subshell < i_rc < i_marker < i_ls < i_exit)

    def test_deploy_loop_still_wraps_its_assembled_command(self):
        src = inspect.getsource(cli_remote._deploy_to_all_remotes)
        self.assertIn("_remote_cmd_with_home_audit(remote_cmd)", src,
                      "the deploy loop must still route its assembled remote "
                      "command through the (now subshell-wrapping) audit helper")


# --------------------------------------------------------------------------- #
# (b)/(c) EXECUTION — the real assembled fragment through `sh -c` with fakes
# --------------------------------------------------------------------------- #
# the coreutils the deploy fragment invokes by name. A curated PATH holding
# ONLY these makes the "no npx" SKIP path deterministic on any box: without it
# the box's real npx would still resolve through `$HOME/.local/bin:$PATH` and
# RUN (a real chromium launch) instead of SKIPping.
_SYS_TOOLS = ("python3", "timeout", "cat", "head", "tail", "grep",
              "mktemp", "rm", "sleep", "ls")


class TestAuditSurvivesTheGatingPostChecks(unittest.TestCase):
    def _sysbin(self):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        for tool in _SYS_TOOLS:
            real = shutil.which(tool)
            if real:
                os.symlink(real, d / tool)
        return d

    def _ordered_gate_fragments(self):
        """The three post-check fragment strings, ordered as
        `_deploy_to_all_remotes` interpolates them — read from the SHIPPED
        source, so the ordering under test is the real one."""
        src = inspect.getsource(cli_remote._deploy_to_all_remotes)
        anchor = src.index("python3 airuleset.py install")
        funcs = {
            "_compact_hardoff_postcheck": cli_remote._compact_hardoff_postcheck,
            "_gh_chain_postcheck": cli_remote._gh_chain_postcheck,
            "_playwright_chromium_postcheck": cli_remote._playwright_chromium_postcheck,
        }
        ordered = sorted(funcs, key=lambda n: src.index(n + "()", anchor))
        return [funcs[n]() for n in ordered]

    def _assembled_chain(self):
        """`python3 airuleset.py install` then the post-checks in shipped order —
        the real gated chain a box runs after `git pull`."""
        return " && ".join(["python3 airuleset.py install"]
                           + self._ordered_gate_fragments())

    def _wrapped_fragment(self):
        """The gated chain routed through the REAL audit helper — exactly what
        `_deploy_to_all_remotes` sends over ssh for a shared host."""
        return cli_remote._remote_cmd_with_home_audit(self._assembled_chain())

    def _box(self, *, with_npx=True, gh_rc=0, npx_rc=0):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        home = Path(td.name)
        (home / ".claude").mkdir()
        bindir = home / ".local" / "bin"
        bindir.mkdir(parents=True)
        # present => Playwright does NOT SKIP as "not provisioned", so it reaches
        # the (fake) npx probe.
        (home / ".claude" / "airuleset-playwright-browsers-path").write_text("/tmp/bp\n")
        gh = bindir / "gh"
        gh.write_text("#!/bin/sh\nexit %d\n" % gh_rc)
        gh.chmod(0o755)
        if with_npx:
            npx = bindir / "npx"
            npx.write_text("#!/bin/sh\nexit %d\n" % npx_rc)
            npx.chmod(0o755)
        repo = home / "repo"
        repo.mkdir()
        (repo / "airuleset.py").write_text("import sys\nraise SystemExit(0)\n")
        return home, repo

    def _run(self, home, repo):
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["PATH"] = str(self._sysbin())     # only curated coreutils + the fakes
        env["AIRULESET_PW_POSTCHECK_RETRY_SLEEP"] = "0"   # no real 5 s wait
        return subprocess.run(["/bin/sh", "-c", self._wrapped_fragment()],
                              cwd=str(repo), capture_output=True, text=True,
                              timeout=60, env=env)

    def _real_home_names(self):
        return {n for n in os.listdir("/home") if not n.startswith(".")}

    def test_marker_and_home_listing_survive_the_success_path(self):
        # gh + npx present, both succeed — the Playwright group `exit 0`s on its
        # probe-success path, which swallowed the audit tail before the fix.
        home, repo = self._box(with_npx=True)
        r = self._run(home, repo)
        self.assertEqual(r.returncode, 0, r.stderr + "\n" + r.stdout)
        self.assertIn(
            cli_remote._HOME_AUDIT_MARKER, r.stdout,
            "the audit marker must reach stdout on a healthy box — the gates' "
            "`exit` swallowed it before #1095. stdout=%r" % r.stdout)
        # (c) _parse_home_audit_output reads the real /home listing (marker last)
        listing = cli_remote._parse_home_audit_output(r.stdout)
        self.assertEqual(cli_remote._parse_home_names(listing),
                         self._real_home_names(),
                         "the parsed /home listing must be the real one — proves "
                         "`ls -1 /home` actually ran after the gates (#1095)")

    def test_marker_survives_the_no_npx_skip_path(self):
        # no npx — the Playwright group takes its `exit 0` SKIP path, which also
        # terminated the remote shell before the audit tail pre-#1095.
        home, repo = self._box(with_npx=False)
        r = self._run(home, repo)
        self.assertEqual(r.returncode, 0, r.stderr + "\n" + r.stdout)
        self.assertIn(
            cli_remote._HOME_AUDIT_MARKER, r.stdout,
            "the audit marker must reach stdout even when Playwright SKIPs "
            "(no npx). stdout=%r" % r.stdout)

    def test_marker_present_and_rc88_reraised_on_playwright_failure(self):
        # npx present but the probe FAILS (rc 88) — the audit must STILL run
        # (an unregistered account is exactly what a failing box may carry) AND
        # the deploy loop must still fail the target (rc 88 re-raised).
        home, repo = self._box(with_npx=True, npx_rc=1)
        r = self._run(home, repo)
        self.assertEqual(r.returncode, 88, r.stderr + "\n" + r.stdout)
        self.assertIn(
            cli_remote._HOME_AUDIT_MARKER, r.stdout,
            "the audit must run even when a gate FAILS (#1095) — stdout=%r"
            % r.stdout)

    def test_marker_present_and_rc87_reraised_on_gh_chain_failure(self):
        # gh resolves but `gh --version` fails (rc 87) — audit must run, target
        # must still fail with the gh-chain's rc.
        home, repo = self._box(with_npx=True, gh_rc=1)
        r = self._run(home, repo)
        self.assertEqual(r.returncode, 87, r.stderr + "\n" + r.stdout)
        self.assertIn(
            cli_remote._HOME_AUDIT_MARKER, r.stdout,
            "the audit must run even when the gh-chain gate FAILS (#1095) — "
            "stdout=%r" % r.stdout)


if __name__ == "__main__":
    unittest.main()
