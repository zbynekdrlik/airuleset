"""Install-time runtime-dependency check — per-box gaps must be LOUD.

subdev 2026-07-23: the box was provisioned without `jq`, so every notify/stop
hook silently no-oped — david's ❓ never pinged Discord, never entered the
question map, and the statusline 'otazky' badge stayed empty while the
question sat on screen. Git-deploy can't see per-machine binaries; the
install output can — warning-only, never fatal.
"""

import sys
import unittest
import unittest.mock as m
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


class RuntimeDepsCheck(unittest.TestCase):
    def test_jq_is_a_tracked_dependency(self):
        # the incident dep — hooks parse their stdin payload with jq
        for d in ("jq", "curl", "git", "gh", "tmux"):
            self.assertIn(d, airuleset.RUNTIME_DEPS, d)

    def test_missing_dep_auto_installs_and_verifies(self):
        # user directive 2026-07-24 ('ak ti nieco chyba mas to doinstalovat'):
        # a missing dep is INSTALLED by the check itself (sudo -n apt-get),
        # then re-verified — sync/push thus self-heals every target that has
        # sudo instead of only warning about the gap.
        installed = set()

        def which(d):
            if d == "jq" and "jq" not in installed:
                return None
            return "/usr/bin/" + d

        def run(argv, **kw):
            self.assertEqual(argv[:3], ["sudo", "-n", "apt-get"])
            self.assertIn("jq", argv)
            installed.add("jq")
            return m.Mock(returncode=0)

        with m.patch("shutil.which", side_effect=which), \
                m.patch("subprocess.run", side_effect=run):
            out = StringIO()
            with m.patch("sys.stdout", out):
                missing = airuleset.check_runtime_deps()
        self.assertEqual(missing, [])
        self.assertIn("auto-install", out.getvalue())
        self.assertNotIn("MISSING RUNTIME DEP", out.getvalue())

    def test_missing_dep_prints_loud_warning_when_install_fails(self):
        # no-sudo box (david/marek/montalu): the sudo -n attempt fails →
        # the LOUD warning stays (the gap must be visible in push output).
        with m.patch("shutil.which",
                     side_effect=lambda d: None if d == "jq" else "/usr/bin/" + d), \
                m.patch("subprocess.run", return_value=m.Mock(returncode=1)):
            out = StringIO()
            with m.patch("sys.stdout", out):
                missing = airuleset.check_runtime_deps()
        self.assertEqual(missing, ["jq"])
        self.assertIn("MISSING RUNTIME DEP", out.getvalue())
        self.assertIn("jq", out.getvalue())

    def test_missing_btop_prints_loud_warning_when_install_fails(self):
        # A sudo-less box (marek/david/montalu/simap on subdev) cannot
        # apt-get install — the gap must be reported LOUDLY, never silently
        # skipped, exactly like every other tracked dependency.
        with m.patch("shutil.which",
                     side_effect=lambda d: None if d == "btop" else "/usr/bin/" + d), \
                m.patch("subprocess.run", return_value=m.Mock(returncode=1)):
            out = StringIO()
            with m.patch("sys.stdout", out):
                missing = airuleset.check_runtime_deps()
        self.assertEqual(missing, ["btop"])
        self.assertIn("MISSING RUNTIME DEP", out.getvalue())
        self.assertIn("btop", out.getvalue())

    def test_all_present_is_quiet(self):
        with m.patch("shutil.which", side_effect=lambda d: "/usr/bin/" + d):
            out = StringIO()
            with m.patch("sys.stdout", out):
                missing = airuleset.check_runtime_deps()
        self.assertEqual(missing, [])
        self.assertEqual(out.getvalue(), "")

    def test_install_runs_the_check(self):
        src = Path(airuleset.__file__).read_text()
        i = src.index("def cmd_install")
        self.assertIn("check_runtime_deps()", src[i:i + 600])

    def test_sshpass_is_a_tracked_dependency(self):
        # #98: sshpass is used by airuleset's own ssh helpers (_burn_remote_cmd
        # and its sibling) but was never added to RUNTIME_DEPS — so install/push
        # never provisioned or verified it on any target.
        self.assertIn("sshpass", airuleset.RUNTIME_DEPS)

    def test_btop_is_a_tracked_dependency(self):
        # user directive 2026-07-28 ("chcem aby airuleset sa staral aby vsade
        # na targetoch bola au utilita btop") — btop was never added to
        # RUNTIME_DEPS, so install/push never provisioned or verified it, and
        # a box missing it degraded with no signal at all.
        self.assertIn("btop", airuleset.RUNTIME_DEPS)

    def test_node_and_npx_are_tracked_dependencies(self):
        # #158: the managed Playwright MCP server needs a real node/npx
        # runtime — RUNTIME_DEPS never checked for either.
        self.assertIn("node", airuleset.RUNTIME_DEPS)
        self.assertIn("npx", airuleset.RUNTIME_DEPS)

    def test_node_install_uses_the_nodejs_apt_package(self):
        # Debian/Ubuntu's real "node" apt package is an UNRELATED amateur
        # packet-radio program — installing it would never provide the
        # `node` binary. The real package (NodeSource's "nodejs", already
        # used fleet-wide) has to be named explicitly.
        seen_argv = []

        def run(argv, **kw):
            seen_argv.append(argv)
            return m.Mock(returncode=0)

        with m.patch("shutil.which",
                     side_effect=lambda d: None if d == "node" else "/usr/bin/" + d), \
                m.patch("subprocess.run", side_effect=run):
            airuleset.check_runtime_deps()
        node_calls = [a for a in seen_argv if "nodejs" in a or "node" in a]
        self.assertTrue(node_calls, seen_argv)
        for argv in node_calls:
            self.assertIn("nodejs", argv, argv)
            self.assertNotIn("node", [tok for tok in argv if tok != "nodejs"])

    def test_npx_install_also_uses_the_nodejs_apt_package(self):
        # npx has no apt package of its own — it ships bundled inside nodejs.
        seen_argv = []

        def run(argv, **kw):
            seen_argv.append(argv)
            return m.Mock(returncode=0)

        with m.patch("shutil.which",
                     side_effect=lambda d: None if d == "npx" else "/usr/bin/" + d), \
                m.patch("subprocess.run", side_effect=run):
            airuleset.check_runtime_deps()
        npx_calls = [a for a in seen_argv if "nodejs" in a]
        self.assertTrue(npx_calls, seen_argv)
        for argv in npx_calls:
            self.assertNotIn("npx", argv, argv)

    def test_a_returncode_0_install_with_the_binary_still_missing_is_reported_missing(self):
        # #158 review (mutation-proven gap): a NON-NodeSource "nodejs" build
        # can return rc=0 without actually providing `npx` — the re-verify
        # MUST check the real binary (`which(d)`), never just trust the
        # apt-get exit code or re-check the wrong name (`which(pkg)`).
        with m.patch("shutil.which",
                     side_effect=lambda d: None if d == "npx" else "/usr/bin/" + d), \
                m.patch("subprocess.run", return_value=m.Mock(returncode=0)):
            missing = airuleset.check_runtime_deps()
        self.assertIn("npx", missing)

    def test_missing_dep_warning_names_the_correct_apt_package(self):
        # #158 review (mutation-proven gap): the remediation text must name
        # the PACKAGE to install (nodejs), never the raw binary name (npx —
        # not a real apt package at all, a dead-end instruction).
        with m.patch("shutil.which",
                     side_effect=lambda d: None if d == "npx" else "/usr/bin/" + d), \
                m.patch("subprocess.run", return_value=m.Mock(returncode=1)):
            out = StringIO()
            with m.patch("sys.stdout", out):
                airuleset.check_runtime_deps()
        self.assertIn("apt-get install nodejs", out.getvalue())
        self.assertNotIn("apt-get install npx", out.getvalue())

    def test_node_and_npx_missing_together_share_one_install_attempt(self):
        # #158 review: node + npx both resolve to "nodejs" — a single missing
        # apt package must not be installed (and its failure warned about)
        # twice for one real gap.
        with m.patch("shutil.which",
                     side_effect=lambda d: None if d in ("node", "npx") else "/usr/bin/" + d), \
                m.patch("subprocess.run", return_value=m.Mock(returncode=0)) as run:
            airuleset.check_runtime_deps()
        self.assertEqual(run.call_count, 1, run.call_args_list)


class SudoLessToolRequestPath(unittest.TestCase):
    """#98: a sub-dev box (david/marek/montalu) has NO sudo, so
    autonomous-verification.md's 'install it yourself' instruction is
    unfollowable there. The module must give it a working alternative: file a
    gk-request naming the package, which lands in RUNTIME_DEPS on fulfilment."""

    MODULE = (airuleset.REPO_DIR / "modules" / "core" /
              "autonomous-verification.md")

    def test_sudo_less_branch_points_at_gk_request(self):
        text = self.MODULE.read_text()
        i = text.index("No sudo on a restricted box")
        chunk = text[i:i + 700]
        self.assertIn("gk-request", chunk)
        self.assertIn("RUNTIME_DEPS", chunk)


if __name__ == "__main__":
    unittest.main()
