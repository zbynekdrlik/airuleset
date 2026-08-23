"""ttyd static-binary installer for the DAVID webterm gateway (#614).

The subdev gateway account (david1) has NO sudo, so `ttyd` — which the
webterm-david ttyd unit's launcher `exec`s — is a user-space static binary in
`~/.local/bin`. #612 go-live installed it BY HAND; this ticket's PATH-self-
sufficiency half (already merged, d2d32bd0) made the unit resolve that binary,
but the gate `prerequisites_ready()` still REQUIRES ttyd to be present, so a
FRESH subdev re-provision with no ttyd would no-op the gate forever. Owner
decision (2026-08-23): auto-install `ttyd` exactly like `ensure_ffmpeg_static_
binary` — unpinned "latest" static binary, NO checksum.

`ensure_ttyd_static_binary()` is therefore the SAME "best-effort, idempotent,
non-fatal, no-op-when-present" shape as `ensure_ffmpeg_static_binary()`: ONE
subprocess call does download + chmod + atomic place into `~/.local/bin/ttyd`
(no privilege needed), so no test here needs a real network call — only the
constructed shell command and the subprocess's own returncode are asserted,
exactly like the ffmpeg installer tests it mirrors (tests/test_soniox_
provisioning.py). The one real end-to-end test stubs `curl` via a fake earlier
on PATH (no network) to prove the atomic-`mv` guarantee.
"""
import os
import sys
import tempfile
import unittest.mock as m
from io import StringIO
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_binary_installers  # noqa: E402  (the module the installer lives in)


def _fake_run(returncode=0, stdout="", stderr=""):
    return m.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestTtydAvailable(TestCase):
    def test_false_when_dest_missing_and_not_on_path(self):
        d = Path(tempfile.mkdtemp()) / "does-not-exist" / "ttyd"
        with m.patch("shutil.which", return_value=None):
            self.assertFalse(airuleset._ttyd_available(d))

    def test_true_when_dest_is_a_real_executable(self):
        # Recognizes the #612 HAND install (or a prior run here) as already-done
        # instead of re-downloading over it (mirrors ffmpeg's dest-first check).
        d = Path(tempfile.mkdtemp()) / "ttyd"
        d.write_text("#!/bin/sh\n")
        d.chmod(0o755)
        with m.patch("shutil.which", return_value=None):
            self.assertTrue(airuleset._ttyd_available(d))

    def test_true_via_system_path_when_our_own_dest_is_absent(self):
        # dev1's system /usr/bin/ttyd — available, so the installer no-ops there.
        d = Path(tempfile.mkdtemp()) / "does-not-exist"
        with m.patch("shutil.which", return_value="/usr/bin/ttyd"):
            self.assertTrue(airuleset._ttyd_available(d))

    def test_a_non_executable_destination_falls_back_to_path(self):
        d = Path(tempfile.mkdtemp()) / "ttyd"
        d.write_text("not executable, e.g. a truncated download")
        with m.patch("shutil.which", return_value="/usr/bin/ttyd"):
            self.assertTrue(airuleset._ttyd_available(d))

    def test_asks_which_for_the_ttyd_binary_name(self):
        seen = []

        def fake_which(name):
            seen.append(name)
            return None

        d = Path(tempfile.mkdtemp()) / "does-not-exist"
        with m.patch("shutil.which", side_effect=fake_which):
            airuleset._ttyd_available(d)
        self.assertEqual(seen, ["ttyd"])

    def test_uses_the_dot_local_bin_destination_by_default(self):
        # Never ~/bin — only ~/.local/bin is on PATH inside a real Bash tool
        # call / the systemd --user unit's own PATH env (#614). Same rule the
        # ffmpeg dest follows (#275 review MAJOR-2).
        self.assertEqual(airuleset.TTYD_STATIC_DEST.parts[-3:],
                         (".local", "bin", "ttyd"))


class TestTtydStaticUrl(TestCase):
    def test_url_targets_latest_unpinned_ttyd_x86_64(self):
        # Owner decision 2026-08-23 (Approach 2): unpinned "latest" static
        # binary, exactly the ffmpeg precedent — no version pin, no checksum.
        url = airuleset.TTYD_STATIC_URL
        self.assertIn("tsl0922/ttyd", url)
        self.assertIn("releases/latest/download", url)   # unpinned "latest"
        self.assertTrue(url.endswith("ttyd.x86_64"))     # the static x86_64 asset


class TestEnsureTtydStaticBinary(TestCase):
    def _dest(self):
        return Path(tempfile.mkdtemp()) / "local" / "bin" / "ttyd"

    def test_no_op_when_already_available(self):
        d = self._dest()
        with m.patch.object(cli_binary_installers, "_ttyd_available",
                            return_value=True), \
                m.patch("subprocess.run") as run:
            airuleset.ensure_ttyd_static_binary(d)
        run.assert_not_called()

    def test_installs_via_one_subprocess_call_when_missing(self):
        d = self._dest()
        calls = {"n": 0}

        def fake_available(dest=None):
            calls["n"] += 1
            return calls["n"] > 1   # missing on the check, present after install

        with m.patch.object(cli_binary_installers, "_ttyd_available",
                            side_effect=fake_available), \
                m.patch("subprocess.run", return_value=_fake_run()) as run:
            airuleset.ensure_ttyd_static_binary(d)
        run.assert_called_once()
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "bash")
        self.assertEqual(argv[1], "-c")
        script = argv[2]
        self.assertIn("set -o pipefail", script)
        self.assertIn(airuleset.TTYD_STATIC_URL, script)
        self.assertIn(str(d), script)
        # ttyd is a SINGLE binary, not a tarball — no tar extraction.
        self.assertNotIn("tar ", script)
        # Atomic install: download+chmod inside a SCRATCH dir under the
        # destination's OWN parent (same filesystem → the final `mv` is an
        # atomic rename), only `mv`d into place at the very end. A hard-killed
        # 180s subprocess must never leave a truncated-but-"executable" binary
        # at the final path (#275 review MAJOR-3, same guarantee for ttyd).
        self.assertIn("mktemp -d -p", script)
        self.assertIn('mv "$TMP/ttyd.new" %s' % d, script)
        # Never curl/cp DIRECTLY into the final destination path.
        self.assertNotIn('-o %s' % d, script)
        self.assertNotIn('-o "%s"' % d, script)

    def test_install_failure_is_loud_but_non_fatal(self):
        out = StringIO()
        d = self._dest()
        with m.patch.object(cli_binary_installers, "_ttyd_available",
                            return_value=False), \
                m.patch("subprocess.run",
                        return_value=_fake_run(returncode=1, stderr="boom")), \
                m.patch("sys.stderr", out):
            airuleset.ensure_ttyd_static_binary(d)   # must not raise
        self.assertIn("ttyd static install failed", out.getvalue())

    def test_install_exception_is_non_fatal(self):
        out = StringIO()
        d = self._dest()
        with m.patch.object(cli_binary_installers, "_ttyd_available",
                            return_value=False), \
                m.patch("subprocess.run", side_effect=FileNotFoundError("curl")), \
                m.patch("sys.stderr", out):
            airuleset.ensure_ttyd_static_binary(d)   # must not raise
        self.assertIn("ttyd static install skipped", out.getvalue())

    def test_a_real_download_writes_the_binary_atomically(self):
        # End-to-end (#275 review MAJOR-3, ttyd single-binary form): run the
        # REAL bash script this function constructs, with only `curl` stubbed
        # (a fake `curl` earlier on PATH that writes a fixture "binary" to the
        # `-o` target). Confirm it lands at the real final destination,
        # executable, with no scratch left behind.
        import subprocess

        d = self._dest()
        work = Path(tempfile.mkdtemp())
        fixture = work / "ttyd.fixture"
        fixture.write_text("#!/bin/sh\necho fake-ttyd\n")

        fake_bin = work / "fakebin"
        fake_bin.mkdir()
        curl_stub = fake_bin / "curl"
        # Parse the `-o <outfile>` this function's script passes and write the
        # fixture there (a static single-file download, no tar).
        curl_stub.write_text(
            "#!/bin/sh\n"
            "out=\"\"\n"
            "while [ $# -gt 0 ]; do\n"
            "  case \"$1\" in -o) shift; out=\"$1\";; esac\n"
            "  shift\n"
            "done\n"
            "cat %s > \"$out\"\n" % fixture)
        curl_stub.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = "%s:%s" % (fake_bin, env.get("PATH", ""))
        _real_subprocess_run = subprocess.run

        def real_run(argv, **kw):
            kw.setdefault("env", env)
            return _real_subprocess_run(argv, **kw)

        with m.patch.object(cli_binary_installers, "_ttyd_available",
                            side_effect=lambda dd=None: (dd or d).is_file()), \
                m.patch("subprocess.run", side_effect=real_run):
            airuleset.ensure_ttyd_static_binary(d)

        self.assertTrue(d.is_file() and os.access(d, os.X_OK))
        self.assertEqual(d.read_text(), "#!/bin/sh\necho fake-ttyd\n")
        leftovers = [c for c in d.parent.iterdir() if c.name != "ttyd"]
        self.assertEqual(leftovers, [],
                         "scratch dir must be cleaned up: %s" % leftovers)


if __name__ == "__main__":
    main()
