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


if __name__ == "__main__":
    unittest.main()
