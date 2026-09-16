"""#1051 — cmd_push per-target post-check: after `airuleset.py install`, run
`gh --version` through the freshly-installed ~/.local/bin/gh chain (bounded by
`timeout`) and FAIL that target (never hang) if it does not return. So a future
push can NEVER ship a hanging `gh` (the #1040/#1051 exec-loop).

RED-first + review-1 CRITICAL lock: the post-check must resolve the SAME gh a
real consumer uses (~/.local/bin/gh), NOT a bare `gh` off the non-login ssh
PATH (which lacks ~/.local/bin) — otherwise it false-PASSES a box still looping
at ~/.local/bin/gh (a system gh answers instead) and false-FAILS a healthy box
whose gh is only at ~/.local/bin.
"""
import inspect
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_remote  # noqa: E402


def _write_gh(dirpath, body):
    os.makedirs(dirpath, exist_ok=True)
    p = os.path.join(dirpath, "gh")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(p, 0o755)
    return p


_HANG = "#!/usr/bin/env bash\nsleep 3600\n"
_FAST = "#!/usr/bin/env bash\necho 'gh version 2.40.0'\nexit 0\n"


def _run_fragment(home, path):
    """Run the post-check fragment in a bash subprocess with HOME/PATH set — the
    fragment forces ~/.local/bin to the FRONT of PATH, so this faithfully models
    the non-login ssh shell resolution the real deploy uses."""
    frag = cli_remote._gh_chain_postcheck()
    env = {**os.environ, "HOME": home, "PATH": path}
    return subprocess.run(["bash", "-c", frag], capture_output=True, text=True,
                          timeout=30, env=env)


class TestGhChainPostCheck(unittest.TestCase):
    def test_fragment_resolves_local_bin_and_bounds_gh_version(self):
        frag = cli_remote._gh_chain_postcheck()
        # it forces ~/.local/bin to the front (review-1 CRITICAL) ...
        self.assertIn('PATH="$HOME/.local/bin:$PATH"', frag)
        # ... and bounds `gh --version` with `timeout` ...
        self.assertRegex(frag, r"timeout\b.*\bgh\s+--version")
        # ... with a DISTINCT non-zero exit so a hang is not confused with a
        # git-pull failure.
        self.assertIn("exit 87", frag)

    def test_fails_a_hanging_local_bin_gh_without_hanging(self):
        # ~/.local/bin/gh hangs -> the post-check must FAIL (never hang) well
        # within the ssh deploy timeout.
        home = tempfile.mkdtemp()
        _write_gh(os.path.join(home, ".local", "bin"), _HANG)
        start = time.monotonic()
        r = _run_fragment(home, os.environ.get("PATH", ""))
        elapsed = time.monotonic() - start
        self.assertNotEqual(r.returncode, 0, "a hanging gh must FAIL the target")
        self.assertLess(elapsed, 15, "the post-check must never hang")
        self.assertIn("POSTCHECK FAILED", r.stderr)

    def test_resolves_local_bin_gh_not_a_system_gh_on_path(self):
        # review-1 CRITICAL lock (false-PASS): a box looping at ~/.local/bin/gh
        # while a healthy system gh sits elsewhere on PATH must STILL FAIL — the
        # probe must resolve ~/.local/bin/gh (the looping one), not the system gh.
        home = tempfile.mkdtemp()
        _write_gh(os.path.join(home, ".local", "bin"), _HANG)   # loops
        sysdir = tempfile.mkdtemp()
        _write_gh(sysdir, _FAST)                                 # healthy system gh
        r = _run_fragment(home, sysdir + os.pathsep + os.environ.get("PATH", ""))
        self.assertNotEqual(r.returncode, 0,
                            "must resolve the LOOPING ~/.local/bin/gh, not the "
                            "system gh -> a false PASS is the review-1 bug")

    def test_passes_a_healthy_local_bin_gh(self):
        # review-1 CRITICAL lock (false-FAILURE): a healthy ~/.local/bin/gh must
        # PASS even though a bare `gh` off the non-login PATH would not find it.
        home = tempfile.mkdtemp()
        _write_gh(os.path.join(home, ".local", "bin"), _FAST)
        r = _run_fragment(home, os.environ.get("PATH", ""))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gh_less_target_is_not_failed(self):
        # review-2 (finding #2): a target that legitimately has NO gh (install
        # returned "skip: no gh on this box") must PASS — nothing to verify — not
        # be false-failed. gh resolves nowhere -> the probe is skipped (exit 0).
        import shutil as _sh
        home = tempfile.mkdtemp()                       # no ~/.local/bin/gh
        minbin = tempfile.mkdtemp()                     # bash+timeout, but NO gh
        for tool in ("bash", "timeout"):
            src = _sh.which(tool)
            if src:
                os.symlink(src, os.path.join(minbin, tool))
        r = _run_fragment(home, minbin)
        self.assertEqual(r.returncode, 0,
                         "a gh-less target must not be failed by the post-check")

    def test_deploy_loop_appends_the_postcheck_to_the_remote_command(self):
        # source-lock: the deploy loop's per-target remote command must run the
        # post-check after `airuleset.py install`, so an ssh rc!=0 (the existing
        # `failed.append` accounting) catches a hanging gh on any target.
        src = inspect.getsource(cli_remote._deploy_to_all_remotes)
        self.assertIn("_gh_chain_postcheck(", src)
        self.assertIn("airuleset.py install", src)


if __name__ == "__main__":
    unittest.main()
